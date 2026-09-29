"""Tab bar — real browser-style tabs with favicons, titles, close buttons, shrink-to-fit."""

from __future__ import annotations

from typing import Callable, Optional, List
from PyQt6.QtCore import Qt, QEvent, QRect
from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QSizePolicy,
    QPushButton,
    QLabel,
)
from PyQt6.QtGui import QPixmap, QEnterEvent, QMouseEvent

from .styles import DIMS, C


class TabCloseButton(QPushButton):
    """Close button (×) — always visible on active tab, hover-only on inactive."""

    def __init__(self, active: bool = False):
        super().__init__("×")
        self.setFixedSize(DIMS.CLOSE_BTN_SIZE, DIMS.CLOSE_BTN_SIZE)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._active = active
        self._update_style()

    def _update_style(self) -> None:
        if self._active:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {C.TEXT_2};
                    border: none;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 0;
                    min-width: 18px; max-width: 18px;
                    min-height: 18px; max-height: 18px;
                    border-radius: {DIMS.CLOSE_BTN_SIZE // 2}px;
                }}
                QPushButton:hover {{
                    background: {C.ERROR};
                    color: white;
                }}
            """)
            self.setVisible(True)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {C.TEXT_3};
                    border: none;
                    font-size: 14px;
                    font-weight: bold;
                    padding: 0;
                    min-width: 18px; max-width: 18px;
                    min-height: 18px; max-height: 18px;
                    border-radius: {DIMS.CLOSE_BTN_SIZE // 2}px;
                }}
                QPushButton:hover {{
                    background: {C.ERROR};
                    color: white;
                }}
            """)
            self.setVisible(False)

    def set_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self._update_style()

    def enterEvent(self, event: QEnterEvent) -> None:
        if not self._active:
            self.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        if not self._active:
            self.setVisible(False)
        super().leaveEvent(event)


class TabLabel(QLabel):
    """Tab title label with ellipsis truncation."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self._active = False
        self._update_style()

    def set_active(self, active: bool) -> None:
        self._active = active
        self._update_style()

    def _update_style(self) -> None:
        if self._active:
            self.setStyleSheet(f"""
                color: {C.TEXT_0};
                font-size: {DIMS.TAB_FONT_SIZE}px;
                font-weight: 500;
                background: transparent;
                border: none;
                padding: 0 4px;
            """)
        else:
            self.setStyleSheet(f"""
                color: {C.TEXT_2};
                font-size: {DIMS.TAB_FONT_SIZE}px;
                font-weight: 400;
                background: transparent;
                border: none;
                padding: 0 4px;
            """)


class FaviconLabel(QLabel):
    """Small favicon image label for tabs."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(DIMS.FAVICON_SIZE, DIMS.FAVICON_SIZE)
        self.setScaledContents(True)
        self.setStyleSheet("background: transparent; border-radius: 3px;")
        self._default_pixmap = None

    def set_favicon(self, pixmap: Optional[QPixmap]) -> None:
        if pixmap and not pixmap.isNull():
            self.setPixmap(pixmap.scaled(
                DIMS.FAVICON_SIZE, DIMS.FAVICON_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))
        elif self._default_pixmap:
            self.setPixmap(self._default_pixmap)
        else:
            self.clear()

    def set_default_favicon(self, pixmap: QPixmap) -> None:
        self._default_pixmap = pixmap


