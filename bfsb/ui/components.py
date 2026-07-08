"""Reusable UI components for the BFSB browser."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget, QHBoxLayout, QSizePolicy

from .styles import DIMS, STYLES


def _s(key: str) -> str:
    """Get style from STYLES dict."""
    return STYLES.get(key, "")


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
