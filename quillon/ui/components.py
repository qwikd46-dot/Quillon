"""Reusable UI components for the Quillon browser."""

from PyQt6.QtCore import (
    QPointF,
    QRectF,
    Qt,
    QPropertyAnimation,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget, QHBoxLayout, QSizePolicy

from .styles import DIMS, STYLES


def _s(key: str) -> str:
    """Get style from STYLES dict."""
    return STYLES.get(key, "")


# ────────────────────────────────
# Toggle Switch
# ────────────────────────────────

class ToggleSwitch(QWidget):
    """Animated toggle switch. Violet track when on, keyboard accessible.

    Emits ``toggled(bool)`` on user interaction only — programmatic
    ``setChecked`` updates the visuals without re-emitting.
    """

    toggled = pyqtSignal(bool)

    _TRACK_ON = QColor("#7c6cf7")
    _TRACK_OFF = QColor("#2a2f5c")
    _THUMB = QColor("#ffffff")
    _FOCUS_RING = QColor(124, 108, 247, 90)

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(46, 26)
        self._checked = checked
        self._pos = 1.0 if checked else 0.0
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"position", self)
        self._anim.setDuration(180)

    def get_position(self) -> float:
        return self._pos

    def set_position(self, value: float) -> None:
        self._pos = max(0.0, min(1.0, value))
        self.update()

    position = pyqtProperty(float, get_position, set_position)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        if checked == self._checked:
            return
        self._checked = checked
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
            event.accept()
            return
        super().keyPressEvent(event)

    def _update_thumb(self) -> None:
        self._pos = self.get_position()
        self.update()

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(46, 26)
        self._checked = checked
        self._pos = 1.0 if checked else 0.0
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"position", self)
        self._anim.setDuration(180)
        self._anim.finished.connect(self._update_thumb)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Interpolate track color by animation position (animated transition)
        t = self._pos
        r = int(124 + (124 - 42) * t)  # 42 (#2a2f5c) → 124 (#7c6cf7) via position
        g = int(108 + (108 - 47) * t)
        b = int(247 + (247 - 92) * t)
        track = QColor(r, g, b)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 46, 26), 13, 13)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            ring = QColor(self._TRACK_ON)
            ring.setAlpha(110)
            pen = p.pen()
            pen.setWidth(2)
            pen.setColor(ring)
            p.setPen(pen)
            p.drawRoundedRect(QRectF(1, 1, 44, 24), 12, 12)
            p.setPen(Qt.PenStyle.NoPen)
        thumb_x = 3 + t * (46 - 6 - 20)
        p.setBrush(self._THUMB)
        p.drawEllipse(QPointF(thumb_x + 10, 13), 10, 10)
        p.end()


def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


# ────────────────────────────────
# Tab Close Button
# ────────────────────────────────

class TabCloseButton(QPushButton):
    """Close button that appears on hover for inactive, always for active."""

    def __init__(self, active: bool = False) -> None:
        super().__init__("×")
        self.setFixedSize(DIMS.CLOSE_BTN_SIZE, DIMS.CLOSE_BTN_SIZE)
        self.applied_as = None
        self.set_active(active)

    def set_active(self, active: bool) -> None:
        """Update button style for active/inactive state."""
        if active:
            self.setStyleSheet(_s("TAB_CLOSE_ACTIVE"))
            self.setVisible(True)
            self.applied_as = "active"
        else:
            self.setStyleSheet(_s("TAB_CLOSE_INACTIVE"))
            self.setVisible(False)
            self.applied_as = "hidden"

    def on_hover_enter(self) -> None:
        """Show close button when hovering an inactive tab."""
        if self.applied_as != "active":
            self.setStyleSheet(_s("TAB_CLOSE_HOVER"))
            self.setVisible(True)

    def on_hover_leave(self) -> None:
        """Hide close button when mouse leaves an inactive tab."""
        if self.applied_as != "active":
            self.setVisible(False)


# ────────────────────────────────
# Favicon Label
# ────────────────────────────────

