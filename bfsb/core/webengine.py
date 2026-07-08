"""WebEngine components: SafePage, Interceptor, profile setup."""
# Removed WebChannel - causes Mojo IPC segfaults. Using runJavaScript() instead.

import json
from PyQt6.QtCore import QUrl, QObject, pyqtSlot
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

from .config import APP_CONFIG, SECURITY_CONFIG
from .blocker import URLBlocker


class RequestInterceptor(QWebEngineUrlRequestInterceptor):
    """Intercepts and blocks requests to blocked domains."""

    _init_called = False

    def __init__(self, blocker: URLBlocker) -> None:
        super().__init__()
        self._blocker = blocker
        if not RequestInterceptor._init_called:
            RequestInterceptor._init_called = True

    def interceptRequest(self, info) -> None:  # type: ignore[override]
        url = info.requestUrl().toString()
        # Allow local traffic
        if "localhost" in url or "127.0.0.1" in url or ":8888" in url:
            return
        if info.resourceType() in SECURITY_CONFIG.BLOCKED_RESOURCE_TYPES:
            if self._blocker.is_blocked(url):
                info.block(True)


class SafePage(QWebEnginePage):
    """WebEngine page with navigation blocking for dangerous content."""

    blocker: URLBlocker | None = None

    def __init__(self, profile: QWebEngineProfile, parent) -> None:
        super().__init__(profile, parent)

    def acceptNavigationRequest(
        self, url: QUrl, nav_type: int, is_main_frame: bool
    ) -> bool:
        url_str = url.toString()
        path = url.path().lower() if hasattr(url, "path") else url_str.lower()

        # Block dangerous file extensions
        for ext in SECURITY_CONFIG.BAD_EXTENSIONS:
            if path.endswith(ext):
                print(f"BLOCKED DOWNLOAD: {url_str}")
                return False

        # Block URLs on blocklist
        if SafePage.blocker and SafePage.blocker.is_blocked(url_str):
            print(f"BLOCKED: {url_str}")
            return False

        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def javaScriptConsoleMessage(self, level, message, line, source) -> None:
        """Log JS console messages for debugging."""
        level_names = {0: "INFO", 1: "WARNING", 2: "ERROR"}
        level_name = level_names.get(level, "LOG")
        print(f"[JS Console {level_name}] {source}:{line} - {message}")


