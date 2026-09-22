"""Main application window — BFSB Browser (multi-tab, server-backed)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional
from urllib.parse import quote
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QStackedWidget,
    QStatusBar,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

from .styles import DIMS, C

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
    RequestInterceptor,
)
from ..core.storage import BookmarkStore, HistoryStore


class BFSBWindow(QMainWindow):
    """Main browser window — multi-tab, server-backed."""

    MAX_TABS = 8  # Limit tabs to prevent memory exhaustion

    def __init__(self, blocker: URLBlocker) -> None:
        super().__init__()
        self._blocker = blocker
        self._cookie_vault = CookieVault()

        # Server
        self._server: Optional[BFSHBServer] = None
        self._server_ready = False

        # Storage (bookmarks, history, downloads) — initialize BEFORE
        # `_init_ui` because the side panel needs the download manager
        # at construction time, and the menu/popovers need the stores.
        from .downloads import DownloadManager
        self._bookmarks = BookmarkStore()
        self._history = HistoryStore()
        self._downloads = DownloadManager(self)

        # The active BFSBMenu instance (set in `_show_main_menu`,
        # cleared when it closes). Used by the /test-state endpoint
        # to report whether the menu is currently visible, and by
        # `_test_hover_*` to know which menu's hover handler to drive.
        self._active_menu: Optional[object] = None

        # UI state
        self._profile = None
        self._stack: Optional[QStackedWidget] = None
        self._chrome = None
        self._side_panel = None
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
        # Show the window IMMEDIATELY so the loading screen is the first
        # thing the user sees when clicking the launcher icon. The first
        # tab renders bfsb_loading.html (server isn't up yet) and swaps
        # to the real home page once the services are healthy.
        self.show()
        self.new_tab()
        self._init_server()
        self._wait_server_then_home()
        self._initialized = True

    def _wait_server_then_home(self) -> None:
        """Poll (non-blocking, event-loop friendly) until BOTH services are
        healthy (local BFSB server + SearXNG), then replace the loading
        screen with the real home page. Runs on the GUI thread via QTimer
        so the loading animation keeps animating while we wait.

        This is also the safety net: even if the loading page's own JS
        fails to redirect, the home page still appears once services
        are up.
        """
        if self._server_ready and getattr(self, "_searxng_ready", False) and self._views:
            self._load_home(self._views[0])
            return
        QTimer.singleShot(200, self._wait_server_then_home)

    def _monitor_services(self) -> None:
        """Background thread: wait for the BFSB server, then poll SearXNG
        health until it responds. Sets flags consumed by
        ``_wait_server_then_home``. Never blocks the GUI thread."""
        import time
        import urllib.request
        # Wait for the local server flag first (set by the server thread).
        while not self._server_ready:
            time.sleep(0.1)
        while True:
            try:
                with urllib.request.urlopen("http://127.0.0.1:8888/healthz", timeout=1) as r:
                    if r.status == 200:
                        self._searxng_ready = True
                        print("[BFSBWindow] SearXNG health check OK")
                        return
            except Exception:
                pass
            time.sleep(0.5)

    def _init_profile(self) -> None:
        """Initialize WebEngine profile."""
        from ..core import create_web_profile, configure_web_settings
        self._profile = create_web_profile()
        configure_web_settings(self._profile.settings())
        # Hold a reference to the interceptor — Qt does NOT take ownership
        # in the C++ binding; if it goes out of scope Python GC will
        # collect the C++ object and the next request will segfault.
        self._interceptor = RequestInterceptor(self._blocker)
        self._profile.setUrlRequestInterceptor(self._interceptor)
        self._profile.downloadRequested.connect(self._on_download)

    def _init_server(self) -> None:
        """Initialize and start the local HTTP server."""
        import threading

        def run_server():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                self._server = loop.run_until_complete(get_server())
                self._server_ready = True
                # Register the main window with the server so the
                # /test-action/* route (only registered when
                # BFSB_TEST=1) can dispatch into Qt methods. This
                # gives the hermes harness a reliable way to drive
                # new_tab / close_tab / _toggle_side_panel without
                # going through ydotool (which is unreliable under Xvfb).
                if os.environ.get("BFSB_TEST") == "1":
                    self._server._main_window = self
                print("[BFSBWindow] Server ready at", self._server.base_url)
                loop.run_forever()
            except Exception as e:
                print(f"[BFSBWindow] Failed to start server: {e}")
            finally:
                loop.close()

        self._server_thread = threading.Thread(target=run_server, daemon=True)
        self._server_thread.start()

        # Background monitor for SearXNG health (never blocks the GUI).
        self._searxng_ready = False
        threading.Thread(target=self._monitor_services, daemon=True).start()

        # NOTE: no blocking wait here. `_wait_server_then_home` polls the
        # `_server_ready` flag on the GUI thread via QTimer, so the loading
        # screen keeps animating while the server comes up.

    def _init_ui(self) -> None:
        """Initialize main UI — chrome + stacked widget for tabs + side panel."""
        self.setWindowTitle(APP_CONFIG.WINDOW_TITLE)
        self.resize(APP_CONFIG.WINDOW_WIDTH, APP_CONFIG.WINDOW_HEIGHT)

        # Central widget = vertical layout: BrowserChrome on top, QStackedWidget below.
        # The native chrome is hidden: the in-page design (tabs + address bar
        # in bfsb_combined.html) owns the visible browser UI. The chrome
        # object stays alive (signals/shortcuts/tests still drive through it).
        from .browser_chrome import BrowserChrome
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        self._chrome = BrowserChrome()
        self._chrome.tab_switched.connect(self.switch_tab)
        self._chrome.tab_closed.connect(self.close_tab)
        self._chrome.new_tab_requested.connect(self.new_tab)
        self._chrome.back_clicked.connect(self.go_back)
        self._chrome.forward_clicked.connect(self.go_forward)
        self._chrome.reload_clicked.connect(self.reload)
        self._chrome.url_submitted.connect(self.navigate)
        self._chrome.menu_clicked.connect(self._show_main_menu)
        self._chrome.bookmark_toggle.connect(self._toggle_bookmark)
        central_layout.addWidget(self._chrome)
        # In-page chrome (bfsb_combined.html) is the visible UI; keep the
        # native chrome instantiated but hidden so all wiring keeps working.
        self._chrome.setVisible(False)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background: {C.BG_0}; border: none;")
        central_layout.addWidget(self._stack, stretch=1)

        self.setCentralWidget(central)

        # Right-side dock: Downloads-only panel.
        from .side_panel import SidePanel
        self._side_panel = SidePanel(
            self._downloads,
            parent=self,
            bookmark_store=self._bookmarks,
            history_store=self._history,
        )
        self._side_panel.setObjectName("downloads_panel")
        self._side_panel.installEventFilter(self)
        self._side_panel.open_url.connect(self._open_in_active_tab)
        self._side_panel.hide()
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._side_panel)

        # Status bar
        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet(f"""
            QStatusBar {{
                background: {C.BG_2};
                color: {C.TEXT_2};
                font-size: {DIMS.STATUS_FONT_SIZE}px;
                border-top: 1px solid {C.BORDER_0};
                padding: 0 {8}px;
            }}
        """)
        self.setStatusBar(self._status_bar)
        self._update_status()

        # Wire up keyboard shortcuts (Ctrl+T, Ctrl+W, Ctrl+B, etc).
        self._setup_window_actions()

    def _update_status(self) -> None:
        """Update status bar message."""
        self._status_bar.showMessage(
            f"Ready | {self._blocker.blocked_count:,} threats blocked"
        )

    # ── keyboard shortcuts ───────────────────────────────────────────

    def _setup_window_actions(self) -> None:
        """Bind window-level keyboard shortcuts.

        Chrome-style: Ctrl+T new tab, Ctrl+W close tab, Ctrl+R reload,
        Ctrl+B toggle sidebar, Ctrl+L focus address bar, Ctrl+Q quit.
        These are window-scope shortcuts (not QAction-shortcut) so they
        work regardless of which child widget has focus.
        """
        shortcuts = {
            "Ctrl+T": self.new_tab,
            "Ctrl+W": self._close_active_tab,
            "Ctrl+R": self.reload,
            "Ctrl+B": self._toggle_side_panel,
            "Ctrl+J": self._toggle_side_panel,  # mirror Chrome: open downloads
            "Ctrl+L": self._focus_url_bar,
            "Ctrl+Q": self.close,
            "Ctrl+D": self._toggle_bookmark,
            "Ctrl+Shift+Del": self._clear_browsing_data,
            "F5": self.reload,
        }
        self._shortcuts: list[QShortcut] = []
        for key, slot in shortcuts.items():
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(slot)
            self._shortcuts.append(sc)

    def _close_active_tab(self) -> None:
        idx = self._stack.currentIndex() if self._stack else -1
        if idx >= 0 and len(self._views) > 1:
            self.close_tab(idx)

    def _focus_url_bar(self) -> None:
        # Native chrome is hidden — focus the in-page search box instead.
        try:
            view = self._get_current_view()
            if view is not None:
                view.page().runJavaScript(
                    "(function(){var i=document.getElementById('searchInput');"
                    "if(i){i.focus();try{i.select();}catch(e){}}})();"
                )
        except Exception:
            pass

    def _toggle_side_panel(self) -> None:
        if self._side_panel is None:
            return
        if self._side_panel.isVisible():
            self._side_panel.hide()
        else:
            self._side_panel.show()
            self._side_panel.raise_()

    def _show_side_panel_tab(self, which: str) -> None:
        """Open the sidebar with a specific tab active.

        ``which`` is one of "downloads", "history", "bookmarks" — passed
        straight to ``SidePanel.select_tab``. Used by the ⋮ menu's
        Downloads/History/Bookmarks rows so all three share the same
        sidebar instead of History/Bookmarks popping a separate hover
        popover.
        """
        if self._side_panel is None:
            return
        self._side_panel.select_tab(which)
        self._side_panel.show()
        self._side_panel.raise_()

    def _toggle_bookmark(self) -> None:
        """Toggle bookmark on the active tab's URL."""
        view = self._get_current_view()
        if view is None:
            return
        url = view.url().toString()
        title = view.page().title() or url
        existing = self._bookmarks.get(url)
        if existing:
            self._bookmarks.remove(url)
        else:
            self._bookmarks.add(url=url, title=title)
        # Update the star visual on the nav bar.
        if self._chrome is not None:
            self._chrome.set_bookmarked(bool(existing) is False)
        if self._side_panel is not None:
            self._side_panel.refresh_bookmarks()

    def showEvent(self, event) -> None:
        """Ensure window is raised and activated on Wayland/XWayland."""
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setWindowState(self.windowState() | Qt.WindowState.WindowActive)

    def _create_view(self) -> QWebEngineView:
        """Create a new web view."""
        view = create_web_view(self._profile, self._blocker, page_class=BFSBPage, window=self)
        view.page()._main_window = self
        # Fallback injection path: also register the nuclear script directly
        # on this page's script collection (profile-level registration stays).
        try:
            from ..core.tampermonkey_scripts import inject_page_scripts, log_diag
            inject_page_scripts(view.page())
            log_diag("[Window] page-level nuclear script registered")
        except Exception as e:
            from ..core.tampermonkey_scripts import log_diag
            log_diag(f"[Window] page-level script injection failed: {e}")
        # Native tab bar title update — fires on every title change in
        # the page (initial load, SPA navigation, history pushState).
        def _on_title_changed(title: str) -> None:
            idx = self._views.index(view) if view in self._views else -1
            if idx >= 0 and self._chrome is not None:
                self._chrome.update_tab_title(idx, title or "New Tab")
        view.page().titleChanged.connect(_on_title_changed)
        # Progress bar — show indeterminate-style animation on load
        # start, advance to real percent on progress events, complete
        # on loadFinished. Wired to the chrome's NativeProgressBar.
        def _on_load_started() -> None:
            if self._chrome is not None:
                self._chrome.start_progress()
        def _on_load_progress(pct: int) -> None:
            if self._chrome is not None:
                self._chrome.set_progress(pct)
        def _on_load_finished_for_chrome(ok: bool) -> None:
            if self._chrome is not None:
                self._chrome.complete_progress()
            # Definitive nuclear-injection diagnostic (MainWorld query):
            # logs nuclear=<injected|undefined>|<cosmetic-css-present>
            try:
                u = view.url().toString()
                if "youtube.com" in u:
                    from ..core.tampermonkey_scripts import log_diag
                    view.page().runJavaScript(
                        "(function(){try{"
                        "return String(typeof window.__bfsbNuclearInjected)+'|'+"
                        "String(typeof window.__bfsbCosmeticReady!=='undefined')+'|'+"
                        "String(!!(window.fetch&&String(window.fetch).indexOf('[native code]')===-1))+'|'+"
                        "String(String(JSON.parse).indexOf('originalJSONParse')!==-1)+'|'+"
                        "String(Function.prototype.toString.call(window.fetch).indexOf('[native code]')!==-1)"
                        "}catch(e){return 'err:'+e}})()",
                        0,
                        lambda r, _u=u: log_diag(f"[Diag] {_u[:70]} nuclear={r}")
                    )
            except Exception:
                pass
        view.page().loadStarted.connect(_on_load_started)
        view.page().loadProgress.connect(_on_load_progress)
        view.loadFinished.connect(_on_load_finished_for_chrome)
        return view

    def _load_home(self, view: Optional[QWebEngineView] = None) -> None:
        """Load home page (search engine) into view."""
        target = view or self._views[-1] if self._views else None
        if not target:
            return
        if not self._server_ready or self._server is None:
            # Show the BFSB loading screen while the local server (and
            # SearXNG) come up. Its JS polls both health endpoints and
            # redirects to the home URL once everything is ready.
            #
            # We load the template from disk via file:// instead of
            # setHtml(): setHtml on a not-yet-shown view can silently
            # fail / flash, while a real file URL always loads. The
            # fetch() calls still work because the server sends
            # Access-Control-Allow-Origin: * (file origin "null" is
            # accepted by the wildcard).
            try:
                tpl_path = Path(__file__).parent.parent / "templates" / "bfsb_loading.html"
                target.load(QUrl.fromLocalFile(str(tpl_path)))
            except Exception:
                html = ("<html><body style='background:#0a0a12;color:#f0f0f5;"
                        "font-family:sans-serif;padding:2rem;text-align:center;'>"
                        "<h1>BFSB</h1><p>Loading services...</p></body></html>")
                target.setHtml(html)
            self._view_urls[target] = "about:home"
        else:
            from ..core import get_server
            import asyncio
            loop = asyncio.new_event_loop()
            try:
                server = loop.run_until_complete(get_server())
                template = server.jinja_env.get_template('bfsb_combined.html')
                html = template.render(PAGE_TYPE='home')
                # Set HTML WITH base URL so view.url() returns home URL immediately
                target.setHtml(html, QUrl(self._server.home_url))
                self._view_urls[target] = self._server.home_url
            except Exception:
                target.setUrl(QUrl(self._server.home_url))
                self._view_urls[target] = self._server.home_url
            finally:
                loop.close()

    def _load_site(self, url: str, view: Optional[QWebEngineView] = None) -> None:
        """Render the BFSB shell and load an external site inside its iframe."""
        target = view or self._get_current_view()
        if not target:
            return
        if not self._server_ready or self._server is None:
            self._load_home(target)
            return

        from ..core import get_server
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            server = loop.run_until_complete(get_server())
            template = server.jinja_env.get_template("bfsb_combined.html")
            html = template.render(
                PAGE_TYPE="site",
                PAGE_URL=url,
                BOOKMARKS_JSON="[]",
            )
            target.setHtml(html, QUrl(self._server.base_url))
            self._view_urls[target] = url
        except Exception as exc:
            print(f"[Window] Failed to render site shell: {exc}")
            self._load_home(target)
        finally:
            loop.close()

    def _on_download(self, download) -> None:
        """Handle download requests — security hardened with confirm dialog.

        - Files matching ``SECURITY_CONFIG.BAD_EXTENSIONS`` (or with no
          extension at all) are blocked by default; the user can opt to
          continue via a destructive confirm dialog.
        - All accepted downloads are saved to ``~/Downloads`` via
          ``DownloadManager`` (XDG-compliant), which the user can
          manage from the side panel.
        """
        url_path = download.url().path()
        ext = Path(url_path).suffix.lower()
        suggested_name = download.suggestedFileName() or Path(url_path).name or "download"

        if ext in SECURITY_CONFIG.BAD_EXTENSIONS or not ext:
            from .confirm_dialog import ConfirmDialog
            warn = "executable" if ext in SECURITY_CONFIG.BAD_EXTENSIONS else "file with no extension"
            if not ConfirmDialog.ask_destructive(
                self,
                f"Download a {warn}?",
                f"{suggested_name} could run code on your computer.",
                detail=(
                    f"BFSB will save it to {self._downloads.downloads_dir()} but "
                    f"will not run it. Only continue if you trust the source."
                ),
                confirm_label="Download anyway",
                cancel_label="Cancel",
            ):
                download.cancel()
                download.deleteLater()
                return
            # User confirmed — fall through to the normal accept path.

        # Hand off to the manager: it picks a non-clobbering path in
        # ~/Downloads, calls accept() on the QWebEngineDownloadItem,
        # and starts tracking progress for the side panel.
        self._downloads.add(download)

    # ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════
    # TAB MANAGEMENT (called from JS bridge)
    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    def _request_tab_sync(self) -> None:
        """Request a debounced tab sync (coalesces multiple requests)."""
        if not self._pending_tab_sync:
            self._pending_tab_sync = True
            self._tab_sync_timer.start(30)  # 30ms debounce

    def _sync_tabs_to_views(self) -> None:
        """Sync tab list to all WebEngine views' HTML tab bars - incremental updates."""
        self._pending_tab_sync = False
        # Build tab data
        tabs_data = []
        home_url = self._server.home_url if self._server else "http://127.0.0.1:8889/"
        for i, view in enumerate(self._views):
            # Use tracked URL (works for inactive/new tabs), fallback to view.url()
            url = self._view_urls.get(view, view.url().toString())
            is_active = (i == self._stack.currentIndex())
            # Use better title for home page / data URLs - check against known home URL
            if url.startswith("data:") or url == "about:home" or url.startswith(home_url):
                title = "BFSB — Browser For Safe Browsing"
            else:
                title = view.page().title() if view.page().title() else f"Tab {i+1}"
            tabs_data.append({"index": i, "title": title, "url": url, "active": is_active})

        # Push to all views via runJavaScript - incremental DOM updates
        tabs_json = json.dumps(tabs_data)
        for view in self._views:
            try:
                view.page().runJavaScript(f"""
                    (function() {{
                        var tabs = {tabs_json};
                        var tabBar = document.querySelector('.bfsb-tab-bar');
                        if (!tabBar) return;

                        var newTabBtn = document.getElementById('newTabBtn');
                        var existingTabs = Array.from(tabBar.querySelectorAll('.bfsb-tab'));

                        // Update existing tabs in place where possible
                        var maxLen = Math.max(existingTabs.length, tabs.length);
                        for (var i = 0; i < maxLen; i++) {{
                            var existingTab = existingTabs[i];
                            var tabData = tabs[i];

                            if (tabData && existingTab) {{
                                // Update existing tab
                                var wasActive = existingTab.classList.contains('active');
                                var shouldBeActive = tabData.active;
                                if (wasActive !== shouldBeActive) {{
                                    existingTab.classList.toggle('active', shouldBeActive);
                                    existingTab.setAttribute('aria-selected', shouldBeActive ? 'true' : 'false');
                                }}
                                // Update title
                                var titleEl = existingTab.querySelector('.bfsb-tab-title');
                                if (titleEl && titleEl.textContent !== tabData.title) {{
                                    titleEl.textContent = tabData.title;
                                }}
                            }} else if (tabData && !existingTab) {{
                                // Add new tab
                                var tabEl = document.createElement('div');
                                tabEl.className = 'bfsb-tab' + (tabData.active ? ' active' : '');
                                tabEl.setAttribute('role', 'tab');
                                tabEl.setAttribute('aria-selected', tabData.active ? 'true' : 'false');
                                tabEl.innerHTML =
                                    '<span class="bfsb-tab-favicon"></span>' +
                                    '<span class="bfsb-tab-title">' + tabData.title + '</span>' +
                                    '<button class="bfsb-tab-close" aria-label="Close tab">×</button>';
                                if (newTabBtn) {{
                                    tabBar.insertBefore(tabEl, newTabBtn);
                                }} else {{
                                    tabBar.appendChild(tabEl);
                                }}
                            }} else if (!tabData && existingTab) {{
                                // Remove extra tab
                                existingTab.remove();
                            }}
                        }}
                    }})();
                """)
            except Exception as e:
                print(f"[Window] Tab sync failed for view: {e}")

    def _sync_single_view(self, view: QWebEngineView, new_active_index: int = None, old_active_index: int = None) -> None:
        """Sync tab bar to a single WebEngine view - minimal update for instant tab switching."""
        try:
            if new_active_index is not None and old_active_index is not None and new_active_index != old_active_index:
                # Minimal: only toggle active class on the two tabs that changed
                # Use querySelectorAll to get only .bfsb-tab elements (not newTabBtn)
                view.page().runJavaScript(f"""
                    (function() {{
                        var tabBar = document.querySelector('.bfsb-tab-bar');
                        if (!tabBar) return;
                        var tabs = tabBar.querySelectorAll('.bfsb-tab');
                        var oldTab = tabs[{old_active_index}];
                        var newTab = tabs[{new_active_index}];
                        if (oldTab) oldTab.classList.remove('active');
                        if (newTab) newTab.classList.add('active');
                        if (oldTab) oldTab.setAttribute('aria-selected', 'false');
                        if (newTab) newTab.setAttribute('aria-selected', 'true');
                    }})();
                """)
            else:
                # Full sync for new tab / close tab cases
                tabs_data = []
                home_url = self._server.home_url if self._server else "http://127.0.0.1:8889/"
                for i, v in enumerate(self._views):
                    # Use tracked URL (works for inactive/new tabs), fallback to view.url()
                    url = self._view_urls.get(v, v.url().toString())
                    # Check URL FIRST (works for inactive tabs), then page title as fallback
                    if url.startswith("data:") or url == "about:home" or url.startswith(home_url):
                        page_title = "BFSB — Browser For Safe Browsing"
                    else:
                        page_title = v.page().title() if v.page().title() else f"Tab {i+1}"
                    is_active = (i == self._stack.currentIndex())
                    tabs_data.append({"index": i, "title": page_title, "url": url, "active": is_active})

                tabs_json = json.dumps(tabs_data)
                view.page().runJavaScript(f"""
                    (function() {{
                        var tabs = {tabs_json};
                        var tabBar = document.querySelector('.bfsb-tab-bar');
                        if (!tabBar) return;
                        var newTabBtn = document.getElementById('newTabBtn');
                        var existingTabs = Array.from(tabBar.querySelectorAll('.bfsb-tab'));
                        var maxLen = Math.max(existingTabs.length, tabs.length);
                        for (var i = 0; i < maxLen; i++) {{
                            var existingTab = existingTabs[i];
                            var tabData = tabs[i];
                            if (tabData && existingTab) {{
                                var wasActive = existingTab.classList.contains('active');
                                var shouldBeActive = tabData.active;
                                if (wasActive !== shouldBeActive) {{
                                    existingTab.classList.toggle('active', shouldBeActive);
                                    existingTab.setAttribute('aria-selected', shouldBeActive ? 'true' : 'false');
                                }}
                                var titleEl = existingTab.querySelector('.bfsb-tab-title');
                                if (titleEl && titleEl.textContent !== tabData.title) {{
                                    titleEl.textContent = tabData.title;
                                }}
                            }} else if (tabData && !existingTab) {{
                                var tabEl = document.createElement('div');
                                tabEl.className = 'bfsb-tab' + (tabData.active ? ' active' : '');
                                tabEl.setAttribute('role', 'tab');
                                tabEl.setAttribute('aria-selected', tabData.active ? 'true' : 'false');
                                tabEl.innerHTML = '<span class="bfsb-tab-favicon"></span>' + '<span class="bfsb-tab-title">' + tabData.title + '</span>' + '<button class="bfsb-tab-close" aria-label="Close tab">×</button>';
                                if (newTabBtn) {{ tabBar.insertBefore(tabEl, newTabBtn); }} else {{ tabBar.appendChild(tabEl); }}
                            }} else if (!tabData && existingTab) {{
                                existingTab.remove();
                            }}
                        }}
                    }})();
                """)
        except Exception as e:
            print(f"[Window] Tab sync failed for view: {e}")

    def new_tab(self, url: Optional[str] = None) -> Optional[QWebEngineView]:
        """Create and add a new tab."""
        # Enforce tab limit to prevent memory exhaustion
        if len(self._views) >= self.MAX_TABS:
            if self._chrome is not None:
                self._chrome.status_label.setText(f"Tab limit ({self.MAX_TABS}) reached")
                QTimer.singleShot(2000, lambda: self._chrome.status_label.setText(""))
            return None

        if os.environ.get("BFSB_TEST") == "1":
            print(f"[BFSB_TEST] new_tab called — view count now {len(self._views) + 1}", flush=True)
        view = self._create_view()

        # Add to stack
        index = self._stack.addWidget(view)
        self._views.insert(index, view)

        # Connect loadFinished BEFORE loading content (race condition fix)
        def _on_load_finished(ok: bool):
            if ok:
                QTimer.singleShot(0, lambda: self._sync_single_view(view))
                # Native tab bar title update — once we know the page title.
                idx = self._views.index(view) if view in self._views else -1
                if idx >= 0 and self._chrome is not None:
                    self._chrome.update_tab_title(idx, view.page().title() or "New Tab")
            view.loadFinished.disconnect(_on_load_finished)
        view.loadFinished.connect(_on_load_finished)

        # Load content FIRST (before switching)
        if url is None or url == "about:home":
            self._load_home(view)
        else:
            self._load_site(url, view)

        # Sync EXISTING views immediately (they have loaded DOMs)
        for i, existing_view in enumerate(self._views):
            if i != index:  # Skip the new view
                self._sync_single_view(existing_view)

        # Push into the native tab bar (BrowserChrome). add_tab() also
        # sets the new tab as active. We do this AFTER the view is in
        # the stack so the index is stable.
        if self._chrome is not None:
            self._chrome.tab_bar.add_tab(index, "New Tab")
            self._chrome.set_active_tab(index)

        # Switch to new tab
        self._stack.setCurrentIndex(index)
        self._update_status()

        return view

    def close_tab(self, index: int) -> None:
        """Close tab at index."""
        if os.environ.get("BFSB_TEST") == "1":
            print(f"[BFSB_TEST] close_tab called index={index} view count now {len(self._views) - 1 if 0 <= index < len(self._views) else len(self._views)}", flush=True)
        if 0 <= index < len(self._views):
            # Get the view being closed and the new active index
            was_active = (index == self._stack.currentIndex())
            old_view = self._views.pop(index)
            self._stack.removeWidget(old_view)
            # Properly destroy the view and its page to release resources
            old_page = old_view.page()
            old_view.setPage(None)  # Detach page from view
            if old_page:
                old_page.deleteLater()
            old_view.deleteLater()
            # Clean up tracked URL
            self._view_urls.pop(old_view, None)

            # Remove from the native tab bar — its internal remap keeps
            # the remaining tab indices in sync with self._views.
            if self._chrome is not None:
                self._chrome.tab_bar.remove_tab(index)

            # If closed the current tab, switch to adjacent
            if self._views:
                new_index = min(index, len(self._views) - 1)
                new_active_view = self._views[new_index]

                # Sync ALL views to remove the tab from their tab bars
                self._sync_tabs_to_views()

                self._stack.setCurrentIndex(new_index)
                if self._chrome is not None:
                    self._chrome.set_active_tab(new_index)
                self._update_status()
            else:
                # No tabs left, create a new one - new_tab handles its own sync
                self.new_tab()

    def switch_tab(self, index: int) -> None:
        """Switch to tab at index."""
        if 0 <= index < len(self._views):
            old_index = self._stack.currentIndex()
            # Sync the target view's tab bar BEFORE switching (so it's ready when visible)
            target_view = self._views[index]
            self._sync_single_view(target_view, new_active_index=index, old_active_index=old_index)
            # Now switch
            self._stack.setCurrentIndex(index)
            # Update the native tab bar highlight.
            if self._chrome is not None:
                self._chrome.set_active_tab(index)
            self._update_status()

    def get_tab_count(self) -> int:
        """Return number of open tabs."""
        return len(self._views)

    def get_active_tab_index(self) -> int:
        """Return index of currently active tab."""
        return self._stack.currentIndex() if self._views else -1

    # ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════
    # NAVIGATION (called from JS bridge)
    # ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    def navigate(self, text: str) -> None:
        """Navigate to URL or trigger search via server."""
        text = text.strip()
        if not text:
            return

        # Handle BFSB internal URLs
        if text.startswith("bfsb://about"):
            self._show_about_dialog()
            return
        elif text.startswith("bfsb://preferences"):
            self._show_preferences_dialog()
            return
        elif text.startswith("bfsb://goBack"):
            self.go_back()
            return
        elif text.startswith("bfsb://goForward"):
            self.go_forward()
            return
        elif text.startswith("bfsb://reload"):
            self.reload()
            return
        elif text.startswith("bfsb://switchTab"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            if 'index' in query:
                self.switch_tab(int(query['index'][0]))
            return
        elif text.startswith("bfsb://closeTab"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            if 'index' in query:
                self.close_tab(int(query['index'][0]))
            return
        elif text.startswith("bfsb://newTab"):
            self.new_tab()
            return
        elif text.startswith("bfsb://navigate"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            if 'url' in query:
                self.navigate(query['url'][0])
            return
        elif text.startswith("bfsb://search"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            if 'q' in query:
                self._perform_search(query['q'][0])
            return
        elif text.startswith("bfsb://"):
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
            if url.startswith(("http://", "https://")):
                self._load_site(url, view)
            else:
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
    # ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    def closeEvent(self, event) -> None:
        """Clean up on window close."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(shutdown_server())
        except Exception:
            pass
        super().closeEvent(event)

    # ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════
    # MAIN MENU
    # ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    def _show_main_menu(self) -> None:
        """Show the ⋮ dropdown menu anchored at the menu button.

        Called from the BrowserChrome's menu button, and from the page
        right-click via ``BFSBPage.contextMenuEvent``. The menu has flat
        sections (no nested submenus); Downloads, History, and Bookmarks
        all open the shared sidebar on the matching tab.
        """
        from .menu import BFSBMenu
        from PyQt6.QtGui import QCursor
        if os.environ.get("BFSB_TEST") == "1":
            print("[BFSB_TEST] _show_main_menu: enter", flush=True)

        menu = BFSBMenu(self)

        # ── Navigation section ─────────────────────────────────────
        menu.add_section("Navigation")
        menu.add_item("＋", "New tab", "Ctrl+T", on_triggered=self.new_tab)
        menu.add_item("▤", "Sidebar", "Ctrl+B", on_triggered=self._toggle_side_panel)
        menu.add_item("⏱", "History", "", on_triggered=lambda: self._show_side_panel_tab("history"))
        menu.add_item("★", "Bookmarks and lists", "", on_triggered=lambda: self._show_side_panel_tab("bookmarks"))
        menu.add_item("⬇", "Downloads", "Ctrl+J", on_triggered=lambda: self._show_side_panel_tab("downloads"))

        menu.add_separator()

        menu.add_section("Privacy")
        try:
            from ..core.adblock_state import is_adblock_enabled
            _ab_on = is_adblock_enabled()
            menu.add_item("◉" if _ab_on else "○", "Ad blocker: " + ("On" if _ab_on else "Off"), 
                          "", on_triggered=lambda: self._on_adblock_toggled(not _ab_on))
        except Exception:
            pass

        # ── Data section ───────────────────────────────────────────
        menu.add_section("Data")
        menu.add_item("⌫", "Clear browsing data", "Ctrl+Shift+Del", on_triggered=self._clear_browsing_data)

        # ── App section ────────────────────────────────────────────
        menu.add_separator()
        menu.add_item("?", "Help", "", on_triggered=self._show_about_dialog)
        menu.add_item("⚙", "Settings", "", on_triggered=self._show_preferences_dialog)
        menu.add_item("⎋", "Exit", "Ctrl+Q", on_triggered=self.close)

        # Position below the menu button on the chrome, with a small
        # margin so it doesn't touch the button. The native chrome is
        # hidden (in-page UI owns the pixels), so fall back to cursor.
        anchor = None
        if self._chrome is not None and self._chrome.isVisible():
            anchor = self._chrome.menu_button()
        if anchor is not None:
            g = anchor.mapToGlobal(anchor.rect().bottomLeft())
            menu.popup(g)
        else:
            menu.popup(QCursor.pos())

        # Track the live menu instance so /test-state can report
        # whether it's visible, and so the harness can drive
        # `_test_hover_*` without ydotool.
        self._active_menu = menu
        menu.aboutToHide.connect(self._clear_active_menu)
        if os.environ.get("BFSB_TEST") == "1":
            print("[BFSB_TEST] _show_main_menu: exit", flush=True)

    def _clear_active_menu(self) -> None:
        """Drop the active-menu reference when the menu closes.

        Connected to ``BFSBMenu.aboutToHide`` so /test-state can
        report ``menu_open=false`` once the menu hides.
        """
        self._active_menu = None

    def _test_hover_bookmarks(self) -> None:
        """Harness hook: open the sidebar on the Bookmarks tab.

        Kept as a named method (rather than removed outright) since a
        test harness may dispatch to it by name. Bookmarks no longer
        has a separate hover popover — hovering and clicking both just
        open the shared sidebar, so this opens it directly.
        """
        self._show_side_panel_tab("bookmarks")

    def _test_hover_history(self) -> None:
        """Harness hook: open the sidebar on the History tab. See
        ``_test_hover_bookmarks`` — History works the same way."""
        self._show_side_panel_tab("history")

    def _test_close_popovers(self) -> None:
        """No-op kept for compatibility.

        There are no hover popovers left to close — Bookmarks and
        History live in the sidebar now. Left in place (rather than
        removed) in case a test harness calls it by name between
        checks.
        """
        pass

    def _open_in_active_tab(self, url: str) -> None:
        """Open a URL in the active tab (used by sidebar row activation)."""
        if not url:
            return
        view = self._get_current_view()
        if view is None:
            self.new_tab(url)
            return
        view.setUrl(QUrl(url))
        self._view_urls[view] = url

    def _clear_history(self) -> None:
        """Clear all history (called by the sidebar's Clear-history action)."""
        from .confirm_dialog import ConfirmDialog
        count = self._history.count()
        if count == 0:
            return
        if ConfirmDialog.ask_destructive(
            self,
            "Clear all history?",
            f"Delete all {count} entries from your browsing history?",
            detail="This cannot be undone. Bookmarks are kept.",
            confirm_label="Clear all",
            cancel_label="Keep",
        ):
            self._history.clear()
            if self._side_panel is not None:
                self._side_panel.refresh_history()

    def _clear_browsing_data(self) -> None:
        """Clear all history, downloads, and bookmarks with a destructive warning.

        Called by the ⋮ menu's 'Clear browsing data' row. Asks for
        confirmation first showing counts of history entries, downloads,
        and bookmarks that will be deleted. This cannot be undone.
        """
        from .confirm_dialog import ConfirmDialog
        h_count = self._history.count()
        d_count = len(self._downloads.all()) if hasattr(self, "_downloads") else 0
        b_count = len(self._bookmarks.list_all(limit=100000))

        if h_count == 0 and d_count == 0 and b_count == 0:
            return

        summary = f"Delete {h_count} history entries"
        if d_count:
            summary += f", {d_count} downloads"
        if b_count:
            summary += f", and {b_count} bookmarks"

        if ConfirmDialog.ask_destructive(
            self,
            "Clear all browsing data?",
            summary,
            detail="This will delete all history, downloads, and bookmarks. "
                   "This cannot be undone.",
            confirm_label="Clear all",
            cancel_label="Cancel",
        ):
            self._history.clear()
            if hasattr(self, "_downloads"):
                self._downloads.clear_finished()
            self._bookmarks.clear()
            if self._side_panel is not None:
                self._side_panel.refresh_history()
                self._side_panel.refresh_bookmarks()

    # ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
    # HELPERS
    # ════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

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
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget, QPushButton,
        )
        from PyQt6.QtCore import Qt
        from ..core.adblock_state import is_adblock_enabled
        from .components import ToggleSwitch

        dialog = QDialog(self)
        dialog.setWindowTitle("Preferences")
        dialog.setFixedSize(520, 420)
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
        layout.setSpacing(12)

        title = QLabel("Preferences")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #eeeef8; letter-spacing: -0.02em;")
        layout.addWidget(title)

        subtitle = QLabel("BFSB Browser Settings")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("font-size: 13px; color: #7c6af5; font-weight: 500;")
        layout.addWidget(subtitle)

        layout.addSpacing(16)

        section = QLabel("PRIVACY")
        section.setStyleSheet(
            "font-size: 11px; font-weight: 600; color: #8f93bd; letter-spacing: 0.08em;"
        )
        layout.addWidget(section)

        row = QWidget()
        row.setStyleSheet(
            "background: rgba(255, 255, 255, 0.03);"
            "border: 1px solid rgba(255, 255, 255, 0.05);"
            "border-radius: 12px;"
        )
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(16, 14, 16, 14)
        row_layout.setSpacing(12)

        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        adblock_title = QLabel("Ad blocker")
        adblock_title.setStyleSheet("font-size: 14px; font-weight: 600; color: #eeeef8;")
        text_col.addWidget(adblock_title)
        adblock_desc = QLabel(
            "Blocks ads and trackers at the network level. "
            "Applies instantly — no restart needed."
        )
        adblock_desc.setWordWrap(True)
        adblock_desc.setStyleSheet("font-size: 12px; color: #9090b8;")
        text_col.addWidget(adblock_desc)
        row_layout.addLayout(text_col, stretch=1)

        self._adblock_toggle = ToggleSwitch(checked=is_adblock_enabled())
        self._adblock_toggle.toggled.connect(self._on_adblock_toggled)
        self._adblock_toggle.setAccessibleName("Ad blocker")
        row_layout.addWidget(self._adblock_toggle, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(row)

        layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(0, 0, 0, 0)
        dialog_layout.addWidget(container)

        dialog.exec()

    def _on_adblock_toggled(self, enabled: bool) -> None:
        """Hot-toggle the adblocker: state file + page-side wiring, no restart."""
        from ..core.adblock_state import set_adblock_enabled
        set_adblock_enabled(enabled)
        try:
            from ..core.tampermonkey_scripts import get_script_manager, log_diag
            manager = get_script_manager()
            manager.set_adblock_enabled(enabled)
            for view in self._views:
                page = view.page()
                if page is None:
                    continue
                manager.set_page_adblock_enabled(page, enabled)
                page.runJavaScript(self._adblock_toggle_js(enabled))
                if enabled and hasattr(view, "_inject_cosmetic_css"):
                    QTimer.singleShot(
                        0, lambda v=view: view._inject_cosmetic_css(v.url().toString())
                    )
            log_diag(f"[BFSB] adblock {'enabled' if enabled else 'disabled'} (hot toggle)")
        except Exception as e:
            log_diag(f"[BFSB] adblock toggle failed: {e}")

    @staticmethod
    def _adblock_toggle_js(enabled: bool) -> str:
        disabled = "false" if enabled else "true"
        reinject = (
            "if (window.__bfsbInjectCosmeticCSS && window.__bfsbYTAdCss) {"
            " window.__bfsbInjectCosmeticCSS(window.__bfsbYTAdCss); }"
        ) if enabled else ""
        return (
            f"window.__bfsbAdblockDisabled = {disabled};"
            "try { document.querySelectorAll(\"style[data-bfsb-cosmetic],"
            "style[data-bfsb-early-cosmetic],#bfsb-cosmetic-style\")"
            ".forEach(function(el){ if (el.parentNode) el.parentNode.removeChild(el); });"
            " } catch (e) {}" + reinject
        )