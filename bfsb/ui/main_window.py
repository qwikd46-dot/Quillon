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
        # Set URL request interceptor ONCE on the shared profile
        self._profile.setUrlRequestInterceptor(RequestInterceptor(self._blocker))
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
                print("[BFSBWindow] Server ready at", self._server.base_url)
                loop.run_forever()
            except Exception as e:
                print(f"[BFSBWindow] Failed to start server: {e}")
            finally:
                loop.close()

        self._server_thread = threading.Thread(target=run_server, daemon=True)
        self._server_thread.start()

        # Wait for server to be fully ready (with retries) - use health endpoint
        import time
        import requests
        for i in range(100):  # Wait up to 10 seconds (100 * 0.1s)
            time.sleep(0.1)
            try:
                resp = requests.get("http://127.0.0.1:8889/health", timeout=0.5)
                if resp.status_code == 200:
                    print("[BFSBWindow] Server health check OK")
                    break
            except Exception:
                pass
        else:
            print("[BFSBWindow] WARNING: Server health check failed after 10s")

    def _init_ui(self) -> None:
        """Initialize main UI — stacked widget for tabs."""
        self.setWindowTitle(APP_CONFIG.WINDOW_TITLE)
        self.resize(APP_CONFIG.WINDOW_WIDTH, APP_CONFIG.WINDOW_HEIGHT)

        # Central widget = stacked widget for tabs
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background: {C.BG_0}; border: none;")
        self.setCentralWidget(self._stack)

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

    def _update_status(self) -> None:
        """Update status bar message."""
        self._status_bar.showMessage(
            f"Ready | {self._blocker.blocked_count:,} threats blocked"
        )

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
        return view

    def _load_home(self, view: Optional[QWebEngineView] = None) -> None:
        """Load home page (search engine) into view."""
        target = view or self._views[-1] if self._views else None
        if not target:
            return
        if not self._server_ready or self._server is None:
            target.setHtml(
                "<html><body style='background:#0a0a12;color:#f0f0f5;"
                "font-family:sans-serif;padding:2rem;text-align:center;'>"
                "<h1>BFSB</h1><p>Starting search engine...</p></body></html>"
            )
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
        self._update_status()

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
        # NOTE: QUrl normalizes the scheme host to lowercase and appends a
        # trailing slash (bfsb://newTab -> bfsb://newtab/), so every match
        # must be case-insensitive.
        if text.lower().startswith("bfsb://about"):
            self._show_about_dialog()
            return
        elif text.lower().startswith("bfsb://preferences"):
            self._show_preferences_dialog()
            return
        elif text.lower().startswith("bfsb://goback"):
            self.go_back()
            return
        elif text.lower().startswith("bfsb://goforward"):
            self.go_forward()
            return
        elif text.lower().startswith("bfsb://reload"):
            self.reload()
            return
        elif text.lower().startswith("bfsb://switchtab"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            if 'index' in query:
                self.switch_tab(int(query['index'][0]))
            return
        elif text.lower().startswith("bfsb://closetab"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            if 'index' in query:
                self.close_tab(int(query['index'][0]))
            return
        elif text.lower().startswith("bfsb://newtab"):
            self.new_tab()
            return
        elif text.lower().startswith("bfsb://navigate"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            if 'url' in query:
                self.navigate(query['url'][0])
            return
        elif text.lower().startswith("bfsb://search"):
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