class BFSBPage(SafePage):
    """BFSB custom page that works with local HTTP server backend."""

    def __init__(self, profile: QWebEngineProfile, parent) -> None:
        super().__init__(profile, parent)
        self._bfsb_internal_nav = False  # Track bfsb:// navigations to suppress progress bar

    def acceptNavigationRequest(
        self, url: QUrl, nav_type: int, is_main_frame: bool
    ) -> bool:
        url_str = url.toString()
        path = url.path().lower() if hasattr(url, "path") else url_str.lower()

        # Handle BFSB internal URLs (bfsb:// scheme)
        if url_str.startswith("bfsb://"):
            self._bfsb_internal_nav = True
            url_lower = url_str.lower()
            print(f"[BFSBPage] Intercepted: {url_str}")
            if hasattr(self, '_main_window') and self._main_window:
                main = self._main_window
                if url_lower.startswith("bfsb://goback"):
                    print("[BFSBPage] -> go_back()")
                    main.go_back()
                elif url_lower.startswith("bfsb://goforward"):
                    print("[BFSBPage] -> go_forward()")
                    main.go_forward()
                elif url_lower.startswith("bfsb://reload"):
                    print("[BFSBPage] -> reload()")
                    main.reload()
                elif url_lower.startswith("bfsb://navigate"):
                    from urllib.parse import urlparse, parse_qs
                    parsed = urlparse(url_str)
                    query = parse_qs(parsed.query)
                    if 'url' in query:
                        print(f"[BFSBPage] -> navigate({query['url'][0]})")
                        main.navigate(query['url'][0])
                elif url_lower.startswith("bfsb://switchtab"):
                    from urllib.parse import urlparse, parse_qs
                    parsed = urlparse(url_str)
                    query = parse_qs(parsed.query)
                    if 'index' in query:
                        print(f"[BFSBPage] -> switch_tab({query['index'][0]})")
                        main.switch_tab(int(query['index'][0]))
                elif url_lower.startswith("bfsb://closetab"):
                    from urllib.parse import urlparse, parse_qs
                    parsed = urlparse(url_str)
                    query = parse_qs(parsed.query)
                    if 'index' in query:
                        print(f"[BFSBPage] -> close_tab({query['index'][0]})")
                        main.close_tab(int(query['index'][0]))
                elif url_lower.startswith("bfsb://newtab"):
                    print("[BFSBPage] -> new_tab()")
                    main.new_tab()
                elif url_lower.startswith("bfsb://about"):
                    print("[BFSBPage] -> _show_about_dialog()")
                    main._show_about_dialog()
                elif url_lower.startswith("bfsb://preferences"):
                    print("[BFSBPage] -> _show_preferences_dialog()")
                    main._show_preferences_dialog()
            # Clear URL to prevent fallback navigation
            return False

        # Block dangerous file extensions
        for ext in SECURITY_CONFIG.BAD_EXTENSIONS:
            if path.endswith(ext):
                print(f"BLOCKED DOWNLOAD: {url_str}")
                return False

        # Block URLs on blocklist
        if BFSBPage.blocker and BFSBPage.blocker.is_blocked(url_str):
            print(f"BLOCKED: {url_str}")
            return False

        # Allow all navigation to local server (our backend)
        if "localhost" in url_str or "127.0.0.1" in url_str or ":8888" in url_str or ":8889" in url_str:
            return super().acceptNavigationRequest(url, nav_type, is_main_frame)

        # Allow all other navigation (external links)
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def javaScriptConsoleMessage(self, level, message, line, source) -> None:
        """Log JS console messages for debugging."""
        level_names = {0: "INFO", 1: "WARNING", 2: "ERROR"}
        level_name = level_names.get(level, "LOG")
        print(f"[JS Console {level_name}] {source}:{line} - {message}")


def create_web_profile() -> QWebEngineProfile:
    """Create and configure WebEngine profile."""
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtWebEngineCore import QWebEngineProfile
    app = QApplication.instance()
    profile = QWebEngineProfile(APP_CONFIG.WINDOW_TITLE, app)
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
    )
    # Disable service workers, webauthn, and other background features
    if hasattr(QWebEngineProfile, 'setSpellCheckEnabled'):
        profile.setSpellCheckEnabled(False)
    if hasattr(QWebEngineProfile, 'setHttpCacheMaximumSize'):
        profile.setHttpCacheMaximumSize(50 * 1024 * 1024)  # 50MB limit
    return profile


def configure_web_settings(settings: QWebEngineSettings) -> None:
    """Apply security-focused WebEngine settings + rendering fixes."""
    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.AllowGeolocationOnInsecureOrigins, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.DnsPrefetchEnabled, False)

    # Enable JavaScript (required for our bridge)
    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, False)

    # Disable features that cause Wayland/Mojo errors
    if hasattr(QWebEngineSettings.WebAttribute, 'ServiceWorkersEnabled'):
        settings.setAttribute(QWebEngineSettings.WebAttribute.ServiceWorkersEnabled, False)
    if hasattr(QWebEngineSettings.WebAttribute, 'WebRTCPublicInterfacesOnly'):
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebRTCPublicInterfacesOnly, True)
    if hasattr(QWebEngineSettings.WebAttribute, 'ScrollAnimatorEnabled'):
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, False)
    if hasattr(QWebEngineSettings.WebAttribute, 'PdfViewerEnabled'):
        settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)
    if hasattr(QWebEngineSettings.WebAttribute, 'FullScreenSupportEnabled'):
        settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, False)

    # Force software rendering path
    settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, False)
    if hasattr(QWebEngineSettings.WebAttribute, 'Accelerated2dCanvasEnabled'):
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, False)


