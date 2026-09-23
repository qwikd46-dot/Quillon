"""Main application window — BFSB Browser (multi-tab, server-backed)."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Optional
from urllib.parse import quote
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QStackedWidget,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

from .styles import C
from .browser_chrome import BrowserChrome

from ..core import (
    create_web_view,
    URLBlocker,
    CookieVault,
    APP_CONFIG,
    SECURITY_CONFIG,
    BFSBPage,
    get_server,
    shutdown_server,
    BFSHBServer,
    set_bfsb_action_target,
    RequestInterceptor,
)


class BFSBWindow(QMainWindow):
    """Main browser window — multi-tab, server-backed."""

    def __init__(self, blocker: URLBlocker) -> None:
        super().__init__()
        self._blocker = blocker
        self._cookie_vault = CookieVault()

        # Server
        self._server: Optional[BFSHBServer] = None
        self._server_ready = False

        # UI state
        self._profile = None
        self._stack: Optional[QStackedWidget] = None
        self._views: list[QWebEngineView] = []  # web views for each tab
        self._view_urls: dict[QWebEngineView, str] = {}  # track expected URL per view
        self._initialized = False

        # Debounced tab sync
        self._tab_sync_timer = QTimer()
        self._tab_sync_timer.setSingleShot(True)
        self._tab_sync_timer.timeout.connect(self._sync_tabs_to_views)
        self._pending_tab_sync = False

        self._init_profile()
        self._init_ui()
        self._init_server()
        self.new_tab()  # Creates first tab and loads home
        self._initialized = True

    def _init_profile(self) -> None:
        """Initialize WebEngine profile."""
        from ..core import create_web_profile, configure_web_settings
        self._profile = create_web_profile()
        configure_web_settings(self._profile.settings())
        # bfsb:// GUI actions (fetch-based) dispatch into this window.
        set_bfsb_action_target(self)
        # Set URL request interceptor ONCE on the shared profile
        self._profile.setUrlRequestInterceptor(RequestInterceptor(self._blocker))
        self._profile.downloadRequested.connect(self._on_download)

    # Placeholder shown in a tab while the local server starts (replaces the
    # old up-to-10s blocking health-wait on the UI thread).
    _STARTING_HTML = (
        "<html><head><style>html,body{margin:0;height:100%;background:#0e1220;"
        "color:#9aa0b5;font-family:sans-serif;display:flex;align-items:center;"
        "justify-content:center;flex-direction:column;gap:10px}</style></head>"
        "<body><div style='width:34px;height:34px;border-radius:10px;"
        "background:linear-gradient(135deg,#7b5cff,#4f7cff)'></div>"
        "<div>Starting private search engine…</div></body></html>"
    )

    def _init_server(self) -> None:
        """Start the local HTTP server in a background thread.

        The UI thread NEVER waits for it: tabs show a placeholder and
        automatically load the home GUI as soon as the server is ready.
        """
        import threading

        def run_server():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                self._server = loop.run_until_complete(get_server())
                self._server_ready = True
                print("[BFSBWindow] Server ready at", self._server.base_url)
                loop.run_forever()
            except Exception as e:
                print(f"[BFSBWindow] Failed to start server: {e}")
            finally:
                loop.close()

        self._server_thread = threading.Thread(target=run_server, daemon=True)
        self._server_thread.start()

        # Register this window with the server so /test-action and
        # /test-state (and any future Qt-dispatch endpoints) work.
        QTimer.singleShot(0, self._register_with_server)

    def _register_with_server(self) -> None:
        """Register with the server once it's up (runs on the UI thread)."""
        if self._server is not None:
            self._server._main_window = self
        elif not self._server_ready:
            QTimer.singleShot(250, self._register_with_server)

    def _init_ui(self) -> None:
        """Initialize main UI — native chrome above a stacked widget of tabs.

        The native chrome (tab bar + URL bar, restyled to the new design)
        is only visible while the current tab is on an external website —
        the BFSB home/results/panels GUI is fully rendered by the local
        server template and needs no native chrome at all.
        """
        self.setWindowTitle(APP_CONFIG.WINDOW_TITLE)
        self.resize(APP_CONFIG.WINDOW_WIDTH, APP_CONFIG.WINDOW_HEIGHT)

        # Container: chrome on top, tab views below.
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._chrome = BrowserChrome()
        self._chrome.tab_switched.connect(self.switch_tab)
        self._chrome.tab_closed.connect(self.close_tab)
        self._chrome.new_tab_requested.connect(lambda: self.new_tab())
        self._chrome.back_clicked.connect(self.go_back)
        self._chrome.forward_clicked.connect(self.go_forward)
        self._chrome.reload_clicked.connect(self.reload)
        self._chrome.url_submitted.connect(self.navigate)
        self._chrome.bookmark_toggle.connect(self._toggle_bookmark_current)
        self._chrome.menu_clicked.connect(self._show_main_menu)
        self._chrome.setVisible(False)  # BFSB pages render their own GUI
        layout.addWidget(self._chrome)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background: {C.BG_0}; border: none;")
        layout.addWidget(self._stack, 1)

        self.setCentralWidget(container)

    def showEvent(self, event) -> None:
        """Ensure window is raised and activated on Wayland/XWayland."""
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setWindowState(self.windowState() | Qt.WindowState.WindowActive)

    def _create_view(self) -> QWebEngineView:
        """Create a new web view wired to the native chrome."""
        view = create_web_view(self._profile, self._blocker, page_class=BFSBPage, window=self)
        view.page()._main_window = self

        # Keep the tracked URL, the chrome URL bar / buttons, and the
        # chrome visibility in sync with whatever this view loads.
        view.urlChanged.connect(lambda url, v=view: self._on_view_url_changed(v, url))
        view.page().titleChanged.connect(
            lambda title, v=view: self._on_view_title_changed(v, title))
        view.loadStarted.connect(
            lambda v=view: self._chrome.start_progress() if v is self._get_current_view() else None)
        view.loadFinished.connect(
            lambda ok, v=view: self._on_view_load_finished(v, ok))
        return view

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
    # NATIVE CHROME SYNC (visible only on external websites)
    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _is_bfsb_url(url: str) -> bool:
        """True when the URL is BFSB's own GUI (template pages) — these
        render the full new-design interface, so the native chrome stays
        hidden. Everything else (a real website) shows the chrome so the
        user always has tabs and an address bar to get back."""
        u = url or ""
        if u.startswith(("bfsb://", "data:", "about:")):
            return True
        if u.startswith(("http://127.0.0.1", "http://localhost", "https://127.0.0.1", "https://localhost")):
            return True
        return False

    def _on_view_url_changed(self, view, url: QUrl) -> None:
        url_str = url.toString() if hasattr(url, "toString") else str(url)
        if url_str.startswith("bfsb://"):
            return  # internal action, never a real location
        self._view_urls[view] = url_str
        if view is self._get_current_view():
            self._update_chrome_for_current()

    def _on_view_title_changed(self, view, title: str) -> None:
        self._request_tab_sync()
        self._sync_chrome_tabs()

    def _on_view_load_finished(self, view, ok: bool) -> None:
        if view is self._get_current_view():
            self._chrome.complete_progress()
            self._update_chrome_for_current()
        self._request_tab_sync()

    def _update_chrome_for_current(self) -> None:
        """Show the native chrome on websites, hide it on BFSB pages, and
        refresh its URL bar / nav buttons / bookmark star."""
        view = self._get_current_view()
        if view is None:
            self._chrome.setVisible(False)
            return
        url = self._view_urls.get(view) or view.url().toString()
        is_site = not self._is_bfsb_url(url)
        self._chrome.setVisible(is_site)
        if is_site:
            self._chrome.set_url(url)
            try:
                self._chrome.update_nav_buttons(
                    view.history().canGoBack(), view.history().canGoForward())
            except Exception:
                pass
            try:
                from ..core.storage.bookmarks import BookmarkStore
                self._chrome.set_bookmarked(bool(BookmarkStore().get(url)))
            except Exception:
                self._chrome.set_bookmarked(False)

    def _sync_chrome_tabs(self) -> None:
        """Rebuild the native chrome tab bar from the real tab list."""
        try:
            chrome_bar = self._chrome.tab_bar
            chrome_bar.clear()
            home_url = self._server.home_url if self._server else "http://127.0.0.1:8889/"
            for i, view in enumerate(self._views):
                url = self._view_urls.get(view, view.url().toString())
                if url.startswith(("data:", "about:")) or url.startswith(home_url) or self._is_bfsb_url(url):
                    title = "BFSB — New Tab"
                else:
                    title = view.page().title() or f"Tab {i + 1}"
                chrome_bar.add_tab(i, title)
            chrome_bar.set_active(self._stack.currentIndex())
        except Exception as e:
            print(f"[Window] chrome tab sync failed: {e}")

    def _toggle_bookmark_current(self) -> None:
        """Bookmark-star pressed on the native chrome."""
        view = self._get_current_view()
        if view is None:
            return
        url = self._view_urls.get(view) or view.url().toString()
        if self._is_bfsb_url(url):
            return
        try:
            from ..core.storage.bookmarks import BookmarkStore

            store = BookmarkStore()
            if store.get(url):
                store.remove(url)
                marked = False
            else:
                store.add(url=url, title=view.page().title() or url)
                marked = True
            self._chrome.set_bookmarked(marked)
        except Exception as e:
            print(f"[Window] bookmark toggle failed: {e}")

    def _show_main_menu(self) -> None:
        """Show the BFSB 3-dots menu anchored to the native chrome button."""
        from .menu import BFSBMenu

        def _open_panel(panel: str):
            home = self._server.home_url if self._server else "http://127.0.0.1:8889/"
            self.new_tab(f"{home}#{panel}")

        menu = BFSBMenu(self)
        menu.add_item("＋", "New tab", "Ctrl+T", on_triggered=lambda: self.new_tab())
        menu.add_item("★", "Bookmarks", on_triggered=lambda: _open_panel("bookmarks"))
        menu.add_item("🕘", "History", on_triggered=lambda: _open_panel("history"))
        menu.add_item("⬇", "Downloads", on_triggered=lambda: _open_panel("downloads"))
        menu.add_separator()
        menu.add_item("⚙", "Settings", on_triggered=lambda: _open_panel("settings"))
        menu.add_item("ℹ", "About BFSB", on_triggered=self._show_about_dialog)
        menu.add_separator()
        menu.add_item("⌫", "Quit BFSB", "Ctrl+Q", on_triggered=self.close)
        btn = self._chrome.menu_button()
        menu.popup_at(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _on_download(self, download) -> None:
        """Handle download requests — security hardened."""
        url_path = download.url().path()
        ext = Path(url_path).suffix.lower()

        if ext in SECURITY_CONFIG.BAD_EXTENSIONS or not ext:
            print(f"BLOCKED DOWNLOAD: {url_path}")
            download.cancel()
            download.deleteLater()
            return

        download.setPath(f"/tmp/{Path(url_path).name or 'download'}")
        download.accept()

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
    # TAB MANAGEMENT (called from JS bridge)
    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    def _request_tab_sync(self) -> None:
        """Request a debounced tab sync (coalesces multiple requests)."""
        if not self._pending_tab_sync:
            self._pending_tab_sync = True
            self._tab_sync_timer.start(30)  # 30ms debounce

    def _sync_tabs_to_views(self) -> None:
        """Sync tab list to all WebEngine views' HTML tab bars.

        The template's tab strip shows the REAL Qt tabs: we rebuild its
        content from tab data on every sync. Markup matches the new
        design: .tab / .active-tab / .t / .close inside #tabStrip; the
        template wires clicks to bfsb://switchTab / bfsb://closeTab.
        """
        self._pending_tab_sync = False
        # Build tab data
        tabs_data = []
        home_url = self._server.home_url if self._server else "http://127.0.0.1:8889/"
        for i, view in enumerate(self._views):
            # Use tracked URL (works for inactive/new tabs), fallback to view.url()
            url = self._view_urls.get(view, view.url().toString())
            is_active = (i == self._stack.currentIndex())
            # Use better title for home page / data URLs - check against known home URL
            if url.startswith("data:") or url == "about:home" or url.startswith(home_url) or self._is_bfsb_url(url):
                title = "BFSB — New Tab"
            else:
                title = view.page().title() if view.page().title() else f"Tab {i+1}"
            tabs_data.append({"index": i, "title": title, "url": url, "active": is_active})

        self._push_tabs_js(tabs_data)

    def _push_tabs_js(self, tabs_data: list[dict]) -> None:
        """Push tab data into every BFSB page's #tabStrip."""
        tabs_json = json.dumps(tabs_data)
        for view in self._views:
            try:
                view.page().runJavaScript(f"""
                    (function() {{
                        var tabs = {tabs_json};
                        var strip = document.getElementById('tabStrip');
                        if (!strip) return;
                        window.__bfsbActiveIndex = -1;
                        var html = '';
                        for (var i = 0; i < tabs.length; i++) {{
                            var t = tabs[i];
                            if (t.active) window.__bfsbActiveIndex = i;
                            html += '<div class="tab' + (t.active ? ' active-tab' : '') +
                                    '" data-index="' + i + '" title="' +
                                    String(t.title).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;') +
                                    '"><span class="t">' +
                                    String(t.title).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;') +
                                    '</span><span class="close" data-index="' + i + '" title="Close tab">&#10005;</span></div>';
                        }}
                        strip.innerHTML = html;
                    }})();
                """)
            except Exception as e:
                print(f"[Window] Tab sync failed for view: {e}")

    def _sync_single_view(self, view: QWebEngineView, new_active_index: int = None, old_active_index: int = None) -> None:
        """Sync tab bar to a single WebEngine view."""
        try:
            tabs_data = []
            home_url = self._server.home_url if self._server else "http://127.0.0.1:8889/"
            for i, v in enumerate(self._views):
                # Use tracked URL (works for inactive/new tabs), fallback to view.url()
                url = self._view_urls.get(v, v.url().toString())
                # Check URL FIRST (works for inactive tabs), then page title as fallback
                if url.startswith("data:") or url == "about:home" or url.startswith(home_url) or self._is_bfsb_url(url):
                    page_title = "BFSB — New Tab"
                else:
                    page_title = v.page().title() if v.page().title() else f"Tab {i+1}"
                is_active = (i == self._stack.currentIndex())
                tabs_data.append({"index": i, "title": page_title, "url": url, "active": is_active})

            self._push_tabs_js(tabs_data)
        except Exception as e:
            print(f"[Window] Tab sync failed for view: {e}")

    def new_tab(self, url: Optional[str] = None) -> Optional[QWebEngineView]:
        """Create and add a new tab."""
        view = self._create_view()

        # Add to stack
        index = self._stack.addWidget(view)
        self._views.insert(index, view)

        # Connect loadFinished BEFORE loading content (race condition fix)
        def _on_load_finished(ok: bool):
            if ok:
                QTimer.singleShot(0, lambda: self._sync_single_view(view))
            view.loadFinished.disconnect(_on_load_finished)
        view.loadFinished.connect(_on_load_finished)

        # Load content FIRST (before switching)
        if url is None or url == "about:home":
            self._load_home(view)
        else:
            view.setUrl(QUrl(url))
            self._view_urls[view] = url

        # Sync EXISTING views immediately (they have loaded DOMs)
        for i, existing_view in enumerate(self._views):
            if i != index:  # Skip the new view
                self._sync_single_view(existing_view)

        # Switch to new tab
        self._stack.setCurrentIndex(index)
        self._sync_chrome_tabs()
        self._update_chrome_for_current()

        return view

    def close_tab(self, index: int) -> None:
        """Close tab at index."""
        if 0 <= index < len(self._views):
            # Get the view being closed and the new active index
            was_active = (index == self._stack.currentIndex())
            old_view = self._views.pop(index)
            self._stack.removeWidget(old_view)
            old_view.deleteLater()
            # Clean up tracked URL
            self._view_urls.pop(old_view, None)

            # If closed the current tab, switch to adjacent
            if self._views:
                new_index = min(index, len(self._views) - 1)
                new_active_view = self._views[new_index]

                # Sync ALL views to remove the tab from their tab bars
                self._sync_tabs_to_views()

                self._stack.setCurrentIndex(new_index)
            else:
                # No tabs left, create a new one - new_tab handles its own sync
                self.new_tab()
            self._sync_chrome_tabs()
            self._update_chrome_for_current()

    def switch_tab(self, index: int) -> None:
        """Switch to tab at index."""
        # PERF-DEBUG(phase1): tab-switch breakdown — remove after phase 1.
        from PyQt6.QtCore import QElapsedTimer
        _t = QElapsedTimer(); _t.start()
        if 0 <= index < len(self._views):
            old_index = self._stack.currentIndex()
            # Sync the target view's tab bar BEFORE switching (so it's ready when visible)
            target_view = self._views[index]
            self._sync_single_view(target_view, new_active_index=index, old_active_index=old_index)
            _t1 = _t.nsecsElapsed()
            # Now switch
            self._stack.setCurrentIndex(index)
            _t2 = _t.nsecsElapsed()
            self._sync_chrome_tabs()
            _t3 = _t.nsecsElapsed()
            self._update_chrome_for_current()
            _t4 = _t.nsecsElapsed()
            print(f"[PERF] switch_tab -> {index}: js_sync={_t1/1e6:.2f}ms "
                  f"stack_switch={(_t2-_t1)/1e6:.2f}ms chrome_tabs={(_t3-_t2)/1e6:.2f}ms "
                  f"chrome_update={(_t4-_t3)/1e6:.2f}ms total={_t4/1e6:.2f}ms", flush=True)

    def get_tab_count(self) -> int:
        """Return number of open tabs."""
        return len(self._views)

    def get_active_tab_index(self) -> int:
        """Return index of currently active tab."""
        return self._stack.currentIndex() if self._views else -1

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
    # NAVIGATION (called from JS bridge)
    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    def navigate(self, text: str) -> None:
        """Navigate to URL or trigger search via server."""
        text = text.strip()
        if not text:
            return

        # Handle BFSB internal URLs.
        # NOTE: QUrl normalizes the scheme host to lowercase and appends a
        # trailing slash (bfsb://newTab -> bfsb://newtab/), so every match
        # here must be case-insensitive and slash-tolerant.
        low = text.lower()
        if low.startswith("bfsb://about"):
            self._show_about_dialog()
            return
        elif low.startswith("bfsb://preferences"):
            self._show_preferences_dialog()
            return
        elif low.startswith("bfsb://goback"):
            self.go_back()
            return
        elif low.startswith("bfsb://goforward"):
            self.go_forward()
            return
        elif low.startswith("bfsb://reload"):
            self.reload()
            return
        elif low.startswith("bfsb://switchtab"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            if 'index' in query:
                self.switch_tab(int(query['index'][0]))
            return
        elif low.startswith("bfsb://closetab"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            if 'index' in query:
                self.close_tab(int(query['index'][0]))
            return
        elif low.startswith("bfsb://newtab"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            target = query['url'][0] if 'url' in query else None
            self.new_tab(target)
            return
        elif low.startswith("bfsb://navigate"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            if 'url' in query:
                self.navigate(query['url'][0])
            return
        elif low.startswith("bfsb://search"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            if 'q' in query:
                self._perform_search(query['q'][0])
            return
        elif low.startswith("bfsb://"):
            # Unknown bfsb:// scheme - ignore
            return
        elif text.startswith(("http://", "https://", "about:")):
            url = text
        elif text.startswith("/"):
            # Internal path (e.g., /search?q=...) - treat as direct URL to server
            url = "http://127.0.0.1:8889" + text
        elif "." in text and " " not in text and not text.startswith("?"):
            # Looks like a domain
            url = "https://" + text
        else:
            # It's a search query - navigate to server search
            self._perform_search(text)
            return

        view = self._get_current_view()
        if view:
            view.setUrl(QUrl(url))
            self._view_urls[view] = url

    def _perform_search(self, query: str) -> None:
        """Navigate to server search URL."""
        if not self._server_ready:
            return

        search_url = f"{self._server.search_url}?q={quote(query)}"
        view = self._get_current_view()
        if view:
            view.setUrl(QUrl(search_url))

    def _get_current_view(self) -> Optional[QWebEngineView]:
        """Get the currently active web view."""
        idx = self._stack.currentIndex()
        if 0 <= idx < len(self._views):
            return self._views[idx]
        return None

    def go_back(self) -> None:
        """Navigate back."""
        try:
            view = self._get_current_view()
            if view:
                view.back()
        except Exception as e:
            print(f"[Window] go_back error: {e}")

    def go_forward(self) -> None:
        """Navigate forward."""
        try:
            view = self._get_current_view()
            if view:
                view.forward()
        except Exception as e:
            print(f"[Window] go_forward error: {e}")

    def reload(self) -> None:
        """Reload current page."""
        try:
            view = self._get_current_view()
            if view:
                view.reload()
        except Exception as e:
            print(f"[Window] reload error: {e}")

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
    # WINDOW LIFECYCLE
    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    def closeEvent(self, event) -> None:
        """Clean up on window close."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(shutdown_server())
        except Exception:
            pass
        super().closeEvent(event)

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _short_title(url: str) -> str:
        """Generate short title from URL."""
        if url in ("about:home", "about:blank"):
            return "Home"
        try:
            host = QUrl(url).host()
            return host if host else url[:25]
        except Exception:
            return url[:25]

    @staticmethod
    def _short_title_from_text(text: str) -> str:
        """Generate short title from page title."""
        if not text:
            return "New Tab"
        if len(text) > 30:
            return text[:27] + "…"
        return text

    def _show_about_dialog(self) -> None:
        """Show the About BFSB dialog."""
        from .navigation import AboutDialog
        dialog = AboutDialog(self)
        dialog.exec()

    def _show_preferences_dialog(self) -> None:
        """Show the Preferences dialog."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QWidget, QPushButton
        from PyQt6.QtCore import Qt

        dialog = QDialog(self)
        dialog.setWindowTitle("Preferences")
        dialog.setFixedSize(500, 400)
        dialog.setModal(True)
        dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        dialog.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        container = QWidget(dialog)
        container.setObjectName("prefsDialog")
        container.setStyleSheet("""
            QWidget#prefsDialog {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1e1e32, stop:1 #18182a);
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 18px;
                outline: none;
            }
            QLabel {
                background: transparent;
                border: none;
                color: #eeeef8;
            }
            QPushButton {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
                color: #9090b8;
                font-size: 13px;
                font-weight: 500;
                padding: 8px 16px;
                min-width: 80px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.1);
                color: #eeeef8;
            }
            QPushButton:pressed {
                background: rgba(255, 255, 255, 0.15);
            }
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Preferences")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #eeeef8; letter-spacing: -0.02em;")
        layout.addWidget(title)

        subtitle = QLabel("BFSB Browser Settings")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("font-size: 13px; color: #7c6af5; font-weight: 500;")
        layout.addWidget(subtitle)

        layout.addSpacing(24)

        placeholder = QLabel("Preferences panel coming soon...")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("font-size: 14px; color: #7a7a8a; padding: 40px;")
        layout.addWidget(placeholder)

        layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(0, 0, 0, 0)
        dialog_layout.addWidget(container)

        dialog.exec()

    def _load_home(self, view: Optional[QWebEngineView] = None) -> None:
        """Load the home GUI into a view.

        Renders the template directly (no per-call asyncio event loop —
        the server thread owns the running loop). If the server hasn't
        finished starting, an instant placeholder is shown and the real
        GUI loads automatically once it's up.
        """
        target = view if view is not None else (self._views[-1] if self._views else None)
        if target is None:
            return
        if not self._server_ready or self._server is None:
            target.setHtml(self._STARTING_HTML, QUrl("http://127.0.0.1:8889/"))
            self._view_urls[target] = "about:home"
            QTimer.singleShot(250, lambda: self._retry_load_home(target))
            return
        try:
            html = self._server.jinja_env.get_template("bfsb_combined.html").render(
                PAGE_TYPE="home",
                HOME_BOOKMARKS_HTML=self._server._home_bookmarks_html(),
                BOOKMARKS_JSON=self._server._bookmarks_json(),
            )
            # Set HTML WITH base URL so view.url() returns home URL immediately
            target.setHtml(html, QUrl(self._server.home_url))
            self._view_urls[target] = self._server.home_url
        except Exception:
            target.setUrl(QUrl(self._server.home_url))
            self._view_urls[target] = self._server.home_url

    def _retry_load_home(self, view: QWebEngineView) -> None:
        """Load home into a placeholder tab once the server is ready."""
        if view not in self._views:
            return
        if self._view_urls.get(view) != "about:home":
            return  # the user already navigated somewhere else
        if self._server_ready:
            self._load_home(view)
        else:
            QTimer.singleShot(250, lambda: self._retry_load_home(view))
