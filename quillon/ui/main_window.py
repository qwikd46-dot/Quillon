"""Main application window — Quillon Browser (multi-tab, server-backed)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import traceback
from typing import Optional
from pathlib import Path
from urllib.parse import urlparse

from PyQt6.QtCore import Qt, QUrl, QTimer, QObject, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QStackedWidget,
    QMessageBox,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.sip import isdeleted as sip_isdeleted

from .styles import C
from .browser_chrome import BrowserChrome
from .side_overlay import ExternalSideOverlay
from .category_popup import CategoryPopup

from ..core import (
    create_web_view,
    URLBlocker,
    CookieVault,
    PasswordVault,
    create_web_profile,
    APP_CONFIG,
    SECURITY_CONFIG,
    QuillonPage,
    get_server,
    shutdown_server,
    BFSHBServer,
    set_quillon_action_target,
    RequestInterceptor,
)

# Single worker for blocking SQLite access that must never run on the GUI
# thread (ground rule 6). BrowserDB is thread-safe (check_same_thread=False
# + write lock), so workers only need serialization, not per-call conns.
from concurrent.futures import ThreadPoolExecutor
_db_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quillon-db")


def view_is_loading(view) -> bool:
    """Whether a web view is still loading, read through its page.

    QWebEngineView has no isLoading(); QWebEnginePage does. Calling the
    view-level form raised AttributeError, which the callers swallowed.

    What that actually cost: the throw sits AFTER
    _update_chrome_for_current(), so the tab did switch and the chrome did
    update. The visible effect was a progress bar that never completed.
    It is NOT the cause of "the tab did not switch" -- that is a separate
    suspect earlier in switch_tab. Do not read this as a fix for that.

    Returns False for a view whose page is gone rather than raising, since
    teardown leaves deleted C++ objects behind and a bare RuntimeError
    there would take the caller down with it.
    """
    try:
        page = view.page()
    except Exception:
        return False
    if page is None:
        return False
    try:
        return bool(page.isLoading())
    except Exception:
        return False


class QuillonWindow(QMainWindow):
    """Main browser window — multi-tab, server-backed."""

    # Emitted from the DB worker when a bookmark lookup for `view` finishes;
    # Qt delivers it on the GUI thread (queued connection across threads).
    _bookmark_checked = pyqtSignal(object, str, bool)

    def __init__(self, blocker: URLBlocker) -> None:
        super().__init__()
        self._blocker = blocker
        self._cookie_vault = CookieVault()
        self._password_vault = PasswordVault()
        self._password_prompt_seen: set[tuple[str, str]] = set()
        # Cache of url -> bookmarked, filled by the DB worker (avoids a
        # synchronous SQLite read on the GUI thread per URL change).
        self._bookmark_cache: dict[str, bool] = {}
        self._bookmark_checked.connect(self._on_bookmark_checked)

        # Server
        self._server: Optional[BFSHBServer] = None
        self._server_loop: Optional[asyncio.AbstractEventLoop] = None
        self._server_ready = False

        # UI state
        self._profile = None
        self._view_profiles: dict[QWebEngineView, object] = {}
        self._private_profiles: list[object] = []
        self._stack: Optional[QStackedWidget] = None
        self._views: list[QWebEngineView] = []  # web views for each tab
        self._view_urls: dict[QWebEngineView, str] = {}  # track expected URL per view
        self._view_titles: dict[QWebEngineView, str] = {}
        self._category_popup: Optional[CategoryPopup] = None
        self._pending_searches: dict[QWebEngineView, str] = {}
        self._sidebar_state_path = Path.home() / ".quillon" / "sidebar-state.json"
        persisted_sidebar_state = self._load_sidebar_state()
        self._sidebar_collapsed = (
            bool(persisted_sidebar_state)
            if persisted_sidebar_state is not None
            else False
        )
        self._sidebar_state_initialized = persisted_sidebar_state is not None
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
        from .downloads import DownloadManager
        self._download_manager = DownloadManager(self)
        # quillon:// GUI actions (fetch-based) dispatch into this window.
        set_quillon_action_target(self)
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
            self._server_loop = loop
            asyncio.set_event_loop(loop)
            try:
                self._server = loop.run_until_complete(get_server())
                self._server_ready = True
                print("[QuillonWindow] Server ready at", self._server.base_url)
                loop.run_forever()
            except Exception as e:
                print(f"[QuillonWindow] Failed to start server: {e}")
            finally:
                loop.close()
                self._server_loop = None

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
        the Quillon home/results/panels GUI is fully rendered by the local
        server template and needs no native chrome at all.
        """
        self.setWindowTitle(APP_CONFIG.WINDOW_TITLE)
        self.resize(APP_CONFIG.WINDOW_WIDTH, APP_CONFIG.WINDOW_HEIGHT)

        # Container: universal sidebar left, browser chrome and tab views right.
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
        self._chrome.setVisible(False)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background: {C.BG_0}; border: none;")
        self._content = QWidget()
        content_layout = QHBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self._side_overlay = ExternalSideOverlay(self._content)
        content_layout.addWidget(self._side_overlay)

        self._right_column = QWidget()
        right_layout = QVBoxLayout(self._right_column)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self._chrome)

        self._view_content = QWidget()
        view_content_layout = QGridLayout(self._view_content)
        view_content_layout.setContentsMargins(0, 0, 0, 0)
        view_content_layout.setSpacing(0)
        view_content_layout.addWidget(self._stack, 0, 0)
        self._category_popup = CategoryPopup(self._view_content, self)
        self._category_popup.closed.connect(self._on_category_popup_closed)
        view_content_layout.addWidget(self._category_popup, 0, 0)
        right_layout.addWidget(self._view_content, 1)
        content_layout.addWidget(self._right_column, 1)
        self._side_overlay.home_requested.connect(self._open_home)
        self._side_overlay.panel_requested.connect(self._overlay_panel)
        self._side_overlay.collapsed_changed.connect(self._on_sidebar_collapsed_changed)
        self._side_overlay.set_collapsed(self._sidebar_collapsed, emit=False)
        layout.addWidget(self._content, 1)

        from PyQt6.QtGui import QKeySequence, QShortcut
        self._quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        self._quit_shortcut.activated.connect(self.close)
        self._new_tab_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        self._new_tab_shortcut.activated.connect(lambda: self.new_tab())
        self._private_tab_shortcut = QShortcut(QKeySequence("Ctrl+Shift+N"), self)
        self._private_tab_shortcut.activated.connect(
            lambda: self.new_tab(private=True)
        )
        self._close_tab_shortcut = QShortcut(QKeySequence("Ctrl+W"), self)
        self._close_tab_shortcut.activated.connect(
            lambda: self.close_tab(self.get_active_tab_index())
        )
        self.setCentralWidget(container)

    def showEvent(self, event) -> None:
        """Ensure window is raised and activated on Wayland/XWayland."""
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setWindowState(self.windowState() | Qt.WindowState.WindowActive)

    def _create_view(self, private: bool = False) -> QWebEngineView:
        """Create a web view wired to the native chrome."""
        profile = self._profile
        if private:
            profile = create_web_profile(private=True)
            profile.setUrlRequestInterceptor(RequestInterceptor(self._blocker))
            profile.downloadRequested.connect(self._on_download)
            self._private_profiles.append(profile)
        view = create_web_view(profile, self._blocker, page_class=QuillonPage, window=self)
        self._view_profiles[view] = profile
        view.page()._private = private
        view.page()._main_window = self
        self._view_titles[view] = "New Tab"

        # Keep the tracked URL, the chrome URL bar / buttons, and the
        # chrome visibility in sync with whatever this view loads.
        view.urlChanged.connect(lambda url, v=view: self._on_view_url_changed(v, url))
        view.page().titleChanged.connect(
            lambda title, v=view: self._on_view_title_changed(v, title))
        view.loadStarted.connect(
            lambda v=view: self._chrome.start_progress() if v is self._get_current_view() else None)
        view.loadProgress.connect(
            lambda percent, v=view: self._on_view_load_progress(v, percent))
        view.loadFinished.connect(
            lambda ok, v=view: self._on_view_load_finished(v, ok))
        return view

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
    # NATIVE CHROME SYNC (visible only on external websites)
    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _is_quillon_url(url: str) -> bool:
        """True when the URL is one of Quillon's own local pages."""
        value = url or ""
        if value.startswith(("quillon://", "data:", "about:")):
            return True
        try:
            parsed = urlparse(value)
            return (
                parsed.scheme in ("http", "https")
                and parsed.hostname in ("127.0.0.1", "localhost")
                and parsed.port in (8888, 8889)
            )
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _fallback_tab_title(url: str, index: int) -> str:
        value = str(url or "").strip()
        if not value or value.startswith(("about:", "data:")):
            return "New Tab"
        try:
            parsed = urlparse(value)
            host = (parsed.hostname or "").lower().rstrip(".")
            path = parsed.path or ""
        except (TypeError, ValueError):
            return f"Tab {index + 1}"
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}:
            return "YouTube Shorts" if "/shorts/" in path else "YouTube"
        if host:
            return host[4:] if host.startswith("www.") else host
        return f"Tab {index + 1}"

    def _base_tab_title_for_view(self, view: QWebEngineView, index: int) -> str:
        url = self._view_urls.get(view, "")
        if not url:
            try:
                url = view.url().toString()
            except Exception:
                url = ""
        if self._is_quillon_url(url) or url.startswith(("about:", "data:", "quillon:")):
            return "Quillon — New Tab"
        title = " ".join(str(self._view_titles.get(view) or "").split())
        if title and title not in {"New Tab", f"Tab {index + 1}"}:
            return title
        return self._fallback_tab_title(url, index)

    def _tab_title_for_view(self, view: QWebEngineView, index: int) -> str:
        title = self._base_tab_title_for_view(view, index)
        if bool(getattr(view.page(), "_private", False)):
            return f"Private · {title}"
        return title

    def _on_view_url_changed(self, view, url: QUrl) -> None:
        url_str = url.toString() if hasattr(url, "toString") else str(url)
        if url_str.startswith("quillon://"):
            return  # internal action, never a real location
        self._view_urls[view] = url_str
        try:
            index = self._views.index(view)
        except ValueError:
            index = 0
        self._view_titles[view] = self._fallback_tab_title(url_str, index)
        self._request_tab_sync()
        self._sync_chrome_tabs()
        if view is self._get_current_view():
            if self._category_popup is not None and self._category_popup.isVisible():
                self._category_popup.close_popup()
            self._update_chrome_for_current()

    def _on_view_title_changed(self, view, title: str) -> None:
        value = " ".join(str(title or "").split())
        if value:
            self._view_titles[view] = value
        else:
            try:
                index = self._views.index(view)
            except ValueError:
                index = 0
            self._view_titles[view] = self._fallback_tab_title(
                self._view_urls.get(view, ""), index
            )
        self._request_tab_sync()
        self._sync_chrome_tabs()

    def _on_view_load_progress(self, view, percent: int) -> None:
        if view is self._get_current_view():
            self._chrome.set_progress(percent)

    def _on_view_load_finished(self, view, ok: bool) -> None:
        if view is self._get_current_view() or not any(
            view_is_loading(candidate) for candidate in self._views if candidate is not view
        ):
            self._chrome.complete_progress()
        if view is self._get_current_view():
            self._update_chrome_for_current()
        pending_query = self._pending_searches.pop(view, None)
        if pending_query is not None and ok:
            encoded_query = json.dumps(pending_query, ensure_ascii=False)
            view.page().runJavaScript(
                f"window.runSearch && window.runSearch({encoded_query});"
            )
        view_url = self._view_urls.get(view) or view.url().toString()
        if self._is_quillon_url(view_url):
            if self._sidebar_state_initialized:
                self._sync_sidebar_to_view(view)
            else:
                self._read_sidebar_state_from_view(view)
        # History only settles once the load is done. Refreshing here is
        # what makes Back grey out and Forward light up again after a
        # navigation completes; the fixed 0/120ms timers in go_back and
        # go_forward fire long before a real page has loaded, so they
        # read a history that is still mid-transition and leave the
        # buttons showing a state that is no longer true.
        if view is self._get_current_view():
            self._sync_nav_state(view)
        self._request_tab_sync()

    def _sync_nav_state(self, view: Optional[QWebEngineView] = None) -> None:
        target = view or self._get_current_view()
        if target is None:
            return
        try:
            can_back = target.history().canGoBack()
            can_forward = target.history().canGoForward()
        except Exception:
            can_back = can_forward = False
        self._chrome.update_nav_buttons(can_back, can_forward)
        url = self._view_urls.get(target) or target.url().toString()
        if not self._is_quillon_url(url) or url.startswith(("about:", "data:", "quillon:")):
            return
        try:
            target.page().runJavaScript(
                "window.__quillonSetNavState && window.__quillonSetNavState("
                f"{str(can_back).lower()}, {str(can_forward).lower()});"
            )
        except Exception:
            pass

    def _update_chrome_for_current(self) -> None:
        """Show the native chrome on websites, hide it on Quillon pages, and
        refresh its URL bar / nav buttons / bookmark star."""
        view = self._get_current_view()
        if view is None:
            self._chrome.setVisible(False)
            self._side_overlay.set_browser_active(False)
            return
        url = self._view_urls.get(view) or view.url().toString()
        is_site = not self._is_quillon_url(url)
        # Track the intended visibility ourselves. isVisible() is false
        # whenever the whole window is not shown, so asking the widget
        # cannot tell "hidden because this is a Quillon page" from "the
        # window has not been shown yet".
        was_hidden = not getattr(self, "_chrome_shown_for_site", False)
        self._chrome_shown_for_site = is_site
        self._chrome.setVisible(is_site)
        self._side_overlay.set_browser_active(is_site)
        if is_site:
            self._chrome.set_url(url)
            self._refresh_bookmarked(view, url)
            # The chrome is hidden on Quillon pages, so a navigation that
            # leaves one — a bookmark card, a history entry, a result link
            # in the same tab — fires loadStarted while the bar is still
            # inside a hidden widget. start_progress() then paints
            # something nobody can see, and by the time the chrome is
            # shown the load is already under way or finished. Start it
            # here, after the chrome is actually shown.
            if was_hidden and view_is_loading(view):
                self._chrome.start_progress()
        else:
            self._side_overlay.close_overlay()
            if self._category_popup is not None:
                self._category_popup.close_popup()
            self._sync_sidebar_to_view(view)
        self._sync_nav_state(view)

    def _load_sidebar_state(self) -> Optional[bool]:
        try:
            payload = json.loads(self._sidebar_state_path.read_text(encoding="utf-8"))
            return bool(payload["collapsed"])
        except Exception:
            return None

    def _save_sidebar_state(self) -> None:
        temp_path = self._sidebar_state_path.with_suffix(
            self._sidebar_state_path.suffix + f".tmp.{os.getpid()}"
        )
        try:
            self._sidebar_state_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(
                json.dumps({"collapsed": bool(self._sidebar_collapsed)}),
                encoding="utf-8",
            )
            temp_path.replace(self._sidebar_state_path)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _on_sidebar_collapsed_changed(self, collapsed: bool) -> None:
        self._set_sidebar_collapsed(collapsed, sync_views=True)

    def _set_sidebar_collapsed(self, collapsed: bool, sync_views: bool = True) -> None:
        self._sidebar_collapsed = bool(collapsed)
        self._sidebar_state_initialized = True
        self._save_sidebar_state()
        if self._side_overlay is not None:
            self._side_overlay.set_collapsed(self._sidebar_collapsed, emit=False)
        if sync_views:
            self._sync_sidebar_to_views()

    def _sync_sidebar_to_view(self, view: QWebEngineView) -> None:
        url = self._view_urls.get(view) or view.url().toString()
        if not self._is_quillon_url(url) or url.startswith(("about:", "data:", "quillon:")):
            return
        if not self._sidebar_state_initialized:
            return
        state = "true" if self._sidebar_collapsed else "false"
        try:
            view.page().runJavaScript(
                f"window.__quillonSetSidebarCollapsed && window.__quillonSetSidebarCollapsed({state});"
            )
        except Exception:
            pass

    def _sync_sidebar_to_views(self) -> None:
        for view in self._views:
            self._sync_sidebar_to_view(view)

    def _read_sidebar_state_from_view(self, view: QWebEngineView) -> None:
        try:
            view.page().runJavaScript(
                "localStorage.getItem('quillon-sidebar-collapsed');",
                lambda value, target=view: self._on_sidebar_state_read(target, value),
            )
        except Exception:
            pass

    def _on_sidebar_state_read(self, view: QWebEngineView, value) -> None:
        if view not in self._views or self._sidebar_state_initialized:
            return
        self._set_sidebar_collapsed(str(value) == "1", sync_views=False)

    def _toggle_side_overlay(self) -> None:
        if self._category_popup is not None and self._category_popup.isVisible():
            self._category_popup.close_popup()
        self._side_overlay.toggle_overlay()

    def _open_home(self) -> None:
        self._side_overlay.close_overlay()
        if self._server is None:
            return
        self.navigate(self._server.home_url)

    def _overlay_home(self) -> None:
        self._open_home()

    def _on_category_popup_closed(self) -> None:
        view = self._get_current_view()
        if view is None:
            return
        url = self._view_urls.get(view) or view.url().toString()
        if not self._is_quillon_url(url):
            self._side_overlay.set_browser_active(True)

    def _overlay_panel(self, panel: str) -> None:
        if panel == "home":
            self._open_home()
            return
        if panel == "about":
            self._show_about_dialog()
            return
        self._side_overlay.close_overlay()
        if self._category_popup is not None:
            self._category_popup.open_panel(panel)

    def _set_adblock_state(self, enabled: bool) -> None:
        from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

        if not hasattr(self, "_adblock_manager"):
            self._adblock_manager = QNetworkAccessManager(self)
        self._pending_adblock_state = bool(enabled)
        try:
            from ..core.tampermonkey_scripts import get_script_manager
            manager = get_script_manager()
            manager.set_adblock_enabled(bool(enabled))
            for view in self._views:
                manager.set_page_adblock_enabled(view.page(), bool(enabled))
                view.page().runJavaScript(
                    f"window.__quillonAdblockDisabled = {not bool(enabled)};"
                )
        except Exception as error:
            print(f"[Window] adblock script toggle failed: {error}")
            print(traceback.format_exc())
        url = f"http://127.0.0.1:8889/api/adblock?enabled={1 if enabled else 0}"
        request = QNetworkRequest(QUrl(url))
        origin = self._server.base_url if self._server else "http://127.0.0.1:8889"
        request.setRawHeader(b"Origin", origin.encode("ascii"))
        reply = self._adblock_manager.post(request, b"")
        reply.finished.connect(lambda: self._adblock_reply_finished(reply, QNetworkReply))

    def _adblock_reply_finished(self, reply, network_reply_type) -> None:
        try:
            state = bool(getattr(self, "_pending_adblock_state", False))
            if reply.error() != network_reply_type.NetworkError.NoError:
                state = not state
            self._side_overlay.set_adblock_state(state)
        finally:
            reply.deleteLater()

    def _refresh_bookmarked(self, view, url: str) -> None:
        """Update the chrome star without blocking the GUI thread.

        Cached values apply instantly; cache misses are resolved by the DB
        worker and applied via the _bookmark_checked signal.
        """
        if url in self._bookmark_cache:
            self._chrome.set_bookmarked(self._bookmark_cache[url])
            return
        self._chrome.set_bookmarked(False)

        def _lookup() -> None:
            try:
                from ..core.storage.bookmarks import BookmarkStore
                marked = bool(BookmarkStore().get(url))
            except Exception:
                marked = False
            self._bookmark_checked.emit(view, url, marked)

        _db_executor.submit(_lookup)

    def _on_bookmark_checked(self, view, url: str, marked: bool) -> None:
        """GUI-thread slot: apply a finished bookmark lookup."""
        self._bookmark_cache[url] = marked
        current_url = self._view_urls.get(view)
        if current_url == url and view is self._get_current_view():
            self._chrome.set_bookmarked(marked)
        self._refresh_quillon_bookmark_views()

    def _refresh_quillon_bookmark_views(self) -> None:
        for view in self._views:
            url = self._view_urls.get(view) or view.url().toString()
            if not self._is_quillon_url(url) or url.startswith(("about:", "data:", "quillon:")):
                continue
            try:
                view.page().runJavaScript(
                    "window.refreshHomeBookmarks && window.refreshHomeBookmarks();"
                )
            except Exception:
                pass

    def _sync_chrome_tabs(self) -> None:
        """Sync the native chrome tab bar with the real tab list.

        Diff-based (phase 1): switching tabs updates titles + active state
        in place; the widget rebuild only happens when tabs are added or
        removed. The old clear()+re-add measured 7-15ms per switch.
        """
        try:
            titles = [self._tab_title_for_view(view, i) for i, view in enumerate(self._views)]
            self._chrome.tab_bar.update_all(titles, self._stack.currentIndex())
        except Exception as e:
            print(f"[Window] chrome tab sync failed: {e}")

    def _toggle_bookmark_current(self) -> None:
        """Bookmark-star pressed on the native chrome.

        The store read/write runs on the DB worker (never the GUI thread);
        the star updates when the _bookmark_checked signal comes back.
        The single-worker executor also serializes double-click races.
        """
        view = self._get_current_view()
        if view is None:
            return
        url = self._view_urls.get(view) or view.url().toString()
        if self._is_quillon_url(url):
            return
        title = self._tab_title_for_view(view, self._views.index(view))

        def _toggle() -> None:
            try:
                from ..core.storage.bookmarks import BookmarkStore

                store = BookmarkStore()
                if store.get(url):
                    store.remove(url)
                    marked = False
                else:
                    store.add(url=url, title=title)
                    marked = True
            except Exception as e:
                print(f"[Window] bookmark toggle failed: {e}")
                return
            self._bookmark_checked.emit(view, url, marked)

        _db_executor.submit(_toggle)

    @pyqtSlot(str)
    def _offer_password_from_json(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
            origin = str(payload.get("origin", "")).strip()
            username = str(payload.get("username", "")).strip()
            password = str(payload.get("password", ""))
            if bool(payload.get("private")):
                return
            parsed = urlparse(origin)
            secure = parsed.scheme == "https" or (
                parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1"}
            )
            if not secure or not username or not password:
                return
            prompt_key = (
                origin,
                username,
                hashlib.sha256(password.encode("utf-8")).hexdigest(),
            )
            if prompt_key in self._password_prompt_seen:
                return
            self._password_prompt_seen.add(prompt_key)
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("Save password?")
            box.setText(f"Save the password for {origin}?")
            box.setInformativeText(f"Account: {username}")
            box.setStandardButtons(
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.No
            )
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec() == QMessageBox.StandardButton.Save:
                self._password_vault.save(origin, username, password)
        except Exception:
            return

    def _show_main_menu(self, global_point=None) -> None:
        from .menu import QuillonMenu
        from PyQt6.QtGui import QCursor

        def _open_panel(panel: str):
            self._overlay_panel(panel)

        menu = QuillonMenu(self)
        menu.add_item("＋", "New tab", "Ctrl+T", on_triggered=lambda: self.new_tab())
        menu.add_item("◌", "Private tab", "Ctrl+Shift+N", on_triggered=lambda: self.new_tab(private=True))
        menu.add_item("★", "Bookmarks", on_triggered=lambda: _open_panel("bookmarks"))
        menu.add_item("🕘", "History", on_triggered=lambda: _open_panel("history"))
        menu.add_item("⬇", "Downloads", on_triggered=lambda: _open_panel("downloads"))
        menu.add_separator()
        menu.add_item("⚙", "Settings", on_triggered=lambda: _open_panel("settings"))
        menu.add_item("ℹ", "About Quillon", on_triggered=lambda: self._overlay_panel("about"))
        menu.add_separator()
        menu.add_item("⌫", "Quit Quillon", "Ctrl+Q", on_triggered=self.close)
        self._active_menu = menu
        menu.aboutToHide.connect(lambda: setattr(self, "_active_menu", None))
        menu.popup_at(global_point or QCursor.pos())

    def _on_download(self, download) -> None:
        """Handle download requests — security hardened."""
        url_path = download.url().path()
        ext = Path(url_path).suffix.lower()

        if ext in SECURITY_CONFIG.BAD_EXTENSIONS or not ext:
            print(f"BLOCKED DOWNLOAD: {url_path}")
            download.cancel()
            download.deleteLater()
            return

        self._download_manager.add(download)

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
        template wires clicks to quillon://switchTab / quillon://closeTab.
        """
        self._pending_tab_sync = False
        tabs_data = []
        for i, view in enumerate(self._views):
            url = self._view_urls.get(view, view.url().toString())
            tabs_data.append({
                "index": i,
                "title": self._tab_title_for_view(view, i),
                "url": url,
                "active": i == self._stack.currentIndex(),
            })

        self._push_tabs_js(tabs_data)

    def _push_tabs_js(self, tabs_data: list[dict], target_view: Optional[QWebEngineView] = None) -> None:
        """Push tab data only into local Quillon pages."""
        tabs_json = json.dumps(tabs_data)
        for view in self._views:
            if target_view is not None and view is not target_view:
                continue
            url = self._view_urls.get(view) or view.url().toString()
            if not self._is_quillon_url(url) or url.startswith(("about:", "data:", "quillon:")):
                continue
            try:
                view.page().runJavaScript(
                    f"window.__quillonRenderTabs && window.__quillonRenderTabs({tabs_json});"
                )
            except Exception as e:
                print(f"[Window] Tab sync failed for view: {e}")

    def _sync_single_view(self, view: QWebEngineView, new_active_index: int = None, old_active_index: int = None) -> None:
        """Sync the tab strip only in the supplied local view."""
        try:
            tabs_data = []
            active_index = self._stack.currentIndex() if new_active_index is None else new_active_index
            for i, v in enumerate(self._views):
                url = self._view_urls.get(v, v.url().toString())
                tabs_data.append({
                    "index": i,
                    "title": self._tab_title_for_view(v, i),
                    "url": url,
                    "active": i == active_index,
                })
            self._push_tabs_js(tabs_data, target_view=view)
        except Exception as e:
            print(f"[Window] Tab sync failed for view: {e}")

    def new_tab(self, url: Optional[str] = None, private: bool = False) -> Optional[QWebEngineView]:
        try:
            return self._new_tab_impl(url, private=private)
        except Exception as error:
            print(f"[Window] new_tab failed: {error}")
            print(traceback.format_exc())
            return None

    def _new_tab_impl(self, url: Optional[str] = None, private: bool = False) -> Optional[QWebEngineView]:
        """Create and add a new tab."""
        if self._category_popup is not None:
            self._category_popup.close_popup()
        view = self._create_view(private=private)
        index = self._stack.addWidget(view)
        self._views.insert(index, view)
        self._view_titles[view] = self._fallback_tab_title(url or "about:home", index)

        def _on_load_finished(ok: bool):
            if ok:
                QTimer.singleShot(0, lambda: self._sync_single_view(view))
            view.loadFinished.disconnect(_on_load_finished)
        view.loadFinished.connect(_on_load_finished)

        if url is None or url == "about:home":
            self._load_home(view)
        else:
            view.setUrl(QUrl(url))
            self._view_urls[view] = url

        self._sync_tabs_to_views()

        self._stack.setCurrentIndex(index)
        self._sync_single_view(view, new_active_index=index)
        self._sync_chrome_tabs()
        self._update_chrome_for_current()
        # Start the bar here rather than trusting loadStarted to win a race.
        # setUrl() above fires loadStarted, but the view only becomes current
        # a few lines later, and the loadStarted handler only starts the bar
        # when the view is already current. When that check loses, nothing
        # ever starts the bar for a tab opened from a result link, and the
        # user sees no progress at all. loadProgress can cover for it, but
        # it does not fire for every load. Being explicit here removes the
        # ordering dependency; start_progress is safe to call twice.
        if url and not self._is_quillon_url(url):
            self._chrome.start_progress()
        if os.environ.get("QUILLON_TEST") == "1":
            source = "url" if url else "home"
            print(f"[QUILLON_TEST] new_tab called source={source} view_count={len(self._views)}", flush=True)
        return view

    def close_tab(self, index: int) -> None:
        try:
            self._close_tab_impl(index)
        except Exception as error:
            print(f"[Window] close_tab failed for index={index}: {error}")
            print(traceback.format_exc())

    def _close_tab_impl(self, index: int) -> None:
        """Close tab at index."""
        if not (0 <= index < len(self._views)):
            return
        if self._category_popup is not None:
            self._category_popup.close_popup()
        current_view = self._get_current_view()
        old_view = self._views.pop(index)
        self._stack.removeWidget(old_view)
        self._view_urls.pop(old_view, None)
        self._view_titles.pop(old_view, None)
        self._pending_searches.pop(old_view, None)
        old_profile = self._view_profiles.pop(old_view, None)
        if old_profile is not None and old_profile is not self._profile:
            try:
                self._private_profiles.remove(old_profile)
            except ValueError:
                pass
            try:
                old_profile.deleteLater()
            except Exception:
                pass
        old_view.deleteLater()
        if self._views:
            if current_view is old_view or current_view not in self._views:
                current_view = self._views[min(index, len(self._views) - 1)]
            new_index = self._views.index(current_view)
            self._stack.setCurrentIndex(new_index)
            self._sync_tabs_to_views()
        else:
            self.new_tab()
        self._sync_chrome_tabs()
        self._update_chrome_for_current()
        if os.environ.get("QUILLON_TEST") == "1":
            print(f"[QUILLON_TEST] close_tab called index={index} view_count={len(self._views)}", flush=True)

    def reorder_tabs(self, from_index: int, to_index: int) -> None:
        try:
            self._reorder_tabs_impl(from_index, to_index)
        except Exception as error:
            print(f"[Window] reorder_tabs failed: {error}")
            print(traceback.format_exc())

    def _reorder_tabs_impl(self, from_index: int, to_index: int) -> None:
        if not (0 <= from_index < len(self._views)):
            return
        if self._category_popup is not None:
            self._category_popup.close_popup()
        to_index = max(0, min(to_index, len(self._views) - 1))
        if from_index == to_index:
            return
        active_view = self._get_current_view()
        view = self._views.pop(from_index)
        self._views.insert(to_index, view)
        self._stack.removeWidget(view)
        self._stack.insertWidget(to_index, view)
        self._chrome.tab_bar.reorder_tabs(from_index, to_index)
        if active_view is not None:
            self._stack.setCurrentIndex(self._views.index(active_view))
        self._sync_tabs_to_views()
        self._sync_chrome_tabs()
        self._update_chrome_for_current()
        if os.environ.get("QUILLON_TEST") == "1":
            print(f"[QUILLON_TEST] reorder_tabs called from={from_index} to={to_index}", flush=True)

    def switch_tab(self, index: int) -> None:
        """Switch to tab at index."""
        from PyQt6.QtCore import QElapsedTimer
        if not (0 <= index < len(self._views)) or self._stack is None:
            return
        timer = QElapsedTimer()
        timer.start()
        try:
            old_index = self._stack.currentIndex()
            self._stack.setCurrentIndex(index)
            if self._category_popup is not None:
                self._category_popup.close_popup()
            stack_ns = timer.nsecsElapsed()
            target_view = self._views[index]
            self._sync_single_view(target_view, new_active_index=index, old_active_index=old_index)
            sync_ns = timer.nsecsElapsed()
            self._sync_chrome_tabs()
            chrome_ns = timer.nsecsElapsed()
            self._update_chrome_for_current()
            if not view_is_loading(target_view) and not any(
                view_is_loading(candidate) for candidate in self._views if candidate is not target_view
            ):
                self._chrome.complete_progress()
            total_ns = timer.nsecsElapsed()
            if os.environ.get("QUILLON_PERF") == "1":
                print(f"[PERF] switch_tab -> {index}: stack_switch={stack_ns/1e6:.2f}ms "
                      f"js_sync={(sync_ns-stack_ns)/1e6:.2f}ms chrome_tabs={(chrome_ns-sync_ns)/1e6:.2f}ms "
                      f"chrome_update={(total_ns-chrome_ns)/1e6:.2f}ms total={total_ns/1e6:.2f}ms", flush=True)
        except Exception as error:
            print(f"[Window] switch_tab failed for index={index}: {error}")
            print(traceback.format_exc())

    def get_tab_count(self) -> int:
        """Return number of open tabs."""
        return len(self._views)

    def get_active_tab_index(self) -> int:
        """Return index of currently active tab."""
        return self._stack.currentIndex() if self._views else -1

    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════
    # NAVIGATION (called from JS bridge)
    # ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _query_int(query: dict, key: str) -> Optional[int]:
        try:
            return int(query.get(key, [""])[0])
        except (TypeError, ValueError, IndexError):
            return None

    @staticmethod
    def _safe_action_target(value: str) -> bool:
        value = value.strip()
        return value.startswith(("http://", "https://")) or value == "about:blank"

    @pyqtSlot(str)
    def _dispatch_quillon_action(self, url: str) -> None:
        self.navigate(url)

    def navigate(self, text: str) -> None:
        """Navigate to URL or trigger search via server."""
        text = text.strip()
        if not text:
            return

        # Handle Quillon internal URLs.
        # NOTE: QUrl normalizes the scheme host to lowercase and appends a
        # trailing slash (quillon://newTab -> quillon://newtab/), so every match
        # here must be case-insensitive and slash-tolerant.
        low = text.lower()
        if low.startswith("quillon://syncnav"):
            self._update_chrome_for_current()
            return
        elif low.startswith("quillon://sidebarcollapsed"):
            from urllib.parse import parse_qs, urlparse
            query = parse_qs(urlparse(text).query)
            self._set_sidebar_collapsed(query.get("state", ["0"])[0] == "1")
            return
        elif low.startswith("quillon://about"):
            self._show_about_dialog()
            return
        elif low.startswith("quillon://preferences"):
            self._show_preferences_dialog()
            return
        elif low.startswith("quillon://goback"):
            self.go_back()
            return
        elif low.startswith("quillon://goforward"):
            self.go_forward()
            return
        elif low.startswith("quillon://reload"):
            self.reload()
            return
        elif low.startswith("quillon://switchtab"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            index = self._query_int(query, 'index')
            if index is not None:
                self.switch_tab(index)
            return
        elif low.startswith("quillon://closetab"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            index = self._query_int(query, 'index')
            if index is not None:
                self.close_tab(index)
            return
        elif low.startswith("quillon://reordertabs"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            from_index = self._query_int(query, 'from')
            to_index = self._query_int(query, 'to')
            if from_index is not None and to_index is not None:
                self.reorder_tabs(from_index, to_index)
            return
        elif low.startswith("quillon://newtab"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            target = query.get('url', [None])[0]
            if target is not None and not self._safe_action_target(target):
                return
            self.new_tab(target)
            return
        elif low.startswith("quillon://navigate"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            target = query.get('url', [None])[0]
            if target is not None and self._safe_action_target(target):
                self.navigate(target)
            return
        elif low.startswith("quillon://search"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            if 'q' in query:
                self._perform_search(query['q'][0])
            return
        elif low.startswith("quillon://"):
            # Unknown quillon:// scheme - ignore
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
        if not self._server_ready:
            return

        view = self._get_current_view()
        if view is None:
            return
        current_url = self._view_urls.get(view) or view.url().toString()
        if self._is_quillon_url(current_url) and current_url.startswith(("http://", "https://")):
            encoded_query = json.dumps(query, ensure_ascii=False)
            view.page().runJavaScript(
                f"window.runSearch && window.runSearch({encoded_query});"
            )
            return
        self._pending_searches[view] = query
        home_url = self._server.home_url
        view.setUrl(QUrl(home_url))
        self._view_urls[view] = home_url

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
                QTimer.singleShot(0, lambda: self._sync_nav_state(view))
                QTimer.singleShot(120, lambda: self._sync_nav_state(view))
        except Exception as e:
            print(f"[Window] go_back error: {e}")

    def go_forward(self) -> None:
        """Navigate forward."""
        try:
            view = self._get_current_view()
            if view:
                view.forward()
                QTimer.singleShot(0, lambda: self._sync_nav_state(view))
                QTimer.singleShot(120, lambda: self._sync_nav_state(view))
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
            from ..core.proxy_bootstrap import shutdown_proxy
            shutdown_proxy()
        except Exception as error:
            print(f"[Window] proxy shutdown failed: {error}")
            print(traceback.format_exc())
        loop = self._server_loop
        if loop is not None and loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(shutdown_server(), loop)

                def _stop_server_when_done(done):
                    try:
                        done.result()
                    except Exception:
                        pass
                    try:
                        loop.call_soon_threadsafe(loop.stop)
                    except Exception:
                        pass

                future.add_done_callback(_stop_server_when_done)
            except Exception:
                try:
                    loop.call_soon_threadsafe(loop.stop)
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
        """Show the About Quillon dialog."""
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

        subtitle = QLabel("Quillon Browser Settings")
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

        The local server owns template rendering and bookmark reads. If it
        hasn't finished starting, an instant placeholder is shown and the
        real GUI loads automatically once it's up.
        """
        target = view if view is not None else (self._views[-1] if self._views else None)
        if target is None:
            return
        if not self._server_ready or self._server is None:
            target.setHtml(self._STARTING_HTML, QUrl("http://127.0.0.1:8889/"))
            self._view_urls[target] = "about:home"
            QTimer.singleShot(250, lambda: self._retry_load_home(target))
            return
        target.setUrl(QUrl(self._server.home_url))
        self._view_urls[target] = self._server.home_url

    def _retry_load_home(self, view: QWebEngineView) -> None:
        """Load home into a placeholder tab once the server is ready."""
        # The timer below outlives the call, so the view can be closed in
        # between. Touching a deleted C++ object raises RuntimeError, and
        # unhandled that aborts the process, so check the handle first.
        try:
            if not view or sip_isdeleted(view):
                return
            view.url()
        except RuntimeError:
            return
        if view not in self._views:
            return
        if self._view_urls.get(view) != "about:home":
            return  # the user already navigated somewhere else
        if self._server_ready:
            self._load_home(view)
        else:
            QTimer.singleShot(250, lambda: self._retry_load_home(view))
