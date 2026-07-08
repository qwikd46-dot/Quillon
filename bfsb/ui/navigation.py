"""Navigation bar — minimal menu button only."""

from __future__ import annotations

from typing import Callable, Optional
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QDialog,
    QVBoxLayout,
    QTextBrowser,
    QGraphicsBlurEffect,
)
from PyQt6.QtGui import QIcon, QPainterPath, QPixmap, QPainter, QPen, QColor, QFont

from .styles import DIMS, C


# ─────────────────────────────────────────────────────────────────────────────
# NAV BUTTON
# ─────────────────────────────────────────────────────────────────────────────

class NavButton(QPushButton):
    """Menu button."""

    def __init__(self, icon: str, tooltip: str, size: int = DIMS.NAV_BTN_SIZE):
        super().__init__()
        self.setFixedSize(size, size)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText(icon)
        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C.TEXT_1};
                border: none;
                font-size: 16px;
                font-weight: 400;
                border-radius: {size // 2}px;
            }}
            QPushButton:hover {{
                background: rgba(124, 106, 245, 0.12);
                color: #9090c8;
            }}
            QPushButton:pressed {{
                background: rgba(124, 106, 245, 0.2);
            }}
            QPushButton:disabled {{
                color: {C.TEXT_3};
            }}
        """)


# ─────────────────────────────────────────────────────────────────────────────
# NAVIGATION BAR
# ─────────────────────────────────────────────────────────────────────────────

class NavigationBar(QWidget):
    """
    Minimal navigation bar — only menu button.
    [Menu]
    """

    def __init__(
        self,
        on_back: Callable[[], None],
        on_forward: Callable[[], None],
        on_reload: Callable[[], None],
        on_navigate: Callable[[str], None],  # kept for API compatibility, unused
    ):
        super().__init__()
        self.setFixedHeight(DIMS.NAV_BAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(f"""
            QWidget {{
                background: {C.BG_2};
                border-bottom: 1px solid {C.BORDER_0};
            }}
        """)

        self._progress_callback: Optional[Callable[[int], None]] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(DIMS.NAV_MARGIN_H, 0, DIMS.NAV_MARGIN_H, 0)
        layout.setSpacing(DIMS.NAV_SPACING)

        layout.addStretch()  # Push menu to the right

        # ── Menu button ──────────────────────────────────────────────────────
        self.btn_menu = NavButton("⋮", "Menu", DIMS.NAV_BTN_SIZE)
        layout.addWidget(self.btn_menu)

    # ── progress ──────────────────────────────────────────────────────────────

    def set_progress_callback(self, callback: Callable[[int], None]) -> None:
        self._progress_callback = callback

    def set_load_progress(self, progress: int) -> None:
        if self._progress_callback:
            self._progress_callback(progress)

    # ── public API (no-ops for compatibility) ────────────────────────────────

    def set_url(self, url: str) -> None:
        pass  # No URL bar to update

    def get_url(self) -> str:
        return ""

    def set_security_status(self, url: str, load_ok: bool, is_local: bool) -> None:
        pass  # No security badge

    def clear_focus(self) -> None:
        pass  # No input to clear

    def update_nav_buttons(self, can_go_back: bool, can_go_forward: bool) -> None:
        pass  # No nav buttons to update


# ─────────────────────────────────────────────────────────────────────────────
# BLUR OVERLAY
# ─────────────────────────────────────────────────────────────────────────────

class _BlurOverlay(QWidget):
    """Full-screen overlay with blur effect for modal dialog backgrounds."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hide()
        blur = QGraphicsBlurEffect()
        blur.setBlurRadius(15)
        self.setGraphicsEffect(blur)
        self.setStyleSheet("background: rgba(0, 0, 0, 0.3);")

    def show_overlay(self) -> None:
        parent = self.parentWidget()
        if parent:
            self.setGeometry(0, 0, parent.width(), parent.height())
            self.raise_()
            self.show()

    def hide_overlay(self) -> None:
        self.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        parent = self.parentWidget()
        if parent and self.isVisible():
            self.setGeometry(0, 0, parent.width(), parent.height())


# ─────────────────────────────────────────────────────────────────────────────
# ABOUT DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class AboutDialog(QDialog):
    """About dialog for BFSB Browser."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About BFSB")
        self.setFixedSize(400, 380)
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._overlay_parent = self._find_overlay_parent()
        self._blur_overlay = _BlurOverlay(self._overlay_parent) if self._overlay_parent else None

        container = QWidget(self)
        container.setObjectName("aboutDialog")
        container.setStyleSheet("""
            QWidget#aboutDialog {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1e1e32, stop:1 #18182a);
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 18px;
            }
            QLabel {
                background: transparent;
                border: none;
                color: #eeeef8;
            }
            QTextBrowser {
                background: transparent;
                border: none;
                color: #b8b8c8;
                font-size: 13px;
            }
            QPushButton {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
                color: #9090b8;
                font-size: 13px;
                font-weight: 500;
                padding: 8px 24px;
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
        layout.setSpacing(14)

        title = QLabel("BFSB Browser")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #eeeef8; letter-spacing: -0.01em;")
        layout.addWidget(title)

        subtitle = QLabel("Browser for Safe Browsing")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("font-size: 13px; color: #7c6af5; font-weight: 500;")
        layout.addWidget(subtitle)

        version = QLabel("Version 1.0.0")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setStyleSheet("font-size: 12px; color: #5a5a7a;")
        layout.addWidget(version)

        layout.addSpacing(6)

        desc = QTextBrowser()
        desc.setOpenExternalLinks(True)
        desc.setHtml("""
            <p style="text-align:center; margin:0; color:#b8b8c8;">
                A privacy-focused browser built on <b style="color:#eeeef8;">Qt WebEngine</b>
                with a local private search engine.<br><br>
                No tracking &nbsp;&bull;&nbsp; No telemetry &nbsp;&bull;&nbsp; No compromises
            </p>
            <p style="text-align:center; margin:14px 0 0; color:#b8b8c8;">
                Started <b style="color:#eeeef8;">2026</b> by
                <b style="color:#7c6af5;">Binwalk</b><br>
                Built with Python 3, PyQt6, QtWebEngine
            </p>
        """)
        desc.setFixedHeight(110)
        layout.addWidget(desc)

        layout.addSpacing(6)

        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

    def _find_overlay_parent(self) -> QWidget | None:
        parent = self.parent()
        while parent and not parent.isWindow():
            parent = parent.parent()
        if not parent or not isinstance(parent, QMainWindow):
            return None
        return parent

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._blur_overlay:
            self._blur_overlay.show_overlay()

    def closeEvent(self, event) -> None:
        if self._blur_overlay:
            self._blur_overlay.hide_overlay()
        super().closeEvent(event)