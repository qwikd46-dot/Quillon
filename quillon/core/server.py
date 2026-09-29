"""
Quillon Local HTTP Server Backend

Serves HTML templates and handles search API.
Runs on localhost:8888 (or configurable port).
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse, unquote

from aiohttp import web
from aiohttp.web_request import Request
from aiohttp.web_response import Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PyQt6.QtCore import QMetaObject, QTimer, Qt, Q_ARG, QObject, pyqtSlot
from PyQt6.QtWidgets import QApplication

from quillon.core.search.aggregator import AggregateResponse, Result, aggregate_search, shutdown as aggregator_shutdown


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


class _ProgressChannel:
    def __init__(self, request_id: str, query: str) -> None:
        self.request_id = request_id
        self.query = query
        self.events: list[dict[str, object]] = []
        self.subscribers: set[asyncio.Queue] = set()
        self.done = False

    def publish(self, event: dict[str, object]) -> None:
        if self.done:
            return
        self.events.append(dict(event))
        if len(self.events) > 128:
            del self.events[:-128]
        for queue in tuple(self.subscribers):
            try:
                queue.put_nowait(dict(event))
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(dict(event))

    def finish(self, event: dict[str, object]) -> None:
        if self.done:
            return
        self.publish(event)
        self.done = True
        for queue in tuple(self.subscribers):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(None)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=160)
        for event in self.events:
            queue.put_nowait(dict(event))
        if self.done:
            queue.put_nowait(None)
        self.subscribers.add(queue)
        return queue


# Deprecated: we no longer redirect to upstream. Results are aggregated
# server-side and rendered on local origin. Kept for reference only.
SEARCH_HANDBACK_URL = "https://duckduckgo.com/?q={q}"

# Upper bound on how stale the rendered vault status may be. The cache is
# keyed on the vault database's mtime+size and normally re-probes the
# moment that changes; this only bounds the case where availability flips
# without the database moving, such as the OS keyring locking.
_VAULT_STATUS_TTL = 5.0


class QuillonHBServer:
    """Local HTTP server for Quillon browser."""

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
        # route dispatches into this object when QUILLON_TEST=1 is in
        # the environment. Optional[QObject] at runtime.
        self._main_window = None
        # ((available, error), checked_at, db_signature) for _vault_status.
        self._vault_cache: tuple = (None, 0.0, None)

        # Jinja2 environment for template rendering
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(['html', 'xml']),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # Created lazily on the Qt main thread by _run_on_qt (see class doc).
        self._invoker: Optional[_MainThreadInvoker] = None
        self._progress_channels: dict[str, _ProgressChannel] = {}

        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    def _create_app(self) -> web.Application:
        """Create aiohttp application with routes."""
        app = web.Application()
        # The address this server is actually bound to. The CORS guard
        # compares request.host against this instead of trusting the
        # client-supplied Host header to prove the caller is local.
        app["quillon_bound_host"] = self.host
        app["quillon_bound_port"] = self.port

        # Routes
        app.router.add_get('/', self.handle_home)
        app.router.add_get('/health', self.handle_health)
        app.router.add_get('/search', self.handle_search)
        app.router.add_get('/api/search/progress', self.handle_search_progress)
        app.router.add_get('/about', self.handle_about_page)
        app.router.add_get('/preferences', self.handle_preferences_page)
        
        # Quillon internal routes (replacing quillon:// scheme)
        app.router.add_get('/_quillon/newTab', self.handle_new_tab)
        app.router.add_get('/_quillon/switchTab', self.handle_switch_tab)
        app.router.add_get('/_quillon/closeTab', self.handle_close_tab)
        app.router.add_get('/_quillon/goBack', self.handle_go_back)
        app.router.add_get('/_quillon/goForward', self.handle_go_forward)
        app.router.add_get('/_quillon/reload', self.handle_reload)
        app.router.add_get('/_quillon/about', self.handle_about_page)

        # JSON feeds for the in-page bookmarks/history views (best-effort).
        app.router.add_get('/api/bookmarks', self.handle_api_bookmarks)
        app.router.add_get('/api/history', self.handle_api_history)
        app.router.add_get('/api/downloads', self.handle_api_downloads)
        # Mutations as POST with JSON answers (reliable; no scheme-dispatch).
        app.router.add_post('/api/bookmark/toggle', self.handle_api_bookmark_toggle)
        app.router.add_post('/api/bookmark/add', self.handle_api_bookmark_add)
        app.router.add_post('/api/bookmark/remove', self.handle_api_bookmark_remove)
        app.router.add_post('/api/history/add', self.handle_api_history_add)
        app.router.add_post('/api/history/remove', self.handle_api_history_remove)
        app.router.add_post('/api/history/clear', self.handle_api_history_clear)
        app.router.add_post('/api/ui/newTab', self.handle_api_new_tab)
        app.router.add_post('/api/ui/closeTab', self.handle_api_close_tab)
        app.router.add_post('/api/ui/switchTab', self.handle_api_ui_switch_tab)
        # Adblocker state shared with the proxy/mitmdump layer (adblock_state.py).
        app.router.add_get('/api/adblock', self.handle_api_adblock_get)
        app.router.add_post('/api/adblock', self.handle_api_adblock_set)

        # Test fixtures used by the hermes harness. Serves tiny static
        # files (2KB text, 1KB PDF) so the DOWNLOADS check can verify
        # that a real downloadRequested signal lands in ~/Downloads.
        app.router.add_get('/test-files/{filename}', self.handle_test_file)

        # Test action dispatch — only enabled when QUILLON_TEST=1 is in the
        # environment. Lets the hermes harness drive Qt-side actions
        # (new tab, close tab, toggle sidebar, open menu) over plain
        # HTTP, sidestepping the unreliable ydotool-to-Qt path under
        # Xvfb. Each action returns a small JSON describing the result.
        if os.environ.get("QUILLON_TEST") == "1":
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
    def _is_local_origin(origin: str) -> bool:
        """True only for an origin that is genuinely Quillon's own page.

        8888 used to be trusted here. That port is the SearXNG container,
        a separate network-facing service with its own attack surface, and
        the browser page never needs to be same-origin with it: search
        results reach the page through the server, not by fetching 8888
        from the page. Trusting it meant anything SearXNG could be made
        to serve -- a reflected result page, an open redirect, a plugin --
        was silently promoted to a fully trusted Quillon origin with read and
        write access to history, bookmarks and the tab controls. Only
        this server's own port is trusted.
        """
        try:
            parsed = urlparse(origin)
            return (
                parsed.scheme in ("http", "https")
                and parsed.hostname in ("127.0.0.1", "localhost")
                and parsed.port == 8889
            )
        except Exception:
            return False

    @staticmethod
    @web.middleware
    async def _cors_middleware(request: Request, handler):
        origin = request.headers.get("Origin", "")
        if origin and not QuillonHBServer._is_local_origin(origin):
            return web.json_response({"error": "origin not allowed"}, status=403)
        # A same-origin fetch from Quillon's own page sends NO Origin header
        # (Origin is only added for cross-origin and non-GET requests), so
        # demanding Origin would lock out the page itself. The browser
        # does however always send Sec-Fetch-Site, and a page on another
        # site cannot forge it: a cross-site subresource request such as
        # <img src="http://127.0.0.1:8889/search?q=..."> arrives as
        # cross-site. That is what stops a web page from driving /search
        # (which writes history and fans out to providers) or
        # /api/search/progress (which creates and evicts server state).
        fetch_site = (request.headers.get("Sec-Fetch-Site") or "").lower()
        if fetch_site in ("cross-site", "same-site") and request.method not in ("GET", "HEAD", "OPTIONS"):
            return web.json_response({"error": "cross-site request refused"}, status=403)
        if fetch_site == "cross-site" and request.path in ("/search", "/api/search/progress"):
            return web.json_response({"error": "cross-site request refused"}, status=403)
        state_changing_get = request.path.startswith("/_quillon/") or request.path.startswith("/api/ui/")
        # request.host is the client-supplied Host header, so using it to
        # decide "is this a local API request" let the caller certify itself:
        # any request claiming Host: 127.0.0.1:8889 skipped every check
        # below. Compare against the address this server actually bound.
        # Note this is still not authentication — Host and Origin are both
        # self-asserted, so a local process can forge either. See the
        # token discussion before relying on this as a trust boundary.
        bound = f"{request.app.get('quillon_bound_host', '')}:{request.app.get('quillon_bound_port', '')}"
        local_api_request = request.path.startswith("/api/ui/") and request.host == bound
        if state_changing_get and not origin and not local_api_request and os.environ.get("QUILLON_TEST") != "1":
            return web.json_response({"error": "origin required"}, status=403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and not origin and not local_api_request and os.environ.get("QUILLON_TEST") != "1":
            return web.json_response({"error": "origin required"}, status=403)
        if request.method == "OPTIONS":
            response = web.Response(status=204)
        else:
            response = await handler(request)
        if request.path != '/api/search/progress' and origin:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
            response.headers['Vary'] = 'Origin'
        return response

    async def handle_home(self, request: Request) -> Response:
        """Serve home page - rendered through Jinja2 with PAGE_TYPE='home'."""
        template = self.jinja_env.get_template('quillon_combined.html')
        html = template.render(
            **self._safety_context(),
            PAGE_TYPE='home',
            HOME_BOOKMARKS_HTML=self._home_bookmarks_html(),
            BOOKMARKS_JSON=self._bookmarks_json(),
        )
        return web.Response(text=html, content_type='text/html')

    def _safety_context(self) -> dict:
        """Blocklist and vault data the page needs to police itself.

        The blocklist is rendered from Python so the address bar and the
        server agree on exactly what counts as blocked. The vault status
        travels with it because a page that silently stops saving
        passwords has to say so on every page, not just the home page.
        """
        try:
            from quillon.core.search import safety as safety_module

            enabled = safety_module.enabled()
            terms = sorted(safety_module.terms()) if enabled else []
            domains = sorted(safety_module.ADULT_DOMAINS) if enabled else []
        except Exception:
            enabled, terms, domains = False, [], []
        available, error, degraded, mode = self._vault_status()
        return {
            "SAFETY_ENABLED": bool(enabled),
            "ADULT_TERMS_JSON": json.dumps(terms),
            "ADULT_DOMAINS_JSON": json.dumps(domains),
            "VAULT_AVAILABLE": bool(available),
            "VAULT_ERROR": error or "",
            "VAULT_DEGRADED": bool(degraded),
            "VAULT_STORAGE_MODE": mode or "",
        }

    def _vault_status(self) -> tuple[bool, str, bool, str]:
        """(available, error, degraded) for the password and cookie vaults.

        Reads the ``available``/``last_error``/``storage_mode`` the vault
        classes already expose rather than keeping a second status of its
        own. Only the Qt main thread may be *called* into, so the window
        reference is read here and the plain-Python attributes touched
        directly; both are safe from the aiohttp thread and neither is a
        QObject.

        Cached, because a render burst would otherwise probe on every
        page. The cache is keyed on the vault database's mtime+size, the
        same cheap change token ``adblock_state`` uses, and bounded by a
        TTL so a keyring that locks without touching the database is
        still noticed. Deliberately lazy: there is no background refresh
        task, because the only consumer is a render.
        """
        now = time.monotonic()
        signature = self._vault_signature()
        cached, checked_at, cached_signature = self._vault_cache
        if cached is not None and cached_signature == signature and now - checked_at < _VAULT_STATUS_TTL:
            return cached

        status = self._probe_vaults()
        self._vault_cache = (status, now, signature)
        return status

    @staticmethod
    def _vault_signature() -> tuple:
        """(mtime, size) of the vault database, or a zero pair if absent."""
        try:
            from quillon.core.config import PATHS

            stat = os.stat(PATHS.VAULT_DB)
            return (stat.st_mtime, stat.st_size)
        except Exception:
            return (0.0, 0)

    def _probe_vaults(self) -> tuple[bool, str, bool, str]:
        """(available, error, degraded, storage_mode) for the vaults.

        ``storage_mode`` is carried so the page can key its once-per-profile
        marker on the degraded condition itself rather than on the
        unavailable notice's error string.

        ``degraded`` means the vault IS saving but its key is in a file
        rather than the OS keyring, because none was reachable. That is
        a different thing from ``available is False``, where nothing is
        being saved at all, and the page must not conflate them.
        """
        window = self._main_window
        vaults = []
        for name in ("_password_vault", "_cookie_vault"):
            vault = getattr(window, name, None)
            if vault is not None:
                vaults.append(vault)
        if not vaults:
            # No window yet (startup race, or a headless harness). Report
            # unavailable: claiming the vault is fine when nothing has
            # confirmed it is the exact failure this notice exists for.
            return (False, "vault has not reported ready yet", False, "")
        for vault in vaults:
            if not getattr(vault, "available", False):
                return (False, str(getattr(vault, "last_error", "") or "vault unavailable"), False, "")
        modes = {str(getattr(vault, "storage_mode", "") or "") for vault in vaults}
        degraded = "file-degraded" in modes
        return (True, "", degraded, "+".join(sorted(m for m in modes if m)))

    @staticmethod
    def _safe_external_url(value: str) -> Optional[str]:
        try:
            parsed = urlparse((value or "").strip())
            if parsed.scheme not in ("http", "https") or not parsed.netloc or not parsed.hostname or parsed.username or parsed.password:
                return None
            parsed.port
            return value.strip()
        except Exception:
            return None

    @staticmethod
    def _saved_bookmark_dicts(limit: int = 50) -> list[dict]:
        """Read saved bookmarks as plain dicts. Never raises — [] on failure."""
        try:
            from quillon.core.storage.bookmarks import BookmarkStore

            marks = BookmarkStore().list_all(limit=limit) or []
            out = []
            for m in marks:
                safe_url = QuillonHBServer._safe_external_url(m.url)
                if safe_url is None:
                    continue
                title = (m.title or safe_url or '').strip() or safe_url
                out.append({
                    'name': title,
                    'url': safe_url,
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

    async def handle_api_downloads(self, request: Request) -> Response:
        def _snapshot(main):
            manager = getattr(main, "_download_manager", None)
            if manager is None:
                return []
            return [
                {
                    "id": item.id,
                    "filename": item.filename,
                    "state": item.state,
                    "received_bytes": item.received_bytes,
                    "total_bytes": item.total_bytes,
                    "progress": item.progress,
                }
                for item in manager.all()
            ]

        try:
            return web.json_response(await self._run_on_qt_async(_snapshot))
        except Exception:
            return web.json_response([])

    async def _run_on_qt_async(self, fn, timeout: float = 5.0):
        """Await a Qt-thread hop without parking the event loop.

        _run_on_qt blocks on a threading.Event, and every caller is an
        async handler running ON the aiohttp event loop, so one request
        that could not reach Qt froze the whole server: the home page,
        /search and the progress streams all died for up to five seconds.
        Two such requests serialised into ten. Parking the wait in a
        worker thread keeps the loop serving, and the hop itself is
        unchanged.
        """
        import asyncio
        import functools

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(self._run_on_qt, fn, timeout)
        )

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
        from quillon.ui.main_window import QuillonWindow

        app = QApplication.instance()
        if app is None:
            return fn(None)
        target = None
        for w in app.topLevelWidgets():
            if isinstance(w, QuillonWindow):
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
            if which == "bookmarks":
                if panel is not None:
                    panel.refresh_bookmarks()
                refresh_views = getattr(main, "_refresh_quillon_bookmark_views", None)
                if callable(refresh_views):
                    refresh_views()
            elif which == "history" and panel is not None:
                panel.refresh_history()
        except Exception:
            pass

    async def handle_api_bookmark_toggle(self, request: Request) -> Response:
        url = self._safe_external_url(request.query.get("url") or "")
        title = (request.query.get("title") or "").strip() or url
        if not url:
            return web.json_response({"ok": False})

        def _do(main):
            from quillon.core.storage.bookmarks import BookmarkStore

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
            marked = await self._run_on_qt_async(_do)
            return web.json_response({"ok": True, "bookmarked": bool(marked)})
        except Exception:
            return web.json_response({"ok": False})

    async def handle_api_bookmark_add(self, request: Request) -> Response:
        url = self._safe_external_url(request.query.get("url") or "")
        title = (request.query.get("title") or "").strip() or url
        if not url:
            return web.json_response({"ok": False})

        def _do(main):
            from quillon.core.storage.bookmarks import BookmarkStore

            store = main._bookmarks if main is not None and hasattr(main, "_bookmarks") else BookmarkStore()
            store.add(url=url, title=title)
            self._refresh_panels(main, "bookmarks")
            return True

        try:
            await self._run_on_qt_async(_do)
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False})

    async def handle_api_bookmark_remove(self, request: Request) -> Response:
        url = self._safe_external_url(request.query.get("url") or "")
        if not url:
            return web.json_response({"ok": False})

        def _do(main):
            from quillon.core.storage.bookmarks import BookmarkStore

            store = main._bookmarks if main is not None and hasattr(main, "_bookmarks") else BookmarkStore()
            store.remove(url)
            self._refresh_panels(main, "bookmarks")
            return True

        try:
            await self._run_on_qt_async(_do)
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False})

    async def handle_api_history_add(self, request: Request) -> Response:
        url = self._safe_external_url(request.query.get("url") or "")
        title = (request.query.get("title") or "").strip()
        if not url:
            return web.json_response({"ok": False, "id": None})

        def _do(main):
            from quillon.core.storage.history import HistoryStore

            store = main._history if main is not None and hasattr(main, "_history") else HistoryStore()
            new_id = store.record(url, title)
            self._refresh_panels(main, "history")
            return new_id

        try:
            new_id = await self._run_on_qt_async(_do)
            return web.json_response({"ok": new_id is not None, "id": new_id})
        except Exception:
            return web.json_response({"ok": False, "id": None})

    async def handle_api_history_remove(self, request: Request) -> Response:
        try:
            entry_id = int(request.query.get("id", ""))
        except (TypeError, ValueError):
            return web.json_response({"ok": False})

        def _do(main):
            from quillon.core.storage.history import HistoryStore

            store = main._history if main is not None and hasattr(main, "_history") else HistoryStore()
            store.remove(entry_id)
            self._refresh_panels(main, "history")
            return True

        try:
            await self._run_on_qt_async(_do)
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False})

    async def handle_api_history_clear(self, request: Request) -> Response:
        def _do(main):
            from quillon.core.storage.history import HistoryStore

            store = main._history if main is not None and hasattr(main, "_history") else HistoryStore()
            store.clear()
            self._refresh_panels(main, "history")
            return True

        try:
            await self._run_on_qt_async(_do)
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False})

    async def handle_api_new_tab(self, request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        url = payload.get("url") if isinstance(payload, dict) else None
        if url is not None and not isinstance(url, str):
            url = None

        def _do(main):
            if main is None:
                return False
            if url and not main._safe_action_target(url):
                return False
            main.new_tab(url or None)
            return True

        try:
            ok = await self._run_on_qt_async(_do)
            return web.json_response({"ok": bool(ok)})
        except Exception:
            return web.json_response({"ok": False})

    async def handle_api_close_tab(self, request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            index = int(payload.get("index"))
        except (TypeError, ValueError, AttributeError):
            index = None

        def _do(main):
            if main is None:
                return False
            target = main.get_active_tab_index() if index is None else index
            main.close_tab(target)
            return True

        try:
            ok = await self._run_on_qt_async(_do)
            return web.json_response({"ok": bool(ok)})
        except Exception:
            return web.json_response({"ok": False}, status=503)

    async def handle_api_ui_switch_tab(self, request: Request) -> Response:
        try:
            payload = await request.json()
            index = int(payload.get("index"))
        except (TypeError, ValueError, AttributeError):
            return web.json_response({"ok": False}, status=400)

        def _do(main):
            if main is None:
                return False
            main.switch_tab(index)
            return True

        try:
            ok = await self._run_on_qt_async(_do)
            return web.json_response({"ok": bool(ok)})
        except Exception:
            return web.json_response({"ok": False}, status=503)

    async def handle_api_adblock_get(self, request: Request) -> Response:
        """Current adblocker on/off state (shared with the mitmdump layer)."""
        try:
            from quillon.core.adblock_state import is_adblock_enabled

            return web.json_response({"ok": True, "enabled": bool(is_adblock_enabled())})
        except Exception:
            return web.json_response({"ok": False, "enabled": True})

    async def handle_api_adblock_set(self, request: Request) -> Response:
        """Set the adblocker on/off state (?enabled=1|0)."""
        enabled = (request.query.get("enabled") or "").strip() not in ("0", "false", "off")
        try:
            from quillon.core.adblock_state import set_adblock_enabled

            set_adblock_enabled(enabled)
            return web.json_response({"ok": True, "enabled": enabled})
        except Exception:
            return web.json_response({"ok": False, "enabled": enabled})

    async def handle_api_history(self, request: Request) -> Response:
        """JSON feed of recent history for the in-page history view."""
        try:
            from quillon.core.storage.history import HistoryStore

            items = HistoryStore().list_recent(limit=200) or []
            return web.json_response([
                {'id': e.id, 'url': e.url, 'title': e.title, 'time': e.last_visit}
                for e in items
            ])
        except Exception:
            return web.json_response([])

    def _progress_channel(self, request_id: str, query: str) -> _ProgressChannel:
        channel = self._progress_channels.get(request_id)
        if channel is None:
            if len(self._progress_channels) >= 64:
                oldest = next(iter(self._progress_channels))
                if not self._progress_channels[oldest].done:
                    self._progress_channels[oldest].finish({"event": "error", "message": "search superseded"})
                self._progress_channels.pop(oldest, None)
            channel = _ProgressChannel(request_id, query)
            self._progress_channels[request_id] = channel
        return channel

    @staticmethod
    def _progress_publisher(channel: Optional[_ProgressChannel]):
        if channel is None:
            return None

        def publish(event: dict[str, object]) -> None:
            channel.publish(event)

        return publish

    @staticmethod
    def _finish_progress(
        channel: Optional[_ProgressChannel],
        request_id: str,
        total_ms: int,
        failed: bool = False,
    ) -> None:
        if channel is None or channel.done:
            return
        if failed:
            channel.publish({
                "event": "error",
                "request_id": request_id,
                "message": "aggregation failed",
            })
        channel.finish({
            "event": "done",
            "request_id": request_id,
            "total_ms": total_ms,
        })

    async def handle_search_progress(self, request: Request) -> web.StreamResponse:
        query = (request.query.get("q") or "").strip()
        if not query:
            raise web.HTTPBadRequest(reason="q is required")
        request_id = (request.query.get("request_id") or uuid.uuid4().hex).strip()
        channel = self._progress_channel(request_id, query)
        queue = channel.subscribe()
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)
        await response.write(b": connected\n\n")
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    break
                if event is None:
                    break
                name = str(event.get("event", "message"))
                data = json.dumps(event, separators=(",", ":"))
                message = f"id: {len(channel.events)}\nevent: {name}\ndata: {data}\n\n"
                await response.write(message.encode("utf-8"))
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            channel.subscribers.discard(queue)
        return response

    async def handle_search(self, request: Request) -> Response:
        """Render or return a Quillon-search result payload."""
        query = (request.query.get("q") or "").strip()
        if not query:
            raise web.HTTPFound("/")

        blocked = self._blocked_search(query)
        if blocked:
            return await self._render_blocked_search(request, query, blocked)

        wants_json = (request.query.get("format") or "").lower() == "json"
        request_id = (request.query.get("request_id") or uuid.uuid4().hex).strip()
        channel = self._progress_channel(request_id, query) if wants_json else None
        progress = self._progress_publisher(channel)
        if channel is not None and not channel.events:
            channel.publish({"event": "submitted", "request_id": request_id, "query": query, "percent": 10})

        import time as _ptime
        _t_submit = _ptime.perf_counter()

        try:
            from urllib.parse import quote as _quote
            from quillon.core.storage.history import HistoryStore

            await asyncio.to_thread(
                HistoryStore().record,
                f"http://127.0.0.1:8889/search?q={_quote(query)}",
                query,
            )
        except asyncio.CancelledError:
            self._finish_progress(
                channel,
                request_id,
                int((_ptime.perf_counter() - _t_submit) * 1000),
                failed=True,
            )
            raise
        except Exception:
            pass
        _t_history = _ptime.perf_counter()

        agg: Optional[AggregateResponse] = None
        try:
            agg = await search_async(query, progress=progress)
        except asyncio.CancelledError:
            self._finish_progress(
                channel,
                request_id,
                int((_ptime.perf_counter() - _t_submit) * 1000),
                failed=True,
            )
            raise
        except Exception as e:
            print(f"[search] failure: {type(e).__name__}: {e}", flush=True)
        _t_agg = _ptime.perf_counter()

        shown = [
            result for result in (agg.results[:12] if agg and agg.results else [])
            if self._safe_external_url(result.url)
        ]
        results_html = self._format_results_html(shown) if shown else make_empty_results()
        infobox_html = self._format_infobox_html(agg)
        count_text = f"About {len(shown)} results" if shown else "No results to display"
        total_ms = agg.total_ms if agg else int((_ptime.perf_counter() - _t_submit) * 1000)
        if channel is not None:
            self._finish_progress(channel, request_id, total_ms, failed=agg is None)

        if wants_json:
            return web.json_response({
                "ok": True,
                "request_id": request_id,
                "query": query,
                "count": len(shown),
                "count_text": count_text,
                "results_html": results_html,
                "infobox_html": infobox_html,
                "results": [
                    {
                        "url": result.url,
                        "title": result.title,
                        "snippet": result.snippet,
                        "source": result.source,
                        "rank": result.rank,
                    }
                    for result in shown
                ],
                "errors": dict(agg.errors) if agg else {},
                "total_ms": total_ms,
            })

        template = self.jinja_env.get_template("quillon_combined.html")
        html = template.render(
            **self._safety_context(),
            PAGE_TYPE="results",
            QUERY=query,
            RESULTS_COUNT=count_text,
            RESULTS_HTML=results_html,
            INFOBOX_HTML=infobox_html,
            BOOKMARKS_JSON=self._bookmarks_json(),
        )
        _t_render = _ptime.perf_counter()
        if os.environ.get("QUILLON_PERF") == "1":
            print(
                f"[PERF] search q={query!r} submit->history={(_t_history-_t_submit)*1000:.1f}ms "
                f"history->agg={(_t_agg-_t_history)*1000:.1f}ms "
                f"agg->render={(_t_render-_t_agg)*1000:.1f}ms "
                f"server_total={(_t_render-_t_submit)*1000:.1f}ms results={len(shown)}",
                flush=True,
            )
        return web.Response(text=html, content_type="text/html")

    @staticmethod
    def _blocked_search(query: str) -> Optional[str]:
        """What in this query is blocklisted, or None to search normally."""
        try:
            from quillon.core.search import safety as safety_module

            if not safety_module.enabled():
                return None
            return safety_module.classify(query)
        except Exception:
            return None

    async def _render_blocked_search(
        self, request: Request, query: str, blocked: str
    ) -> Response:
        """Refuse a flagged query.

        No search provider is contacted and the query is deliberately
        kept out of history — the whole point is not to associate these
        terms with the user's browsing.
        """
        if (request.query.get("format") or "").lower() == "json":
            return web.json_response({
                "ok": True,
                "query": query,
                "blocked": True,
                "blocked_term": blocked,
                "count": 0,
                "count_text": "Search blocked",
                "results_html": "",
                "infobox_html": "",
                "results": [],
                "errors": {},
                "total_ms": 0,
            })

        template = self.jinja_env.get_template("quillon_combined.html")
        html_out = template.render(
            **self._safety_context(),
            PAGE_TYPE="results",
            QUERY=query,
            RESULTS_COUNT="Search blocked",
            RESULTS_HTML=make_empty_results(),
            INFOBOX_HTML="",
            BLOCKED_TERM=blocked,
            BOOKMARKS_JSON=self._bookmarks_json(),
        )
        return web.Response(text=html_out, content_type="text/html")

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
            links = []
            for label, url in pairs or ():
                safe_url = self._safe_external_url(url)
                if safe_url:
                    links.append(
                        f'<a href="{html.escape(safe_url, quote=True)}">{html.escape(label or "")}</a>'
                    )
            if not links:
                return ''
            return '<div class="infobox-links">' + ''.join(links) + '</div>'

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
        return web.Response(text='<html><body>Quillon Browser - About</body></html>', content_type='text/html')

    async def handle_preferences_page(self, request: Request) -> Response:
        """Serve preferences page."""
        template = self.jinja_env.get_template('quillon_combined.html')
        html = template.render(**self._safety_context(), PAGE_TYPE='preferences')
        return web.Response(text=html, content_type='text/html')

    def _dispatch_window_method(self, method_name: str, *args):
        def _call(main):
            if main is None:
                return False
            return getattr(main, method_name)(*args)
        return self._run_on_qt(_call)

    async def handle_new_tab(self, request: Request) -> Response:
        target = request.query.get("url")

        def _do(main):
            if main is None:
                return False
            if target and not main._safe_action_target(target):
                return False
            main.new_tab(target or None)
            return True

        try:
            return web.json_response({"ok": bool(await self._run_on_qt_async(_do))})
        except Exception:
            return web.json_response({"ok": False}, status=503)

    async def handle_switch_tab(self, request: Request) -> Response:
        try:
            index = int(request.query.get("index", ""))
            self._dispatch_window_method("switch_tab", index)
            return web.json_response({"ok": True})
        except (TypeError, ValueError):
            return web.json_response({"ok": False}, status=400)
        except Exception:
            return web.json_response({"ok": False}, status=503)

    async def handle_close_tab(self, request: Request) -> Response:
        try:
            index = int(request.query.get("index", ""))
            self._dispatch_window_method("close_tab", index)
            return web.json_response({"ok": True})
        except (TypeError, ValueError):
            return web.json_response({"ok": False}, status=400)
        except Exception:
            return web.json_response({"ok": False}, status=503)

    async def handle_go_back(self, request: Request) -> Response:
        try:
            self._dispatch_window_method("go_back")
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False}, status=503)

    async def handle_go_forward(self, request: Request) -> Response:
        try:
            self._dispatch_window_method("go_forward")
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False}, status=503)

    async def handle_reload(self, request: Request) -> Response:
        try:
            self._dispatch_window_method("reload")
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False}, status=503)

    async def handle_about_page(self, request: Request) -> Response:
        """Serve about page."""
        template = self.jinja_env.get_template('quillon_combined.html')
        html = template.render(**self._safety_context(), PAGE_TYPE='about')
        return web.Response(text=html, content_type='text/html')

    async def handle_health(self, request: Request) -> Response:
        """Health check endpoint for server readiness."""
        return web.Response(text='OK', content_type='text/plain', status=200)

    # Test fixtures: small static payloads the hermes harness downloads
    # through the embedded QWebEngineProfile to exercise the
    # downloadRequested → DownloadManager path. Each is keyed on filename
    # so a future PDF or image check just adds a branch here.
    _TEST_FILE_PAYLOADS: dict[str, tuple[bytes, str]] = {
        "sample.txt": (b"Hello Quillon test file\n" * 100, "text/plain"),
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
        "switchTab": "switch_tab",
        "reorderTabs": "reorder_tabs",
        "toggleSideOverlay": "_toggle_side_overlay",
        "showMainMenu": "_show_main_menu",
    }

    async def handle_test_action(self, request: Request) -> Response:
        """Dispatch a test action to the MainWindow.

        Only enabled when QUILLON_TEST=1 was in the environment when the
        server was constructed. Each action is a method name on the
        MainWindow; we resolve it from the whitelist above and call
        it. Optional ?arg=... query parameter is passed as a string
        to the method (e.g. ``/test-action/closeTab?arg=0``).

        IMPORTANT: aiohttp runs handlers on its own event loop, not
        the Qt main thread. QWidget constructors and most
        ``MainWindow`` methods must run on the main thread, otherwise
        Qt fires SIGTRAP (the trap in the quillon log) the moment we
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
        if name == "reorderTabs":
            try:
                from_index = int(request.query.get("from", ""))
                to_index = int(request.query.get("to", ""))
            except (TypeError, ValueError) as error:
                raise web.HTTPBadRequest(reason="from and to are required") from error
            coerced: object = (from_index, to_index)
        elif arg is not None:
            try:
                coerced = int(arg)
            except (TypeError, ValueError):
                coerced = arg
        elif name == "closeTab":
            coerced = "__current__"
        else:
            coerced = None

        def _call(main):
            if main is None:
                raise web.HTTPServiceUnavailable(reason="main window not registered with the server")
            if coerced == "__current__":
                method(main.get_active_tab_index())
            elif isinstance(coerced, tuple):
                method(*coerced)
            elif coerced is None:
                method()
            else:
                method(coerced)
            return True

        await self._run_on_qt_async(_call)
        return web.json_response({
            "action": name,
            "method": method_name,
            "ok": True,
        })

    async def handle_test_state(self, request: Request) -> Response:
        """Return a JSON snapshot of the Qt-side UI state.

        Only enabled when QUILLON_TEST=1 is in the environment. The
        harness uses this to assert that the menu and popovers opened
        without relying on screenshot diffs (which fail under bare
        Xvfb without a window manager — Qt's popup windows aren't
        composited, so the framebuffer stays the same).

        The snapshot includes:
        - ``menu_open`` — whether the QuillonMenu is currently visible
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

            # Menu open? The QuillonMenu is a QWidget; we just check
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
                snap["active_index"] = main._stack.currentIndex() if hasattr(main, "_stack") else -1
                snap["tab_urls"] = [
                    (getattr(main, "_view_urls", {}).get(view) or view.url().toString())
                    for view in views
                ]
                snap["tab_titles"] = [
                    view.page().title() if view.page().title() else f"Tab {index + 1}"
                    for index, view in enumerate(views)
                ]
            else:
                snap["view_count"] = 0
                snap["active_index"] = -1
                snap["tab_urls"] = []
                snap["tab_titles"] = []
            try:
                from quillon.core.webengine import QuillonPage
                snap["popup_count"] = len(getattr(QuillonPage, "_open_popups", []))
            except Exception:
                snap["popup_count"] = 0

            # Native chrome visibility + the URL it would show. The chrome
            # must be hidden on Quillon pages and visible on external sites.
            chrome = getattr(main, "_chrome", None)
            if chrome is not None:
                snap["chrome_visible"] = chrome.isVisible()
                snap["hamburger_present"] = hasattr(chrome.nav_bar, "btn_sidebar")
                snap["menu_button_present"] = hasattr(chrome.nav_bar, "btn_menu")
            overlay = getattr(main, "_side_overlay", None)
            snap["side_overlay_open"] = bool(overlay is not None and overlay.isVisible())
            view = main._get_current_view() if hasattr(main, "_get_current_view") else None
            snap["current_url"] = (
                (getattr(main, "_view_urls", {}).get(view) or view.url().toString())
                if view is not None else ""
            )

            return snap

        # Run on the Qt main thread (queued via _MainThreadInvoker).
        snap = await self._run_on_qt_async(lambda _main: _snapshot())
        return web.json_response(snap)

    async def start(self) -> None:
        """Start the server."""
        self._app = self._create_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        print(f"[QuillonHBServer] Started on http://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the server."""
        for channel in tuple(self._progress_channels.values()):
            if not channel.done:
                channel.finish({"event": "error", "message": "server stopped"})
        self._progress_channels.clear()
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        print("[QuillonHBServer] Stopped")

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
_server_instance: Optional[QuillonHBServer] = None


# --- module-level helper for handle_search ---------------------------------

async def search_async(
    query: str,
    page: int = 1,
    progress=None,
) -> Optional[AggregateResponse]:
    """Run the multi-source aggregator. Returns an AggregateResponse
    or None on total failure. All upstream HTTP connections come from
    THIS process — the embedded chromium never makes outbound search
    calls."""
    try:
        return await aggregate_search(query, progress=progress)
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


async def get_server() -> QuillonHBServer:
    """Get or create the global server instance."""
    global _server_instance
    if _server_instance is None:
        template_dir = Path(__file__).parent.parent / "templates"
        _server_instance = QuillonHBServer(template_dir)
        await _server_instance.start()
    return _server_instance


async def shutdown_server() -> None:
    """Shutdown the global server."""
    global _server_instance
    if _server_instance:
        await _server_instance.stop()
        _server_instance = None
    await aggregator_shutdown()