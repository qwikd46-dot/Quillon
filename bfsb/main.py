"""BFSB — Browser for Safe Browsing.

A privacy-focused browser built on Qt WebEngine with local metasearch.
"""

import os
import sys

# Platform: prefer Wayland (we're on Hyprland), fall back to X11.
# On pure X11 the env is already correct; on Wayland we want the Wayland
# platform plugin to avoid the silent-fail when XCB has no DISPLAY.
# Override with BFSB_FORCE_XCB=1 if you need to debug X11-specific issues.
if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("BFSB_FORCE_XCB")):
    os.environ["QT_QPA_PLATFORM"] = "xcb"
    os.environ["QT_WEBENGINE_DISABLE_WAYLAND"] = "1"

# Use software rendering backend to avoid GPU/compositor black screen
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ["QT_WEBENGINE_DISABLE_GPU"] = "1"

# Chromium flags: disable GPU compositing, sandbox, dev-shm, service workers, Mojo IPC
# Use ONLY software rendering - no SwiftShader conflicts
_chromium_flags = (
    "--disable-dev-shm-usage "
    "--disable-gpu "
    "--disable-gpu-compositing "
    "--disable-accelerated-2d-canvas "
    "--disable-accelerated-video-decode "
    "--disable-webgl "
    "--disable-webgl2 "
    "--disable-3d-apis "
    "--disable-breakpad "
    "--disable-component-extensions-with-background-pages "
    "--disable-extensions "
    "--disable-plugins "
    "--disable-default-apps "
    "--disable-sync "
    "--disable-background-networking "
    "--disable-background-timer-throttling "
    "--disable-renderer-backgrounding "
    "--disable-features=VizDisplayCompositor,UseSkiaRenderer,CanvasOopRasterization,ServiceWorker,NetworkService,OutOfProcessNetworkService "
    "--disable-service-worker "
    "--disable-mojo-broker "
    "--disable-mojo-internal "
    "--disable-ipc-flooding-protection "
    "--num-raster-threads=1 "
    "--renderer-process-limit=1 "
    "--remote-debugging-port=9222 "
    "--remote-allow-origins=*"
)

# Network-level ad blocking: route Chromium through the BFSB mitmproxy addon
# which rewrites /youtubei/v1/* responses and strips ad placements BEFORE the
# page sees them (undetectable by anti-adblock checks). Fail-safe: if the
# proxy cannot start, no proxy flags are set and browsing works as before.
try:
    from bfsb.core.proxy_bootstrap import ensure_proxy_running
    _proxy = ensure_proxy_running()
    if _proxy:
        _port, _spki = _proxy
        _chromium_flags += (
            f" --proxy-server=http://127.0.0.1:{_port}"
            f" --ignore-certificate-errors-spki-list={_spki}"
        )
        print(f"[BFSB] Network ad-block proxy active on 127.0.0.1:{_port}")
    else:
        print("[BFSB] Proxy unavailable - running without network ad-block")
except Exception as _e:
    print(f"[BFSB] Proxy bootstrap failed: {_e}")

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = _chromium_flags

# Disable Qt logging spam
os.environ["QT_LOGGING_RULES"] = "qt.webenginecontext.debug=false;qt.webenginecontext.warning=false"

# Set Qt WebEngine paths to working locations
os.environ.setdefault("QTWEBENGINEPROCESS_PATH", "/usr/lib64/qt6/libexec/QtWebEngineProcess")
os.environ.setdefault("QTWEBENGINE_RESOURCES_PATH", "/home/binwalk/.local/lib/python3.14/site-packages/PyQt6/Qt6/resources")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineCore import QWebEngineUrlScheme

# Register bfsb:// custom URL scheme BEFORE QApplication creation.
# Guarded: webengine.py also registers it, so skip if already done.
bfsb_scheme = QWebEngineUrlScheme(b"bfsb")
bfsb_scheme.setSyntax(QWebEngineUrlScheme.Syntax.HostPortAndUserInformation)
bfsb_scheme.setDefaultPort(8889)
bfsb_scheme.setFlags(
    QWebEngineUrlScheme.Flag.SecureScheme |
    QWebEngineUrlScheme.Flag.CorsEnabled |
    QWebEngineUrlScheme.Flag.FetchApiAllowed |
    QWebEngineUrlScheme.Flag.ContentSecurityPolicyIgnored
)
try:
    QWebEngineUrlScheme.registerScheme(bfsb_scheme)
except RuntimeError:
    pass  # already registered

from bfsb.core import PATHS, URLBlocker
from bfsb.ui import BFSBWindow


def main() -> int:
    """Application entry point."""
    PATHS.ensure_dirs()
    blocker = URLBlocker()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("BFSB")
    app.setApplicationDisplayName("BFSB")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("Binwalk")
    app.setOrganizationDomain("bfsb.browser")
    # Tie the window to the installed bfsb.desktop entry. On Wayland this
    # sets the shell app_id to "bfsb" (instead of "python"), so the
    # taskbar/dock shows the name "BFSB" and the correct SVG/PNG icon
    # from the .desktop file instead of "bfsb.python" + a generic icon.
    app.setDesktopFileName("bfsb")

    from PyQt6.QtGui import QIcon
    app.setWindowIcon(QIcon("/home/binwalk/Downloads/bfsb/bfsb_icon.png"))

    from bfsb.ui.styles import get_palette
    app.setPalette(get_palette())

    window = BFSBWindow(blocker)
    window.show()
    print("BFSB started")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
