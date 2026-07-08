"""BFSB — Browser for Safe Browsing.

A privacy-focused browser built on Qt WebEngine with local metasearch engine.
"""

# QtWebEngine flags - MUST be set before ANY Qt import
import os
import sys

# Force XCB platform to avoid Wayland ozone issues
os.environ["QT_QPA_PLATFORM"] = "xcb"
os.environ["QT_WEBENGINE_DISABLE_WAYLAND"] = "1"

# Use software rendering backend to avoid GPU/compositor black screen
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ["QT_WEBENGINE_DISABLE_GPU"] = "1"

# Chromium flags: disable GPU compositing, sandbox, dev-shm
# Use ONLY software rendering - no SwiftShader conflicts
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--no-sandbox "
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
    "--disable-features=VizDisplayCompositor,UseSkiaRenderer,CanvasOopRasterization "
    "--num-raster-threads=1 "
    "--renderer-process-limit=1 "
)
os.environ["QTWEBENGINEPROCESS_PATH"] = "/usr/lib/qt6/QtWebEngineProcess"
os.environ["QTWEBENGINE_RESOURCES_PATH"] = "/usr/share/qt6/resources"

# Disable Qt logging spam
os.environ["QT_LOGGING_RULES"] = "qt.webenginecontext.debug=false;qt.webenginecontext.warning=false"

from PyQt6.QtWidgets import QApplication

from bfsb.core import PATHS, URLBlocker
from bfsb.ui import BFSBWindow


def main() -> int:
    """Application entry point."""
    # Ensure directories exist
    PATHS.ensure_dirs()

    # Initialize core services
    blocker = URLBlocker()

    # Create Qt application
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Application identity (for desktop integration, taskbar, window icons)
    app.setApplicationName("BFSB")
    app.setApplicationDisplayName("BFSB")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("Binwalk")
    app.setOrganizationDomain("bfsb.browser")

    # Set application window icon
    from PyQt6.QtGui import QIcon
    app.setWindowIcon(QIcon("/home/zon/bfsb/bfsb_icon.png"))

    # Apply palette
    from bfsb.ui.styles import get_palette
    app.setPalette(get_palette())

    # Create and show main window
    window = BFSBWindow(blocker)
    window.show()

    print("BFSB started")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())