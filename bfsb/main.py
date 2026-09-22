"""BFSB — Browser for Safe Browsing.

A privacy-focused browser built on Qt WebEngine with local metasearch.
"""

import os
import sys

# Keep the platform override for the current Linux deployment, but do not
# force Chromium onto the slow software-rendering path. QtWebEngine can use
# the system compositor/GPU when available and fall back on its own.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
os.environ.setdefault("QT_WEBENGINE_DISABLE_WAYLAND", "1")
os.environ.setdefault("QTWEBENGINEPROCESS_PATH", "/usr/lib/qt6/QtWebEngineProcess")
os.environ.setdefault("QTWEBENGINE_RESOURCES_PATH", "/usr/share/qt6/resources")
os.environ.setdefault(
    "QT_LOGGING_RULES",
    "qt.webenginecontext.debug=false;qt.webenginecontext.warning=false",
)

from PyQt6.QtWidgets import QApplication

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

    from PyQt6.QtGui import QIcon
    icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bfsb_icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    from bfsb.ui.styles import get_palette
    app.setPalette(get_palette())

    window = BFSBWindow(blocker)
    window.show()
    print("BFSB started")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