class FaviconLabel(QLabel):
    """Small favicon image label for tabs."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(DIMS.ICON_SIZE, DIMS.ICON_SIZE)
        self.setScaledContents(True)
        self.setStyleSheet("background: transparent;")


# ────────────────────────────────
# Tab Label
# ────────────────────────────────

class TabLabel(QLabel):
    """Tab title label — auto-truncated with ellipsis."""

    def __init__(self, text: str = "", max_width: int = 200) -> None:
        super().__init__(text)
        self.setMaximumWidth(max_width)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred
        )
        self.setStyleSheet(_s("TAB_LABEL_INACTIVE"))
        self.setElideMode(Qt.TextElideMode.ElideRight)

    def set_active(self, active: bool) -> None:
        """Update style for active/inactive state."""
        if active:
            self.setStyleSheet(_s("TAB_LABEL_ACTIVE"))
        else:
            self.setStyleSheet(_s("TAB_LABEL_INACTIVE"))


# ────────────────────────────────
# Security Badge
# ────────────────────────────────

class SecurityBadge(QLabel):
    """Security status badge (secure/insecure)."""

    def __init__(self) -> None:
        super().__init__()
        self.setVisible(False)

    def set_secure(self) -> None:
        """Show secure badge."""
        self.setText("🔒 Secure")
        self.setStyleSheet(_s("SECURE_BADGE"))
        self.setVisible(True)

    def set_insecure(self) -> None:
        """Show insecure badge."""
        self.setText("Insecure")
        self.setStyleSheet(_s("INSECURE_BADGE"))
        self.setVisible(True)

    def hide_badge(self) -> None:
        """Hide badge."""
        self.setVisible(False)


# ────────────────────────────────
# Icon Button
# ────────────────────────────────

class IconButton(QPushButton):
    """Icon-only button with consistent styling."""

    def __init__(
        self,
        text: str,
        size: tuple[int, int] = (36, 36),
        style: str | None = None,
        tooltip: str = "",
    ) -> None:
        super().__init__(text)
        self.setFixedSize(*size)
        self.setStyleSheet(style or _s("NAV_BUTTON"))
        if tooltip:
            self.setToolTip(tooltip)


# ────────────────────────────────
# Tab Widget Builder
# ────────────────────────────────

def create_tab_widget(
    title: str,
    favicon: FaviconLabel | None = None,
    close_callback=None,
    index: int = 0,
) -> QWidget:
    """Create a tab widget with favicon, label, and ✕ close button."""
    tab = QWidget()
    tab.setFixedHeight(DIMS.TAB_HEIGHT)
    tab.setMinimumWidth(DIMS.TAB_MIN_WIDTH)
    tab.setMaximumWidth(DIMS.TAB_MAX_WIDTH)
    tab.setSizePolicy(
        QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    )
    tab.setProperty("active", False)
    tab.setStyleSheet(_s("TAB_INACTIVE"))

    layout = QHBoxLayout(tab)
    layout.setContentsMargins(8, 0, 6, 0)
    layout.setSpacing(6)

    if favicon is None:
        favicon = FaviconLabel()
    layout.addWidget(favicon)

    label = TabLabel(title)
    layout.addWidget(label, 1)

    close_btn = TabCloseButton()
    if close_callback:
        close_btn.clicked.connect(lambda _, t=tab: close_callback(t))
    layout.addWidget(close_btn)

    tab.favicon = favicon
    tab.title_label = label
    tab.close_btn = close_btn

    return tab


def update_tab_style(
    tab: QWidget,
    is_active: bool,
    close_btn: QPushButton | None = None,
) -> None:
    """Update tab appearance for active/inactive state."""
    if is_active:
        tab.setStyleSheet(_s("TAB_ACTIVE"))
        tab.setProperty("active", True)
    else:
        tab.setStyleSheet(_s("TAB_INACTIVE"))
        tab.setProperty("active", False)

    for child in tab.findChildren(QLabel):
        if hasattr(child, 'set_active'):
            child.set_active(is_active)

    if close_btn and hasattr(close_btn, 'set_active'):
        close_btn.set_active(is_active)
