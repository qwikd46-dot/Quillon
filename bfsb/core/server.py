"""
BFSB Local HTTP Server Backend

Serves HTML templates and handles search API.
Runs on localhost:8888 (or configurable port).
"""

from __future__ import annotations

import asyncio
import html
import json
import os
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse, unquote

from aiohttp import web
from aiohttp.web_request import Request
from aiohttp.web_response import Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PyQt6.QtCore import QMetaObject, QTimer, Qt, Q_ARG, QObject, pyqtSlot
from PyQt6.QtWidgets import QApplication

from bfsb.core.search.aggregator import AggregateResponse, Result, aggregate_search, shutdown as aggregator_shutdown


class _MainThreadInvoker(QObject):
    """Runs arbitrary callables on the Qt main thread.

    ``QTimer.singleShot(0, fn)`` from a foreign thread never fires — the
    internal timer would live on a thread with no event loop. Instead we
    keep an invoker QObject living on the main thread and hand it keys of
    pending callables via ``QMetaObject.invokeMethod`` with a queued
    connection; its slot runs them on the main thread.
    """

    def __init__(self) -> None:
        super().__init__()
        self._pending: dict[str, object] = {}

    @pyqtSlot(str)
    def _execute(self, key: str) -> None:
        fn = self._pending.pop(key, None)
        if callable(fn):
            fn()


# Deprecated: we no longer redirect to upstream. Results are aggregated
# server-side and rendered on local origin. Kept for reference only.
SEARCH_HANDBACK_URL = "https://duckduckgo.com/?q={q}"