class TabWidget(QWidget):
    """Individual tab widget with favicon, label, and close button."""

    def __init__(
        self,
        index: int,
        title: str = "New Tab",
        favicon: Optional[QPixmap] = None,
        on_click: Optional[Callable[[int], None]] = None,
        on_close: Optional[Callable[[int], None]] = None,
    ):
        super().__init__()
        self._index = index
        self._active = False
        self._on_click = on_click
        self._on_close = on_close

        self.setFixedHeight(DIMS.TAB_HEIGHT)
        self.setMinimumWidth(DIMS.TAB_MIN_WIDTH)
        self.setMaximumWidth(DIMS.TAB_MAX_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(DIMS.TAB_MARGIN_H, 0, DIMS.TAB_MARGIN_H, 0)
        layout.setSpacing(DIMS.TAB_SPACING)

        # Favicon
        self.favicon = FaviconLabel()
        if favicon:
            self.favicon.set_favicon(favicon)
        layout.addWidget(self.favicon)

        # Title label
        self.title_label = TabLabel(title)
        layout.addWidget(self.title_label, 1)

        # Close button
        self.close_btn = TabCloseButton(active=False)
        self.close_btn.clicked.connect(lambda: self._on_close and self._on_close(self._index))
        layout.addWidget(self.close_btn)

        self._update_style()
        # Overlay button for safe click handling (prevents segfault)
        self._click_overlay = QPushButton(self)
        self._click_overlay.setFlat(True)
        self._click_overlay.setStyleSheet("background:transparent;")
        self._click_overlay.setCursor(Qt.CursorShape.PointingHandCursor)
        self._click_overlay.clicked.connect(lambda: self._on_click and self._on_click(self._index))
        self._click_overlay.setGeometry(self.rect())
        self._click_overlay.raise_()

    def _update_style(self) -> None:
        if self._active:
            self.setStyleSheet(f"""
                QWidget {{
                    background: {C.BG_3};
                    border-top: 1px solid {C.BORDER_1};
                    border-left: 1px solid {C.BORDER_1};
                    border-right: 1px solid {C.BORDER_1};
                    border-bottom: 1px solid {C.BG_0};
                    border-top-left-radius: {DIMS.TAB_RADIUS}px;
                    border-top-right-radius: {DIMS.TAB_RADIUS}px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QWidget {{
                    background: {C.BG_1};
                    border: 1px solid {C.BORDER_0};
                    border-bottom: 1px solid {C.BG_0};
                    border-top-left-radius: {DIMS.TAB_RADIUS}px;
                    border-top-right-radius: {DIMS.TAB_RADIUS}px;
                }}
                QWidget:hover {{
                    background: {C.BG_2};
                    border: 1px solid {C.BORDER_1};
                    border-bottom: 1px solid {C.BG_0};
                }}
            """)

    def set_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self.title_label.set_active(active)
            self.close_btn.set_active(active)
            self._update_style()

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_favicon(self, pixmap: Optional[QPixmap]) -> None:
        self.favicon.set_favicon(pixmap)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # Deprecated – click handling moved to overlay button to avoid segfault
        super().mousePressEvent(event)


    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_click_overlay"):
            self._click_overlay.setGeometry(self.rect())

class NewTabButton(QPushButton):
    """New tab (+) button at the end of the tab bar."""

    def __init__(self, on_click: Callable[[], None]):
        super().__init__("+")
        self.setFixedSize(DIMS.NEW_TAB_BTN_SIZE, DIMS.NEW_TAB_BTN_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("New tab")
        self.clicked.connect(on_click)
        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C.TEXT_2};
                border: none;
                font-size: 20px;
                font-weight: bold;
                border-radius: {DIMS.NEW_TAB_BTN_SIZE // 2}px;
            }}
            QPushButton:hover {{
                background: {C.BG_3};
                color: {C.TEXT_0};
            }}
            QPushButton:pressed {{
                background: {C.BG_4};
            }}
        """)


class TabBar(QWidget):
    """Tab bar — manages tabs, new tab button, and tab switching."""

    def __init__(
        self,
        on_tab_switch: Callable[[int], None],
        on_tab_close: Callable[[int], None],
        on_new_tab: Callable[[], None],
    ):
        super().__init__()
        self._on_tab_switch = on_tab_switch
        self._on_tab_close = on_tab_close
        self._on_new_tab = on_new_tab

        self._tabs: List[TabWidget] = []
        self._active_index = -1

        self.setFixedHeight(DIMS.TAB_BAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(f"background: {C.BG_1}; border-bottom: none;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(DIMS.TAB_BAR_MARGIN_H, 0, DIMS.TAB_BAR_MARGIN_H, 0)
        layout.setSpacing(DIMS.TAB_BAR_SPACING)

        # Container for tabs (will hold TabWidget instances)
        self._tabs_container = QWidget()
        self._tabs_layout = QHBoxLayout(self._tabs_container)
        self._tabs_layout.setContentsMargins(0, 0, 0, 0)
        self._tabs_layout.setSpacing(DIMS.TAB_BAR_SPACING)
        self._tabs_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._tabs_container, 1)

        # New tab button
        self.new_tab_btn = NewTabButton(self._on_new_tab)
        layout.addWidget(self.new_tab_btn)

    def add_tab(
        self,
        index: int,
        title: str = "New Tab",
        favicon: Optional[QPixmap] = None,
    ) -> TabWidget:
        """Add a new tab at the given index."""
        try:
            tab = TabWidget(
                index=index,
                title=title,
                favicon=favicon,
                on_click=self._handle_tab_click,
                on_close=self._handle_tab_close,
            )
            self._tabs.insert(index, tab)
            self._tabs_layout.insertWidget(index, tab)

            # Update indices for tabs after this one
            for i in range(index + 1, len(self._tabs)):
                self._tabs[i]._index = i

            # If first tab, make it active
            if len(self._tabs) == 1:
                self._set_active(0)

            return tab
        except Exception as e:
            print(f"[TabBar] ERROR in add_tab: {e}")
            import traceback
            traceback.print_exc()
            raise

    def _handle_tab_click(self, index: int) -> None:
        if 0 <= index < len(self._tabs):
            self._set_active(index)
            self._on_tab_switch(index)

    def _handle_tab_close(self, index: int) -> None:
        if 0 <= index < len(self._tabs):
            self._on_tab_close(index)

    def close_tab(self, index: int) -> None:
        """Close tab at index."""
        try:
            if 0 <= index < len(self._tabs):
                tab = self._tabs.pop(index)
                self._tabs_layout.removeWidget(tab)
                tab.deleteLater()

                # Update indices
                for i, t in enumerate(self._tabs):
                    t._index = i

                # Handle active tab changes
                if len(self._tabs) == 0:
                    self._active_index = -1
                    self._on_new_tab()
                elif self._active_index >= len(self._tabs):
                    self._active_index = len(self._tabs) - 1
                    self._set_active(self._active_index)
                elif self._active_index == index:
                    # Closed the active tab
                    if self._active_index >= len(self._tabs):
                        self._active_index = len(self._tabs) - 1
                    self._set_active(self._active_index)
                elif self._active_index > index:
                    # Closed a tab before the active one
                    self._active_index -= 1

                # Refresh all tab styles
                for i, tab in enumerate(self._tabs):
                    tab.set_active(i == self._active_index)
        except Exception as e:
            print(f"[TabBar] ERROR in close_tab: {e}")
            import traceback
            traceback.print_exc()
            raise

    def _set_active(self, index: int) -> None:
        if self._active_index == index:
            return

        # Deactivate old
        if 0 <= self._active_index < len(self._tabs):
            self._tabs[self._active_index].set_active(False)

        # Activate new
        self._active_index = index
        if 0 <= index < len(self._tabs):
            self._tabs[index].set_active(True)

    def get_active_index(self) -> int:
        return self._active_index

    def get_tab(self, index: int) -> Optional[TabWidget]:
        if 0 <= index < len(self._tabs):
            return self._tabs[index]
        return None

    def count(self) -> int:
        return len(self._tabs)

    def update_tab_title(self, index: int, title: str) -> None:
        if 0 <= index < len(self._tabs):
            self._tabs[index].set_title(title)

    def update_tab_favicon(self, index: int, pixmap: Optional[QPixmap]) -> None:
        if 0 <= index < len(self._tabs):
            self._tabs[index].set_favicon(pixmap)