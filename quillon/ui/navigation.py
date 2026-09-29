"""Navigation bar — minimal menu button only."""

from __future__ import annotations

from typing import Callable, Optional
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QAbstractAnimation, QPoint
from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QDialog,
    QVBoxLayout,
    QTextBrowser,
    QMainWindow,
    QLabel,
)
from PyQt6.QtGui import QIcon, QPixmap, QFont

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
# ABOUT DIALOG (used by sidebar / drop-down menu — see below)
# ─────────────────────────────────────────────────────────────────────────────

class AboutDialog(QDialog):
    """About dialog for Quillon Browser."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Quillon")
        self.setFixedSize(420, 360)
        # Frameless + StaysOnTop hint keeps it themed without OS chrome
        # but still reliably grabs clicks/focus when modal is shown.
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        # We are our own QWidget — no extra translucent child.

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
                min-width: 80px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.1);
                color: #eeeef8;
            }
            QPushButton:pressed {
                background: rgba(255, 255, 255, 0.18);
            }
            QPushButton:focus {
                outline: none;
                background: rgba(124, 106, 245, 0.18);
                color: #eeeef8;
                border: 1px solid #7c6af5;
            }
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QLabel("Quillon Browser")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 24px; font-weight: 700; color: #eeeef8; letter-spacing: -0.02em;")
        layout.addWidget(title)

        subtitle = QLabel("Browser for Safe Browsing")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("font-size: 13px; color: #7c6af5; font-weight: 600;")
        layout.addWidget(subtitle)

        version = QLabel("Version 1.0.0")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setStyleSheet("font-size: 12px; color: #7a7a8a; margin-top: 2px;")
        layout.addWidget(version)

        layout.addSpacing(8)

        desc = QTextBrowser()
        desc.setOpenExternalLinks(True)
        desc.setHtml("""
            <p style="text-align:center; margin:0; color:#c8c8d8;">
                A privacy-focused browser built on <b style="color:#ffffff;">Qt WebEngine</b>
                with a local private search engine.<br><br>
                <span style="color:#7c6af5; font-weight: 600;">No tracking &nbsp;&bull;&nbsp; No telemetry &nbsp;&bull;&nbsp; No compromises</span>
            </p>
            <p style="text-align:center; margin:14px 0 0; color:#a0a0b8; font-size:12px;">
                Started <b style="color:#eeeef8;">2026</b> by
                <b style="color:#7c6af5;">Binwalk</b><br>
                Built with Python 3, PyQt6, QtWebEngine
            </p>
        """)
        desc.setFixedHeight(120)
        layout.addWidget(desc)

        layout.addSpacing(4)

        # Close button — multiple mechanisms bound. Some users on
        # certain Qt/PyQt6 builds see the first click NOT fire while
        # focus is still being transferred into the dialog. Bind the
        # click to all three common close paths (accept/reject/close)
        # so any of them does the same thing: hide the dialog.
        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setDefault(True)
        close_btn.setAutoDefault(True)
        def _on_close():
            # Hand off to the overridden accept() — it plays the slide-
            # down animation and only then calls QDialog.accept().
            # No explicit hide() here: that would start a second
            # animation from the new (animating) position.
            self.accept()
        close_btn.clicked.connect(_on_close)
        # Also close on Esc by accepting close behavior in keyPressEvent.
        self.closeEvent = self._close_event_accept  # override below
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # The dialog itself fills the available space via container; just
        # wrap it in a layout with zero margins.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)

        # Centre on parent + grab focus + raise to top.
        if parent is not None:
            self.move(
                parent.x() + (parent.width() - self.width()) // 2,
                parent.y() + (parent.height() - self.height()) // 2,
            )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Reset the close guard so a future close can animate again.
        self._quillon_closing = False
        # Take focus and put the cursor on Close so the dialog reads
        # ALL keyboard/mouse input from the start — prevents the first
        # click from being eaten by a stale focused widget behind.
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._grab_focus)
        # Slide-up entrance: start the dialog ~its own height below its
        # final centred position and animate up. ~280ms, OutCubic for a
        # brisk landing (matches Quillon's teal-accent feel).
        # Recompute end_pos from parent in case __init__'s move() was
        # overridden by the window manager before this fired.
        end_pos = self.pos()
        parent = self.parent()
        if parent is not None and end_pos.x() == 0 and end_pos.y() == 0:
            end_pos = QPoint(
                parent.x() + (parent.width() - self.width()) // 2,
                parent.y() + (parent.height() - self.height()) // 2,
            )
            self.move(end_pos)
        # Drop the dialog just below its final position so we can animate up.
        off = self.frameGeometry().height()
        start_pos = QPoint(end_pos.x(), end_pos.y() + off)
        self.move(start_pos)
        if getattr(self, "_quillon_slide_anim", None) is not None:
            try:
                self._quillon_slide_anim.stop()
            except Exception:
                pass
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(280)
        anim.setStartValue(start_pos)
        anim.setEndValue(end_pos)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._quillon_slide_anim = anim

    def _close_event_accept(self, event) -> None:
        """Override closeEvent to play a slide-down animation first,
        then accept+hide. Esc and the window's X button route here.

        Subsequent close attempts (from the Close button's signal)
        are ignored — _animate_close() flips _quillon_closing on the
        first call and the rest fall through to a plain accept.
        """
        if getattr(self, "_quillon_closing", False):
            # Second close while animating — just accept and bail.
            QDialog.accept(self)
            if self.isVisible():
                self.hide()
            event.accept()
            return
        # We can't consume `event` and let the animation finish later —
        # QDialog would tear down before the animation completes. Instead
        # start the animation and tell the event loop we'll handle it.
        self._animate_close(finished_cb=lambda: (
            QDialog.accept(self),
            self.hide() if self.isVisible() else None,
        ))
        event.ignore()

    def _animate_close(self, finished_cb=None) -> None:
        """Slide the dialog back down (off the bottom of the window)
        before tearing it down. Mirrors the slide-up entrance.
        """
        if getattr(self, "_quillon_closing", False):
            return
        self._quillon_closing = True
        # Stop any in-flight slide-up so we don't fight it.
        if getattr(self, "_quillon_slide_anim", None) is not None:
            try:
                self._quillon_slide_anim.stop()
            except Exception:
                pass
        start_pos = self.pos()
        off = self.frameGeometry().height()
        end_pos = QPoint(start_pos.x(), start_pos.y() + off)
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(220)  # a touch faster than entrance
        anim.setStartValue(start_pos)
        anim.setEndValue(end_pos)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        if finished_cb is not None:
            anim.finished.connect(finished_cb)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._quillon_slide_anim = anim

    def accept(self) -> None:
        """Override QDialog.accept — play slide-down first."""
        if getattr(self, "_quillon_closing", False):
            return
        self._animate_close(finished_cb=lambda: QDialog.accept(self))

    def reject(self) -> None:
        """Override QDialog.reject — play slide-down first."""
        if getattr(self, "_quillon_closing", False):
            return
        self._animate_close(finished_cb=lambda: QDialog.reject(self))

    def hide(self) -> None:
        """Override QDialog.hide — slide down first, then hide."""
        if getattr(self, "_quillon_closing", False) or not self.isVisible():
            QDialog.hide(self)
            return
        self._animate_close(finished_cb=lambda: QDialog.hide(self))

    def _grab_focus(self) -> None:
        try:
            self.raise_()
            self.activateWindow()
            # Focus the Close button so Enter/Space dismiss immediately
            # and mouse click on it doesn't miss the first click.
            for w in self.findChildren(QPushButton):
                if w.text().strip().lower() == "close":
                    w.setFocus()
                    w.setDefault(True)
                    break
        except Exception:
            pass