def create_web_view(profile: QWebEngineProfile, blocker: URLBlocker, page_class=None, window=None) -> QWebEngineView:
    """Create a configured WebEngine view with runJavaScript for UI sync."""
    from PyQt6.QtGui import QColor
    from PyQt6.QtCore import QTimer

    view = QWebEngineView()
    if page_class is None:
        page_class = SafePage
    page = page_class(profile, view)
    view.setPage(page)
    if page_class == SafePage:
        SafePage.blocker = blocker
    elif page_class == BFSBPage:
        BFSBPage.blocker = blocker
    # URL request interceptor is set once on the profile in _init_profile
    configure_web_settings(profile.settings())

    # Set background color to match theme (prevents white flash / black screen)
    page.setBackgroundColor(QColor("#0a0a12"))  # C.BG_0 equivalent

    # Diagnostic: log load progress and errors
    def _on_load_started():
        print(f"[WebEngine] Load started: {view.url().toString()}")

    def _on_load_finished(ok: bool):
        status = "OK" if ok else "FAILED"
        print(f"[WebEngine] Load finished ({status}): {view.url().toString()}")
        if not ok:
            print(f"[WebEngine] Page error - URL: {view.url().toString()}")

    def _on_load_progress(progress: int):
        if progress % 25 == 0:  # Log every 25%
            print(f"[WebEngine] Load progress: {progress}%")

    view.loadStarted.connect(_on_load_started)
    view.loadFinished.connect(_on_load_finished)
    view.loadProgress.connect(_on_load_progress)

    # Also log URL changes
    def _on_url_changed(url):
        print(f"[WebEngine] URL changed: {url.toString()}")
    view.urlChanged.connect(_on_url_changed)

    # UI sync via runJavaScript (no WebChannel - avoids Mojo IPC segfaults)
    # Python gets state from QWebEngineView directly and pushes to JS
    if window is not None:
        def _sync_ui():
            try:
                # Get state from Python (synchronous, no callbacks needed)
                current_url = view.url().toString()

                # Skip internal bfsb:// URLs and data: URLs (for home page)
                if current_url.startswith("bfsb://") or current_url.startswith("data:"):
                    display_url = ""
                else:
                    display_url = current_url

                can_go_back = view.history().canGoBack()
                can_go_forward = view.history().canGoForward()
                back_disabled = "true" if not can_go_back else "false"
                forward_disabled = "true" if not can_go_forward else "false"

                # Push state to JS - update nav buttons
                view.page().runJavaScript(f"""
                    (function() {{
                        var backBtn = document.getElementById('btn-back');
                        var forwardBtn = document.getElementById('btn-forward');
                        if (backBtn) backBtn.disabled = {back_disabled};
                        if (forwardBtn) forwardBtn.disabled = {forward_disabled};
                    }})();
                """)

                # Push URL to URL bar (only if not focused)
                # Use json.dumps for safe string interpolation
                display_url_js = json.dumps(display_url)
                view.page().runJavaScript(f"""
                    (function() {{
                        var urlInput = document.getElementById('urlInput');
                        if (urlInput && document.activeElement !== urlInput) {{
                            urlInput.value = {display_url_js};
                        }}
                    }})();
                """)
            except Exception as e:
                print(f"[WebEngine] UI sync failed: {e}")

        # Sync UI periodically
        sync_timer = QTimer()
        sync_timer.timeout.connect(_sync_ui)
        sync_timer.start(500)

        # Initial sync after load
        def _on_load_finished_for_sync(ok: bool):
            if ok:
                QTimer.singleShot(100, _sync_ui)

        view.loadFinished.connect(_on_load_finished_for_sync)

    # Diagnostic: render process monitoring
    def _on_render_process_terminated(status, exit_code):
        print(f"[WebEngine] Render process terminated: status={status}, exit_code={exit_code}")

    page.renderProcessTerminated.connect(_on_render_process_terminated)

    return view