class BFSHBServer:
    """Local HTTP server for BFSB browser."""

    def __init__(
        self,
        template_dir: Path,
        host: str = "127.0.0.1",
        port: int = 8889,
    ):
        self.template_dir = template_dir
        self.host = host
        self.port = port
        # Set by MainWindow after construction. The /test-action/*
        # route dispatches into this object when BFSB_TEST=1 is in
        # the environment. Optional[QObject] at runtime.
        self._main_window = None

        # Jinja2 environment for template rendering
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(['html', 'xml']),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # Created lazily on the Qt main thread by _run_on_qt (see class doc).
        self._invoker: Optional[_MainThreadInvoker] = None

        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    def _create_app(self) -> web.Application:
        """Create aiohttp application with routes."""
        app = web.Application()

        # Routes
        app.router.add_get('/', self.handle_home)
        app.router.add_get('/health', self.handle_health)
        app.router.add_get('/search', self.handle_search)
        app.router.add_get('/about', self.handle_about_page)
        app.router.add_get('/preferences', self.handle_preferences_page)
        
        # BFSB internal routes (replacing bfsb:// scheme)
        app.router.add_get('/_bfsb/newTab', self.handle_new_tab)
        app.router.add_get('/_bfsb/switchTab', self.handle_switch_tab)
        app.router.add_get('/_bfsb/closeTab', self.handle_close_tab)
        app.router.add_get('/_bfsb/goBack', self.handle_go_back)
        app.router.add_get('/_bfsb/goForward', self.handle_go_forward)
        app.router.add_get('/_bfsb/reload', self.handle_reload)
        app.router.add_get('/_bfsb/about', self.handle_about_page)

        # JSON feeds for the in-page bookmarks/history views (best-effort).
        app.router.add_get('/api/bookmarks', self.handle_api_bookmarks)
        app.router.add_get('/api/history', self.handle_api_history)
        # Mutations as POST with JSON answers (reliable; no scheme-dispatch).
        app.router.add_post('/api/bookmark/toggle', self.handle_api_bookmark_toggle)
        app.router.add_post('/api/bookmark/add', self.handle_api_bookmark_add)
        app.router.add_post('/api/bookmark/remove', self.handle_api_bookmark_remove)
        app.router.add_post('/api/history/add', self.handle_api_history_add)
        app.router.add_post('/api/history/remove', self.handle_api_history_remove)
        app.router.add_post('/api/history/clear', self.handle_api_history_clear)
        app.router.add_post('/api/ui/newTab', self.handle_api_new_tab)
        # Adblocker state shared with the proxy/mitmdump layer (adblock_state.py).
        app.router.add_get('/api/adblock', self.handle_api_adblock_get)
        app.router.add_post('/api/adblock', self.handle_api_adblock_set)

        # Test fixtures used by the hermes harness. Serves tiny static
        # files (2KB text, 1KB PDF) so the DOWNLOADS check can verify
        # that a real downloadRequested signal lands in ~/Downloads.
        app.router.add_get('/test-files/{filename}', self.handle_test_file)

        # Test action dispatch — only enabled when BFSB_TEST=1 is in the
        # environment. Lets the hermes harness drive Qt-side actions
        # (new tab, close tab, toggle sidebar, open menu) over plain
        # HTTP, sidestepping the unreliable ydotool-to-Qt path under
        # Xvfb. Each action returns a small JSON describing the result.
        if os.environ.get("BFSB_TEST") == "1":
            app.router.add_get('/test-action/{name}', self.handle_test_action)
            # /test-state returns a JSON snapshot of the Qt-side UI
            # state (which popovers are open, how many bookmarks /
            # history entries the stores hold, etc). The harness
            # uses this for assertions instead of screenshot diffs
            # (which fail under bare Xvfb without a window manager
            # because Qt's popup windows don't get composited).
            app.router.add_get('/test-state', self.handle_test_state)

        app.router.add_static('/static', path=str(self.template_dir / 'static'), name='static')

        # CORS for local development
        app.middlewares.append(self._cors_middleware)

        return app

    @staticmethod
    @web.middleware
    async def _cors_middleware(request: Request, handler):
        """aiohttp new-style middleware (requires the @web.middleware
        decorator — without it aiohttp 3.x treats this as a legacy
        (app, handler) factory and the app fails to serve requests)."""
        response: Response = await handler(request)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response

    async def handle_home(self, request: Request) -> Response:
        """Serve home page - rendered through Jinja2 with PAGE_TYPE='home'."""
        template = self.jinja_env.get_template('bfsb_combined.html')
        html = template.render(
            PAGE_TYPE='home',
            HOME_BOOKMARKS_HTML=self._home_bookmarks_html(),
            BOOKMARKS_JSON=self._bookmarks_json(),
        )
        return web.Response(text=html, content_type='text/html')

    @staticmethod
    def _saved_bookmark_dicts(limit: int = 50) -> list[dict]:
        """Read saved bookmarks as plain dicts. Never raises — [] on failure."""
        try:
            from bfsb.core.storage.bookmarks import BookmarkStore

            marks = BookmarkStore().list_all(limit=limit) or []
            out = []
            for m in marks:
                title = (m.title or m.url or '').strip() or m.url
                out.append({
                    'name': title,
                    'url': m.url,
                    'fav': (title.strip()[:1] or '?').upper(),
                })
            return out
        except Exception:
            return []

    @classmethod
    def _home_bookmarks_html(cls, limit: int = 8) -> str:
        """Build the home-page bookmark cards from the saved bookmarks store.

        Best-effort only: any failure yields an empty string and the
        template simply omits the bookmarks section.
        """
        try:
            import html as _html

            cards = []
            for b in cls._saved_bookmark_dicts(limit=limit):
                cards.append(
                    '<a class="bookmark" href="{url}"><span class="favicon">{letter}</span>'
                    '<span>{title}</span></a>'.format(
                        url=_html.escape(b['url'], quote=True),
                        letter=_html.escape(b['fav']),
                        title=_html.escape(b['name']),
                    )
                )
            return ''.join(cards)
        except Exception:
            return ''

    @classmethod
    def _bookmarks_json(cls, limit: int = 50) -> str:
        """Saved bookmarks as a JSON array for the in-page bookmarks view.

        ``<`` is escaped so a hostile bookmark title cannot break out of
        the ``<script>`` block the JSON is inlined into.
        """
        try:
            return json.dumps(cls._saved_bookmark_dicts(limit=limit)).replace('<', '\\u003c')
        except Exception:
            return '[]'

    async def handle_api_bookmarks(self, request: Request) -> Response:
        """JSON feed of saved bookmarks for the in-page bookmarks view."""
        try:
            return web.json_response(self._saved_bookmark_dicts(limit=200))
        except Exception:
            return web.json_response([])

    def _run_on_qt(self, fn, timeout: float = 5.0):
        """Run ``fn(main_window_or_None)`` on the Qt main thread and wait.

        Returns ``fn(None)`` when no Qt GUI is present. Raises on timeout
        or when ``fn`` raised — callers must NOT fall back to running
        ``fn`` on the calling (aiohttp) thread: touching QWidgets from a
        non-GUI thread kills the process with SIGTRAP.
        """
        import threading
        import uuid

        from PyQt6.QtCore import Qt as _Qt
        from bfsb.ui.main_window import BFSBWindow

        app = QApplication.instance()
        if app is None:
            return fn(None)
        target = None
        for w in app.topLevelWidgets():
            if isinstance(w, BFSBWindow):
                target = w
                break
        if target is None:
            return fn(None)

        box, err, done = [], [], threading.Event()

        def _job() -> None:
            try:
                box.append(fn(target))
            except BaseException as e:  # noqa: BLE001
                err.append(e)
            finally:
                done.set()

        if self._invoker is None:
            invoker = _MainThreadInvoker()
            invoker.moveToThread(target.thread())
            self._invoker = invoker
        key = uuid.uuid4().hex
        self._invoker._pending[key] = _job
        QMetaObject.invokeMethod(
            self._invoker, "_execute", _Qt.ConnectionType.QueuedConnection, Q_ARG(str, key)
        )
        if not done.wait(timeout=timeout):
            raise TimeoutError("qt hop timed out")
        if err:
            raise err[0]
        return box[0] if box else None

    def _refresh_panels(self, main, which: str) -> None:
        """Refresh native side-panel lists. Main-thread only; guarded."""
        try:
            if main is None:
                return
            panel = getattr(main, "_side_panel", None)
            if panel is None:
                return
            if which == "bookmarks":
                panel.refresh_bookmarks()
            elif which == "history":
                panel.refresh_history()
        except Exception:
            pass

    async def handle_api_bookmark_toggle(self, request: Request) -> Response:
        url = (request.query.get("url") or "").strip()
        title = (request.query.get("title") or "").strip() or url
        if not url:
            return web.json_response({"ok": False})

        def _do(main):
            from bfsb.core.storage.bookmarks import BookmarkStore

            store = main._bookmarks if main is not None and hasattr(main, "_bookmarks") else BookmarkStore()
            marked = False
            if store.get(url):
                store.remove(url)
            else:
                store.add(url=url, title=title)
                marked = True
            self._refresh_panels(main, "bookmarks")
            if main is not None:
                try:
                    if main._chrome is not None:
                        main._chrome.set_bookmarked(marked)
                except Exception:
                    pass
            return marked

        try:
            marked = self._run_on_qt(_do)
            return web.json_response({"ok": True, "bookmarked": bool(marked)})
        except Exception:
            return web.json_response({"ok": False})

    async def handle_api_bookmark_add(self, request: Request) -> Response:
        url = (request.query.get("url") or "").strip()
        title = (request.query.get("title") or "").strip() or url
        if not url:
            return web.json_response({"ok": False})

        def _do(main):
            from bfsb.core.storage.bookmarks import BookmarkStore

            store = main._bookmarks if main is not None and hasattr(main, "_bookmarks") else BookmarkStore()
            store.add(url=url, title=title)
            self._refresh_panels(main, "bookmarks")
            return True

        try:
            self._run_on_qt(_do)
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False})

    async def handle_api_bookmark_remove(self, request: Request) -> Response:
        url = (request.query.get("url") or "").strip()
        if not url:
            return web.json_response({"ok": False})

        def _do(main):
            from bfsb.core.storage.bookmarks import BookmarkStore

            store = main._bookmarks if main is not None and hasattr(main, "_bookmarks") else BookmarkStore()
            store.remove(url)
            self._refresh_panels(main, "bookmarks")
            return True

        try:
            self._run_on_qt(_do)
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False})

    async def handle_api_history_add(self, request: Request) -> Response:
        url = (request.query.get("url") or "").strip()
        title = (request.query.get("title") or "").strip()
        if not url:
            return web.json_response({"ok": False, "id": None})

        def _do(main):
            from bfsb.core.storage.history import HistoryStore

            store = main._history if main is not None and hasattr(main, "_history") else HistoryStore()
            new_id = store.record(url, title)
            self._refresh_panels(main, "history")
            return new_id

        try:
            new_id = self._run_on_qt(_do)
            return web.json_response({"ok": new_id is not None, "id": new_id})
        except Exception:
            return web.json_response({"ok": False, "id": None})

    async def handle_api_history_remove(self, request: Request) -> Response:
        try:
            entry_id = int(request.query.get("id", ""))
        except (TypeError, ValueError):
            return web.json_response({"ok": False})

        def _do(main):
            from bfsb.core.storage.history import HistoryStore

            store = main._history if main is not None and hasattr(main, "_history") else HistoryStore()
            store.remove(entry_id)
            self._refresh_panels(main, "history")
            return True

        try:
            self._run_on_qt(_do)
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False})

    async def handle_api_history_clear(self, request: Request) -> Response:
        def _do(main):
            from bfsb.core.storage.history import HistoryStore

            store = main._history if main is not None and hasattr(main, "_history") else HistoryStore()
            store.clear()
            self._refresh_panels(main, "history")
            return True

        try:
            self._run_on_qt(_do)
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False})

    async def handle_api_new_tab(self, request: Request) -> Response:
        def _do(main):
            if main is None:
                return False
            main.new_tab()
            return True

        try:
            ok = self._run_on_qt(_do)
            return web.json_response({"ok": bool(ok)})
        except Exception:
            return web.json_response({"ok": False})

    async def handle_api_adblock_get(self, request: Request) -> Response:
        """Current adblocker on/off state (shared with the mitmdump layer)."""
        try:
            from bfsb.core.adblock_state import is_adblock_enabled

            return web.json_response({"ok": True, "enabled": bool(is_adblock_enabled())})
        except Exception:
            return web.json_response({"ok": False, "enabled": True})

    async def handle_api_adblock_set(self, request: Request) -> Response:
        """Set the adblocker on/off state (?enabled=1|0)."""
        enabled = (request.query.get("enabled") or "").strip() not in ("0", "false", "off")
        try:
            from bfsb.core.adblock_state import set_adblock_enabled

            set_adblock_enabled(enabled)
            return web.json_response({"ok": True, "enabled": enabled})
        except Exception:
            return web.json_response({"ok": False, "enabled": enabled})

    async def handle_api_history(self, request: Request) -> Response:
        """JSON feed of recent history for the in-page history view."""
        try:
            from bfsb.core.storage.history import HistoryStore

            items = HistoryStore().list_recent(limit=200) or []
            return web.json_response([
                {'id': e.id, 'url': e.url, 'title': e.title, 'time': e.last_visit}
                for e in items
            ])
        except Exception:
            return web.json_response([])

    async def handle_search(self, request: Request) -> Response:
        """Render a BFSB-search results page in our own origin.

        URL bar stays at ``127.0.0.1:8889/search?q=…``. We never
        redirect to the upstream search engine; instead we
        server-side fetch results through ``aggregate_search`` (which
        runs outbound HTTP from BFSB's helper-thread process, not
        from the embedded chromium subprocess), and render them in
        ``bfsb_combined.html``.

        That means:

        * The browser's network inspector sees only ``127.0.0.1`` —
          no DDG URL ever appears in DevTools.
        * Pages keep our URL on screen.
        * The user-visible chrome is BFSB; we don't inject DDG
          markup or asset URLs anywhere into the rendered HTML.
        """
        query = (request.query.get("q") or "").strip()
        if not query:
            raise web.HTTPFound("/")

        # PERF-DEBUG(phase1): measure query intake stages — remove after phase 1.
        import time as _ptime
        _t_submit = _ptime.perf_counter()  # gated by BFSB_PERF=1

        # Log the search itself so History reflects what was asked,
        # not just the pages later visited. Best-effort, OFF the event
        # loop: the sync SQLite write measured 26-63ms of loop stall per
        # query (PERF_NOTES.md A), delaying every concurrent request.
        try:
            from urllib.parse import quote as _quote

            from bfsb.core.storage.history import HistoryStore

            await asyncio.to_thread(
                HistoryStore().record,
                f"http://127.0.0.1:8889/search?q={_quote(query)}",
                query,
            )
        except Exception:
            pass
        _t_history = _ptime.perf_counter()  # PERF-DEBUG(phase1)

        agg: Optional[AggregateResponse] = None
        try:
            agg = await search_async(query)
        except Exception as e:
            print(f"[search] failure: {type(e).__name__}: {e}", flush=True)
        _t_agg = _ptime.perf_counter()  # PERF-DEBUG(phase1)

        shown = list(agg.results[:12]) if agg and agg.results else []
        results_html = (
            self._format_results_html(shown)
            if shown
            else make_empty_results()
        )
        infobox_html = self._format_infobox_html(agg)
        count_text = (
            f"About {len(shown)} results"
            if shown
            else "No results to display"
        )

        template = self.jinja_env.get_template("bfsb_combined.html")
        html = template.render(
            PAGE_TYPE="results",
            QUERY=query,
            RESULTS_COUNT=count_text,
            RESULTS_HTML=results_html,
            INFOBOX_HTML=infobox_html,
            BOOKMARKS_JSON=self._bookmarks_json(),
        )
        # PERF-DEBUG(phase1): query intake stage table — remove after phase 1.
        _t_render = _ptime.perf_counter()
        if os.environ.get("BFSB_PERF") == "1":
            print(
                f"[PERF] search q={query!r} submit->history={(_t_history-_t_submit)*1000:.1f}ms "
            f"history->agg={(_t_agg-_t_history)*1000:.1f}ms "
            f"agg->render={(_t_render-_t_agg)*1000:.1f}ms "
            f"server_total={(_t_render-_t_submit)*1000:.1f}ms results={len(shown)}",
            flush=True,
        )
        return web.Response(text=html, content_type="text/html")

    def _format_results_html(self, results: list[Result]) -> str:
        """Format search results as HTML.

        Every result gets a real site favicon (Google s2 service) layered
        over the letter fallback, a dimmed domain line, a clean title
        link, snippet and a small source tag. All styling lives in the
        template's ``.result-*`` / ``.source-tag`` CSS.
        """
        if not results:
            return '<div style="text-align:center;color:#a0a0b0;padding:48px;"><div style="font-size:48px;margin-bottom:16px;">🔍</div><div style="font-size:18px;margin-bottom:8px;">No results</div></div>'

        parts = []
        for r in results:
            domain = r.url
            try:
                domain = urlparse(r.url).netloc.replace('www.', '')
            except Exception:
                pass
            title = r.title or ''
            snippet = r.snippet or ''
            source = r.source or ''
            safe_domain = html.escape(domain, quote=True)
            safe_url = html.escape(r.url or '', quote=True)
            safe_title = html.escape(title)
            safe_snippet = html.escape(snippet)
            safe_source = html.escape(source)
            fav = html.escape(((domain or title or '?').strip()[:1] or '?').upper())
            favicon_img = (
                f'<img src="https://www.google.com/s2/favicons?domain={safe_domain}&sz=32" '
                'alt="" loading="lazy" onerror="this.remove()">'
            )

            sources_html = f'<span class="source-tag">{safe_source}</span>'

            parts.append(f'''
                <div class="result-item">
                    <div class="result-url">
                        <span class="result-favicon">{fav}{favicon_img}</span>
                        <span class="result-domain">{safe_domain}</span>
                    </div>
                    <a class="result-title" href="{safe_url}">{safe_title}</a>
                    <div class="result-desc">{safe_snippet}</div>
                    <div class="result-sources">{sources_html}</div>
                </div>
            ''')
        return ''.join(parts)

    def _format_infobox_html(self, agg: Optional[AggregateResponse]) -> str:
        """Format infobox HTML — every query gets an icon-headed card."""
        domain, first_url, first_title, first_snippet = '', '', '', ''
        if agg and agg.results:
            first = agg.results[0]
            try:
                domain = urlparse(first.url).netloc.replace('www.', '')
            except Exception:
                pass
            first_url = first.url or ''
            first_title = first.title or ''
            first_snippet = first.snippet or ''

        def _head(title: str, sub: str) -> str:
            safe_title = html.escape(title or '')
            safe_sub = html.escape(sub or '')
            fav = html.escape(((title or domain or '?').strip()[:1] or '?').upper())
            # Real site logo (Google s2 favicon service) on a white chip so
            # dark-background logos (YouTube play button) stay visible; the
            # letter remains as fallback if the image fails to load.
            logo_img = (
                f'<img src="https://www.google.com/s2/favicons?domain={safe_sub}&sz=64" '
                'alt="" onerror="this.remove()">'
            )
            return (
                '<div class="infobox-head">'
                f'<span class="infobox-fav">{fav}{logo_img}</span>'
                '<div>'
                f'<div class="infobox-title">{safe_title}</div>'
                f'<div class="infobox-sub">{safe_sub}</div>'
                '</div>'
                '</div>'
            )

        def _links(pairs) -> str:
            if not pairs:
                return ''
            return '<div class="infobox-links">' + ''.join(
                f'<a href="{html.escape(url or "", quote=True)}">{html.escape(label or "")}</a>'
                for label, url in pairs
            ) + '</div>'

        if agg and agg.abstract:
            title = agg.abstract_source or "Info"
            sub = domain
            try:
                if agg.abstract_url:
                    sub = urlparse(agg.abstract_url).netloc.replace('www.', '')
            except Exception:
                pass
            links = (
                [("Read more \u2192", agg.abstract_url)]
                if agg.abstract_url
                else ([("Visit site", first_url)] if first_url else [])
            )
            return (
                '<div class="infobox">'
                + _head(title, sub)
                + f'<div class="infobox-desc">{html.escape(agg.abstract or "")}</div>'
                + _links(links)
                + '</div>'
            )

        # Fallback: known entities from first result (kept from old logic)
        if agg and agg.results:
            entity_data = {
                'youtube.com': {
                    'title': 'YouTube',
                    'rows': [
                        ('Founded', 'February 14, 2005'),
                        ('Country', 'United States'),
                        ('HQ', 'San Bruno, CA'),
                        ('Founders', 'Steve Chen, Chad Hurley, Jawed Karim'),
                    ],
                    'links': [
                        ('Wikipedia', 'https://en.wikipedia.org/wiki/YouTube'),
                        ('YouTube', 'https://www.youtube.com'),
                        ('Twitter', 'https://twitter.com/YouTube'),
                    ],
                },
                'github.com': {
                    'title': 'GitHub',
                    'rows': [
                        ('Founded', 'April 10, 2008'),
                        ('Country', 'United States'),
                        ('HQ', 'San Francisco, CA'),
                        ('CEO', 'Thomas Dohmke'),
                    ],
                    'links': [
                        ('Wikipedia', 'https://en.wikipedia.org/wiki/GitHub'),
                        ('GitHub', 'https://github.com'),
                        ('Twitter', 'https://twitter.com/github'),
                    ],
                },
                'wikipedia.org': {
                    'title': 'Wikipedia',
                    'rows': [
                        ('Founded', 'January 15, 2001'),
                        ('Country', 'United States'),
                        ('HQ', 'San Francisco, CA'),
                        ('Founder', 'Jimmy Wales, Larry Sanger'),
                    ],
                    'links': [
                        ('Wikipedia', 'https://en.wikipedia.org/wiki/Wikipedia'),
                        ('Wikimedia', 'https://wikimedia.org'),
                    ],
                },
            }

            entity = entity_data.get(domain.lower())
            if entity:
                rows_html = ''.join(
                    f'<div class="infobox-row"><span class="infobox-key">{html.escape(k)}</span><span class="infobox-val">{html.escape(v)}</span></div>'
                    for k, v in entity['rows']
                )
                return (
                    '<div class="infobox">'
                    + _head(entity['title'], domain)
                    + rows_html
                    + _links(entity.get('links', []))
                    + '</div>'
                )

            # Generic card from the top result so every query gets an
            # About panel with an icon, a name, and a description.
            return (
                '<div class="infobox">'
                + _head(first_title or domain, domain)
                + f'<div class="infobox-desc">{html.escape(first_snippet)}</div>'
                + _links([("Visit site", first_url)] if first_url else [])
                + '</div>'
            )

        return '<div class="infobox"><div class="infobox-title">Info</div><div style="color:#55556a;">No additional info available</div></div>'


    async def handle_about_page(self, request: Request) -> Response:
        """Serve about page."""
        return web.Response(text='<html><body>BFSB Browser - About</body></html>', content_type='text/html')

    async def handle_preferences_page(self, request: Request) -> Response:
        """Serve preferences page."""
        template = self.jinja_env.get_template('bfsb_combined.html')
        html = template.render(PAGE_TYPE='preferences')
        return web.Response(text=html, content_type='text/html')

    async def handle_new_tab(self, request: Request) -> Response:
        """Handle new tab request."""
        # This will be handled by the window via JS bridge
        # Return a redirect to home
        return web.Response(text='OK', content_type='text/plain')

    async def handle_switch_tab(self, request: Request) -> Response:
        """Handle tab switch request."""
        return web.Response(text='OK', content_type='text/plain')

    async def handle_close_tab(self, request: Request) -> Response:
        """Handle close tab request."""
        return web.Response(text='OK', content_type='text/plain')

    async def handle_go_back(self, request: Request) -> Response:
        """Handle go back request."""
        return web.Response(text='OK', content_type='text/plain')

    async def handle_go_forward(self, request: Request) -> Response:
        """Handle go forward request."""
        return web.Response(text='OK', content_type='text/plain')

    async def handle_reload(self, request: Request) -> Response:
        """Handle reload request."""
        return web.Response(text='OK', content_type='text/plain')

    async def handle_about_page(self, request: Request) -> Response:
        """Serve about page."""
        template = self.jinja_env.get_template('bfsb_combined.html')
        html = template.render(PAGE_TYPE='about')
        return web.Response(text=html, content_type='text/html')

    async def handle_health(self, request: Request) -> Response:
        """Health check endpoint for server readiness."""
        return web.Response(text='OK', content_type='text/plain', status=200)

    # Test fixtures: small static payloads the hermes harness downloads
    # through the embedded QWebEngineProfile to exercise the
    # downloadRequested → DownloadManager path. Each is keyed on filename
    # so a future PDF or image check just adds a branch here.
    _TEST_FILE_PAYLOADS: dict[str, tuple[bytes, str]] = {
        "sample.txt": (b"Hello BFSB test file\n" * 100, "text/plain"),
        "sample.pdf": (b"%PDF-1.4\n%fake test pdf for hermes download check\n" * 20, "application/pdf"),
    }

    async def handle_test_file(self, request: Request) -> Response:
        """Serve a small test file for the hermes downloads check.

        Returns 404 for any filename we don't explicitly list, so the
        route can't be abused as a generic file-read primitive.
        """
        filename = request.match_info.get("filename", "")
        payload = self._TEST_FILE_PAYLOADS.get(filename)
        if payload is None:
            raise web.HTTPNotFound(reason=f"unknown test file: {filename}")
        body, content_type = payload
        return web.Response(
            body=body,
            content_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(body)),
            },
        )

    # Whitelist of allowed test actions. The handler below will only
    # dispatch a name that appears in this dict. This prevents the
    # test route from being used as an arbitrary method-invocation
    # primitive on the main window.
    _TEST_ACTIONS: dict[str, str] = {
        "newTab": "new_tab",
        "closeTab": "close_tab",
        "showMainMenu": "_show_main_menu",
    }

    async def handle_test_action(self, request: Request) -> Response:
        """Dispatch a test action to the MainWindow.

        Only enabled when BFSB_TEST=1 was in the environment when the
        server was constructed. Each action is a method name on the
        MainWindow; we resolve it from the whitelist above and call
        it. Optional ?arg=... query parameter is passed as a string
        to the method (e.g. ``/test-action/closeTab?arg=0``).

        IMPORTANT: aiohttp runs handlers on its own event loop, not
        the Qt main thread. QWidget constructors and most
        ``MainWindow`` methods must run on the main thread, otherwise
        Qt fires SIGTRAP (the trap in the bfsb log) the moment we
        touch a QWebEngineView. We hop to the main thread via
        ``QTimer.singleShot(0, ...)`` and block the calling thread on
        a ``threading.Event`` until the main thread runs the closure.
        """
        name = request.match_info.get("name", "")
        method_name = self._TEST_ACTIONS.get(name)
        if method_name is None:
            raise web.HTTPNotFound(reason=f"unknown test action: {name!r}")
        main = self._main_window
        if main is None:
            raise web.HTTPServiceUnavailable(
                reason="main window not registered with the server",
            )
        method = getattr(main, method_name, None)
        if not callable(method):
            raise web.HTTPNotFound(
                reason=f"main window has no method {method_name!r}",
            )
        arg = request.query.get("arg")
        # Most actions take no arg; the few that do (close_tab) accept
        # an int. We try int coercion, fall back to string.
        if arg is not None:
            try:
                coerced: object = int(arg)
            except (TypeError, ValueError):
                coerced = arg
        else:
            coerced = None

        # Run on the Qt main thread (queued via _MainThreadInvoker).
        def _call(main):
            if main is None:
                raise web.HTTPServiceUnavailable(reason="main window not registered with the server")
            if coerced is None:
                method()
            else:
                method(coerced)
            return True

        self._run_on_qt(_call)
        return web.json_response({
            "action": name,
            "method": method_name,
            "ok": True,
        })

    async def handle_test_state(self, request: Request) -> Response:
        """Return a JSON snapshot of the Qt-side UI state.

        Only enabled when BFSB_TEST=1 is in the environment. The
        harness uses this to assert that the menu and popovers opened
        without relying on screenshot diffs (which fail under bare
        Xvfb without a window manager — Qt's popup windows aren't
        composited, so the framebuffer stays the same).

        The snapshot includes:
        - ``menu_open`` — whether the BFSBMenu is currently visible
        - ``bookmark_popover_open`` — whether the Bookmarks popover is
          currently visible
        - ``history_popover_open`` — whether the History popover is
          currently visible
        - ``sidebar_open`` — whether the side panel dock is visible
        - ``bookmark_count`` — number of bookmarks in the store
        - ``history_count`` — number of history entries in the store
        - ``view_count`` — number of QWebEngineView tabs

        Like ``handle_test_action``, this hops to the Qt main thread
        via ``QTimer.singleShot(0, ...)`` because touching QWidgets
        from the aiohttp thread fires SIGTRAP.
        """
        main = self._main_window
        if main is None:
            raise web.HTTPServiceUnavailable(
                reason="main window not registered with the server",
            )

        def _snapshot() -> dict:
            """Run on the Qt main thread — safe to touch QWidgets."""
            snap: dict = {}

            # Menu open? The BFSBMenu is a QWidget; we just check
            # isVisible() on the most recently created instance.
            # ``_active_menu`` is a transient attribute — set in
            # ``_show_main_menu`` and cleared on close.
            menu = getattr(main, "_active_menu", None)
            snap["menu_open"] = bool(menu is not None and menu.isVisible())

            # Popovers — same pattern.
            bp = getattr(main, "_bookmark_popover", None)
            hp = getattr(main, "_history_popover", None)
            snap["bookmark_popover_open"] = bool(bp is not None and bp.isVisible())
            snap["history_popover_open"] = bool(hp is not None and hp.isVisible())

            # Sidebar — the side panel dock.
            side_panel = getattr(main, "_side_panel", None)
            if side_panel is not None and side_panel.isVisible():
                snap["sidebar_open"] = True
            else:
                snap["sidebar_open"] = False

            # Bookmark / history counts.
            bm_store = getattr(main, "_bookmarks", None)
            if bm_store is not None and hasattr(bm_store, "list_all"):
                try:
                    snap["bookmark_count"] = len(bm_store.list_all(limit=10000))
                except Exception:
                    snap["bookmark_count"] = -1
            else:
                snap["bookmark_count"] = 0

            hist_store = getattr(main, "_history", None)
            if hist_store is not None and hasattr(hist_store, "count"):
                try:
                    snap["history_count"] = int(hist_store.count())
                except Exception:
                    snap["history_count"] = -1
            elif hist_store is not None and hasattr(hist_store, "list_recent"):
                try:
                    snap["history_count"] = len(hist_store.list_recent(limit=10000))
                except Exception:
                    snap["history_count"] = -1
            else:
                snap["history_count"] = 0

            # View count — number of tabs.
            views = getattr(main, "_views", None)
            if views is not None:
                snap["view_count"] = len(views)
            else:
                snap["view_count"] = 0

            # Native chrome visibility + the URL it would show. The chrome
            # must be hidden on BFSB pages and visible on external sites.
            chrome = getattr(main, "_chrome", None)
            if chrome is not None:
                snap["chrome_visible"] = chrome.isVisible()
            view = main._get_current_view() if hasattr(main, "_get_current_view") else None
            snap["current_url"] = (
                (getattr(main, "_view_urls", {}).get(view) or view.url().toString())
                if view is not None else ""
            )

            return snap

        # Run on the Qt main thread (queued via _MainThreadInvoker).
        snap = self._run_on_qt(lambda _main: _snapshot())
        return web.json_response(snap)

    async def start(self) -> None:
        """Start the server."""
        self._app = self._create_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        print(f"[BFSHBServer] Started on http://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the server."""
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        print("[BFSHBServer] Stopped")

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def home_url(self) -> str:
        return f"{self.base_url}/"

    @property
    def search_url(self) -> str:
        return f"{self.base_url}/search"


# Global server instance
_server_instance: Optional[BFSHBServer] = None


# --- module-level helper for handle_search ---------------------------------

async def search_async(query: str, page: int = 1) -> Optional[AggregateResponse]:
    """Run the multi-source aggregator. Returns an AggregateResponse
    or None on total failure. All upstream HTTP connections come from
    THIS process — the embedded chromium never makes outbound search
    calls."""
    try:
        return await aggregate_search(query)
    except Exception as e:
        print(f"[search_async] aggregator failed: {e}", flush=True)
        return None


# --- module-level helpers for the empty-state UI ---------------------------

def make_empty_results() -> str:
    """Render an empty-state results block (no DDG branding anywhere)."""
    return (
        '<div class="empty-results">'
        '<div class="empty-results-icon" aria-hidden="true">'
        '<svg viewBox="0 0 24 24" width="48" height="48" fill="none" '
        'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
        'stroke-linejoin="round">'
        '<circle cx="11" cy="11" r="7"/>'
        '<line x1="20" y1="20" x2="16.5" y2="16.5"/>'
        '</svg></div>'
        '<div class="empty-results-title">No results to show</div>'
        '<div class="empty-results-hint">Try a different query or check '
        'your spelling.</div>'
        '</div>'
    )


def make_empty_infobox() -> str:
    return (
        '<div class="infobox">'
        '<div class="infobox-title">Info</div>'
        '<div style="color:#55556a;">No additional info available</div>'
        '</div>'
    )


async def get_server() -> BFSHBServer:
    """Get or create the global server instance."""
    global _server_instance
    if _server_instance is None:
        template_dir = Path(__file__).parent.parent / "templates"
        _server_instance = BFSHBServer(template_dir)
        await _server_instance.start()
    return _server_instance


async def shutdown_server() -> None:
    """Shutdown the global server."""
    global _server_instance
    if _server_instance:
        await _server_instance.stop()
        _server_instance = None
    await aggregator_shutdown()