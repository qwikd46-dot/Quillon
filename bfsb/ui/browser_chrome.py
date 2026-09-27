"""Native PyQt browser chrome — tab bar + URL bar + nav buttons.

This widget sits ABOVE the QStackedWidget containing web views.
Unlike HTML-based chrome, it persists across page navigations because
it's a real Qt widget, not part of the web page DOM.
"""

from __future__ import annotations

from typing import Callable, Optional, List
from PyQt6.QtCore import QByteArray, QEvent, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLineEdit,
    QSizePolicy,
    QScrollArea,
    QLabel,
    QToolButton,
)
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from .styles import DIMS, C


_BOOKMARK_PATH = "M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"


# ══════════════════════════════════════════════════════════════════
# NATIVE TAB WIDGET
# ══════════════════════════════════════════════════════════════════

class NativeTab(QToolButton):
    """A single tab in the native tab bar — styled like the new GUI."""

    activated = pyqtSignal(int)
    closed = pyqtSignal(int)

    # Palette mirrors bfsb_combined.html :root tokens.
    _BG_TAB = "#171c33"
    _BG_TAB_ACTIVE = "#262c55"
    _BORDER = "#2b3355"
    _ACCENT = "#7b5cff"
    _TEXT = "#e8eaf2"
    _TEXT_DIM = "#9aa0b5"
    _TEXT_FAINT = "#6b7194"
    _HOVER = "#181e3d"

    def __init__(self, index: int, title: str = "New Tab"):
        super().__init__()
        self.setAutoRaise(False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._index = index
        self._active = False
        self.setFixedHeight(38)
        self.setFixedWidth(190)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(12)

        self.title_label = QLabel(title)
        self.title_label.setMaximumWidth(132)
        self.title_label.setStyleSheet(f"""
            color: {self._TEXT_DIM};
            font-size: 13px;
            background: transparent;
            border: none;
        """)
        self.title_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.title_label, 1)

        self.close_btn = QToolButton(self)
        self.close_btn.setText("✕")
        self.close_btn.setAutoRaise(True)
        self.close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_btn.setFixedSize(18, 18)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                border: none;
                color: {self._TEXT_DIM};
                font-size: 13px;
                border-radius: 8px;
            }}
            QToolButton:hover {{
                background: rgba(123, 92, 255, 0.25);
                color: {self._TEXT};
            }}
        """)
        self.close_btn.clicked.connect(self._on_close_clicked)
        layout.addWidget(self.close_btn)
        self.clicked.connect(self._on_clicked)
        self._close_visible = False
        self.close_btn.setVisible(False)

        self._update_style()

    def _update_style(self):
        if self._active:
            self.setStyleSheet(f"""
                QToolButton {{
                    background: {self._BG_TAB_ACTIVE};
                    border: 1px solid {self._ACCENT};
                    border-radius: 10px;
                    padding: 0;
                }}
                QToolButton:hover {{
                    background: {self._BG_TAB_ACTIVE};
                }}
                QToolButton:pressed {{
                    background: {self._BG_TAB_ACTIVE};
                }}
            """)
            self.title_label.setStyleSheet(f"""
                color: {self._TEXT};
                font-size: 13px;
                background: transparent;
                border: none;
            """)
            self._set_close_visible(True)
        else:
            self.setStyleSheet(f"""
                QToolButton {{
                    background: {self._BG_TAB};
                    border: 1px solid {self._BORDER};
                    border-radius: 10px;
                    padding: 0;
                }}
                QToolButton:hover {{
                    background: {self._HOVER};
                }}
                QToolButton:pressed {{
                    background: {self._HOVER};
                }}
            """)
            self.title_label.setStyleSheet(f"""
                color: {self._TEXT_DIM};
                font-size: 13px;
                background: transparent;
                border: none;
            """)
            self._set_close_visible(True)

    def _set_close_visible(self, visible: bool):
        self._close_visible = visible
        self.close_btn.setVisible(visible)

    def set_active(self, active: bool):
        if self._active != active:
            self._active = active
            self._update_style()

    def set_title(self, title: str):
        self.title_label.setText(title)
        self.title_label.setToolTip(title)

    def title(self) -> str:
        return self.title_label.text()

    def set_index(self, index: int):
        self._index = index

    def _on_clicked(self, _checked: bool = False) -> None:
        try:
            self.activated.emit(self._index)
        except Exception as error:
            print(f"[Chrome] tab activation failed: {error}")

    def _on_close_clicked(self, _checked: bool = False) -> None:
        try:
            self.closed.emit(self._index)
        except Exception as error:
            print(f"[Chrome] tab close failed: {error}")



# ══════════════════════════════════════════════════════════════════
# NATIVE TAB BAR
# ══════════════════════════════════════════════════════════════════

class NativeTabBar(QWidget):
    """Horizontal scrollable tab bar with tabs + new tab button."""

    tab_switched = pyqtSignal(int)
    tab_closed = pyqtSignal(int)
    new_tab_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setFixedHeight(50)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("""
            QWidget { background: #0e1220; border: none; }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        # Scroll area for tabs
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:horizontal { height: 6px; background: transparent; }
            QScrollBar::handle:horizontal { background: #2b3355; border-radius: 3px; }
        """)

        self._tabs_container = QWidget()
        self._tabs_container.setStyleSheet("background: transparent;")
        self._tabs_layout = QHBoxLayout(self._tabs_container)
        self._tabs_layout.setContentsMargins(0, 0, 0, 0)
        self._tabs_layout.setSpacing(8)
        self._tabs_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._scroll.setWidget(self._tabs_container)
        layout.addWidget(self._scroll, 1)

        # New tab button
        self.new_tab_btn = QPushButton("+")
        self.new_tab_btn.setFixedSize(32, 32)
        self.new_tab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_tab_btn.setToolTip("New Tab")
        self.new_tab_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #2b3355;
                color: #9aa0b5;
                font-size: 18px;
                border-radius: 16px;
            }
            QPushButton:hover {
                background: #181e3d;
                color: #e8eaf2;
            }
        """)
        self.new_tab_btn.clicked.connect(self.new_tab_requested.emit)
        self._tabs_layout.addWidget(self.new_tab_btn)

        self._tabs: List[NativeTab] = []
        self._active_index = -1

    def add_tab(self, index: int, title: str = "New Tab") -> NativeTab:
        tab = NativeTab(index, title)
        tab.activated.connect(self._on_tab_clicked)
        tab.closed.connect(self._on_tab_closed)
        self._tabs.insert(index, tab)
        self._tabs_layout.insertWidget(index, tab)
        # Update indices
        for i in range(index + 1, len(self._tabs)):
            self._tabs[i].set_index(i)
        if len(self._tabs) == 1:
            self._set_active(0)
        return tab

    def remove_tab(self, index: int):
        if 0 <= index < len(self._tabs):
            tab = self._tabs.pop(index)
            self._tabs_layout.removeWidget(tab)
            tab.deleteLater()
            for i, t in enumerate(self._tabs):
                t.set_index(i)
            if len(self._tabs) == 0:
                self._active_index = -1
            elif self._active_index >= len(self._tabs):
                self._active_index = len(self._tabs) - 1
                self._set_active(self._active_index)
            elif self._active_index > index:
                self._active_index -= 1
            # Refresh styles
            for i, t in enumerate(self._tabs):
                t.set_active(i == self._active_index)

    def _set_active(self, index: int):
        if self._active_index == index:
            return
        if 0 <= self._active_index < len(self._tabs):
            self._tabs[self._active_index].set_active(False)
        self._active_index = index
        if 0 <= index < len(self._tabs):
            self._tabs[index].set_active(True)

    def _on_tab_clicked(self, index: int):
        if not (0 <= index < len(self._tabs)):
            return
        try:
            self._set_active(index)
            self.tab_switched.emit(index)
        except Exception as error:
            print(f"[Chrome] tab switch dispatch failed: {error}")

    def _on_tab_closed(self, index: int):
        try:
            self.tab_closed.emit(index)
        except Exception as error:
            print(f"[Chrome] tab close dispatch failed: {error}")

    def set_active(self, index: int):
        self._set_active(index)

    def update_title(self, index: int, title: str):
        if 0 <= index < len(self._tabs):
            self._tabs[index].set_title(title)

    def count(self) -> int:
        return len(self._tabs)

    def update_all(self, titles: List[str], active: int) -> None:
        """Diff-based sync: update titles + active state in place when the
        tab count is unchanged (the common switch case — phase 1: the old
        clear()+re-add path measured 7-15ms per switch, O(tabs)); rebuild
        only when tabs were added/removed."""
        if len(self._tabs) == len(titles):
            for i, title in enumerate(titles):
                if self._tabs[i].title() != title:
                    self._tabs[i].set_title(title)
            self._set_active(active)
            return
        self.clear()
        for i, title in enumerate(titles):
            self.add_tab(i, title)
        self._set_active(active)

    def reorder_tabs(self, from_index: int, to_index: int) -> None:
        if not (0 <= from_index < len(self._tabs)):
            return
        to_index = max(0, min(to_index, len(self._tabs) - 1))
        if from_index == to_index:
            return
        tab = self._tabs.pop(from_index)
        self._tabs.insert(to_index, tab)
        self._tabs_layout.removeWidget(tab)
        self._tabs_layout.insertWidget(to_index, tab)
        for index, item in enumerate(self._tabs):
            item.set_index(index)
        self._set_active(self._active_index)

    def clear(self):
        for tab in self._tabs:
            self._tabs_layout.removeWidget(tab)
            tab.deleteLater()
        self._tabs.clear()
        self._active_index = -1


class RoundedAddressPill(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0), 21.0, 21.0)
        painter.fillPath(path, QColor("#141933"))
        painter.end()


# ══════════════════════════════════════════════════════════════════
# NATIVE NAVIGATION BAR (URL bar + buttons)
# ══════════════════════════════════════════════════════════════════

class NativeNavBar(QWidget):
    """Navigation bar with back/forward/reload + URL input."""

    back_clicked = pyqtSignal()
    forward_clicked = pyqtSignal()
    reload_clicked = pyqtSignal()
    url_submitted = pyqtSignal(str)
    bookmark_toggle = pyqtSignal()

    @staticmethod
    def _render_icon(svg: str, size: int = 20) -> QIcon:
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _line_icon(paths: str, color: str = "#9aa0b5", size: int = 20) -> QIcon:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'fill="none" stroke="{color}" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            f'{paths}</svg>'
        )
        return NativeNavBar._render_icon(svg, size)

    @staticmethod
    def _bookmark_icon(marked: bool) -> QIcon:
        fill = "#7b5cff" if marked else "none"
        stroke = "#7b5cff" if marked else "#9aa0b5"
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            f'<path d="{_BOOKMARK_PATH}" fill="{fill}" stroke="{stroke}" '
            'stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>'
            '</svg>'
        )
        return NativeNavBar._render_icon(svg)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(52)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("QWidget { background: #0e1220; border: none; }")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(8)

        self.btn_back = self._make_nav_button("", '<path d="M19 12H5m7-7-7 7 7 7"/>')
        self.btn_back.setToolTip("Back")
        self.btn_back.clicked.connect(self.back_clicked.emit)
        layout.addWidget(self.btn_back)

        self.btn_forward = self._make_nav_button("", '<path d="M5 12h14m-7-7 7 7-7 7"/>')
        self.btn_forward.setToolTip("Forward")
        self.btn_forward.clicked.connect(self.forward_clicked.emit)
        layout.addWidget(self.btn_forward)

        self.btn_reload = self._make_nav_button("", '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>')
        self.btn_reload.setToolTip("Reload")
        self.btn_reload.clicked.connect(self.reload_clicked.emit)
        layout.addWidget(self.btn_reload)

        self.address_pill = RoundedAddressPill()
        self.address_pill.setObjectName("addressPill")
        self.address_pill.setProperty("focused", False)
        self.address_pill.setFixedHeight(42)
        self.address_pill.setStyleSheet("""
            QFrame#addressPill {
                background: transparent;
                border: none;
                border-radius: 22px;
            }
            QFrame#addressPill[focused="true"] { border: none; }
        """)
        pill_layout = QHBoxLayout(self.address_pill)
        pill_layout.setContentsMargins(18, 11, 18, 11)
        pill_layout.setSpacing(14)
        self.search_icon = QLabel()
        self.search_icon.setPixmap(self._line_icon(
            '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>', size=18
        ).pixmap(QSize(18, 18)))
        self.search_icon.setFixedSize(18, 18)
        self.search_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.search_icon.setStyleSheet("color: #9aa0b5; font-size: 21px; background: transparent; border: none;")
        pill_layout.addWidget(self.search_icon)
        self.url_input = QLineEdit()
        self.url_input.setMinimumWidth(0)
        self.url_input.setObjectName("nativeUrlInput")
        self.url_input.setPlaceholderText("Search privately or enter URL...")
        self.url_input.setStyleSheet("""
            QLineEdit {
                background: transparent;
                border: none;
                color: #e8eaf2;
                font-size: 14px;
                padding: 0;
                 min-height: 18px;
                 max-height: 18px;
            }
            QLineEdit:focus { border: none; }
        """)
        self.url_input.setFixedHeight(18)
        self.url_input.returnPressed.connect(self._on_url_submit)
        self.url_input.installEventFilter(self)
        pill_layout.addWidget(self.url_input, 1)
        self.btn_bookmark = QPushButton()
        self.btn_bookmark.setObjectName("nativeBookmark")
        self.btn_bookmark.setIcon(self._bookmark_icon(False))
        self.btn_bookmark.setIconSize(QSize(18, 18))
        self.btn_bookmark.setFixedSize(18, 18)
        self.btn_bookmark.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_bookmark.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_bookmark.setToolTip("Bookmark this page")
        self.btn_bookmark.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #9aa0b5;
                 font-size: 18px;
                 padding: 0;
             }
             QPushButton:focus { outline: none; border: none; background: transparent; }
             QPushButton:hover { color: #7b5cff; background: transparent; }
            QPushButton[bookmarked="true"] { color: #7b5cff; background: transparent; }
        """)
        self.btn_bookmark.setProperty("bookmarked", False)
        self.btn_bookmark.clicked.connect(self._on_bookmark_clicked)
        pill_layout.addWidget(self.btn_bookmark)
        self.lock_icon = QLabel()
        self.lock_icon.setObjectName("nativeLock")
        self.lock_icon.setFixedSize(18, 18)
        self.lock_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lock_icon.setStyleSheet("color: #9aa0b5; font-size: 14px; background: transparent; border: none;")
        pill_layout.addWidget(self.lock_icon)
        layout.addWidget(self.address_pill, 1)
        self._update_nav_buttons(False, False)

    def eventFilter(self, watched, event):
        if watched is self.url_input:
            if event.type() == QEvent.Type.FocusIn:
                self._set_address_focus(True)
            elif event.type() == QEvent.Type.FocusOut:
                self._set_address_focus(False)
        return super().eventFilter(watched, event)

    def _set_address_focus(self, focused: bool):
        self.address_pill.setProperty("focused", bool(focused))
        self.address_pill.style().unpolish(self.address_pill)
        self.address_pill.style().polish(self.address_pill)
        self.address_pill.update()

    def _on_bookmark_clicked(self):
        self.bookmark_toggle.emit()

    def set_bookmarked(self, bookmarked: bool):
        """Update the bookmark visual based on the current page state."""
        self.btn_bookmark.setIcon(self._bookmark_icon(bool(bookmarked)))
        self.btn_bookmark.setProperty("bookmarked", bool(bookmarked))
        self.btn_bookmark.setToolTip("Remove bookmark" if bookmarked else "Bookmark this page")
        self.btn_bookmark.style().unpolish(self.btn_bookmark)
        self.btn_bookmark.style().polish(self.btn_bookmark)

    def _make_nav_button(self, text: str, icon_paths: str = "") -> QPushButton:
        btn = QPushButton(text)
        if icon_paths:
            btn.setIcon(self._line_icon(icon_paths, size=18))
            btn.setIconSize(QSize(18, 18))
            btn.setText("")
        btn.setFixedSize(30, 30)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #9aa0b5;
                font-size: 15px;
                border-radius: 15px;
            }
            QPushButton:hover {
                background: rgba(123, 92, 255, 0.15);
                color: #e8eaf2;
            }
            QPushButton:disabled {
                color: #3d4360;
            }
        """)
        return btn

    def _on_url_submit(self):
        text = self.url_input.text().strip()
        if text:
            self.url_submitted.emit(text)
            self.url_input.clearFocus()

    def set_url(self, url: str):
        is_https = url.startswith("https://")
        lock_paths = (
            '<rect x="5" y="11" width="14" height="9" rx="2"/>'
            '<path d="M8 11V7a4 4 0 0 1 8 0v4"/>'
            if is_https else
            '<rect x="5" y="11" width="14" height="9" rx="2"/>'
            '<path d="M8 11V7a4 4 0 0 1 7-2.6"/>'
        )
        self.lock_icon.setPixmap(self._line_icon(lock_paths, size=18).pixmap(QSize(18, 18)))
        self.lock_icon.setToolTip("Secure connection" if is_https else "Connection is not secure")
        if self.url_input.hasFocus():
            return
        if url.startswith("bfsb://") or url.startswith("data:"):
            self.url_input.setText("")
        elif url.startswith("http://127.0.0.1:8889/") or url.startswith("http://localhost:8889/"):
            # Local server - show as search.bfsb.com for search, empty for home
            if "/search" in url and "q=" in url:
                from urllib.parse import urlparse, parse_qs
                try:
                    parsed = urlparse(url)
                    query = parse_qs(parsed.query).get("q", [""])[0]
                    if query:
                        self.url_input.setText(f"https://search.bfsb.com/search?q={query}")
                    else:
                        self.url_input.setText("")
                except Exception:
                    self.url_input.setText("")
            else:
                # Home page - keep empty
                self.url_input.setText("")
        else:
            self.url_input.setText(url)
            self.url_input.setCursorPosition(0)

    def update_nav_buttons(self, can_back: bool, can_forward: bool):
        self._update_nav_buttons(can_back, can_forward)

    def _update_nav_buttons(self, can_back: bool, can_forward: bool):
        self.btn_back.setEnabled(can_back)
        self.btn_forward.setEnabled(can_forward)


# ══════════════════════════════════════════════════════════════════
# NATIVE PROGRESS BAR
# ══════════════════════════════════════════════════════════════════

class NativeProgressBar(QWidget):
    """Thin progress bar that sits under the nav bar."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("background: transparent;")
        self.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._bar = QWidget()
        self._bar.setFixedHeight(2)
        self._bar.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #7b5cff, stop:1 #4f7cff);
            border-radius: 0 2px 2px 0;
        """)
        # Real glow via Qt's drop-shadow effect (CSS box-shadow is ignored on widgets).
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect
        from PyQt6.QtGui import QColor
        glow = QGraphicsDropShadowEffect(self._bar)
        glow.setBlurRadius(14)
        glow.setOffset(0, 0)
        glow.setColor(QColor(123, 92, 255, 220))  # matches the gradient's violet
        self._bar.setGraphicsEffect(glow)
        self._bar.hide()
        layout.addWidget(self._bar)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(80)
        self._pulse_timer.timeout.connect(self._pulse)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide)
        self._pulse_phase = 0.0
        self._indeterminate = False

    def start_progress(self):
        self._hide_timer.stop()
        self._indeterminate = True
        self._pulse_phase = 0.0
        self.show()
        self._bar.show()
        self._bar.setFixedWidth(0)
        self._pulse_timer.start()

    def _pulse(self):
        if not self._indeterminate:
            return
        import math
        self._pulse_phase += 0.18
        width = max(24, int(self.width() * (0.18 + 0.12 * (1 + math.sin(self._pulse_phase)))))
        self._bar.setFixedWidth(min(width, max(24, int(self.width() * 0.42))))

    def set_progress(self, percent: int):
        self._hide_timer.stop()
        self._indeterminate = False
        self._pulse_timer.stop()
        value = max(0, min(100, int(percent)))
        self._bar.setFixedWidth(int(self.width() * value / 100) if self.width() else value)
        self.show()
        self._bar.show()

    def complete_progress(self):
        self._indeterminate = False
        self._pulse_timer.stop()
        self._bar.setFixedWidth(self.width())
        self._hide_timer.start(200)

    def _hide(self):
        self._bar.hide()
        self.hide()
        self._bar.setFixedWidth(0)


# ══════════════════════════════════════════════════════════════════
# BROWSER CHROME — Combined tab bar + nav bar + progress bar
# ══════════════════════════════════════════════════════════════════

class BrowserChrome(QWidget):
    """Complete browser chrome: tab bar on top, nav bar below.

    This is a native PyQt widget that sits above the QStackedWidget.
    It persists across page navigations because it's NOT part of the
    web page HTML — it's a real Qt widget.
    """

    tab_switched = pyqtSignal(int)
    tab_closed = pyqtSignal(int)
    new_tab_requested = pyqtSignal()
    back_clicked = pyqtSignal()
    forward_clicked = pyqtSignal()
    reload_clicked = pyqtSignal()
    url_submitted = pyqtSignal(str)
    bookmark_toggle = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("background: #0e1220;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tab_bar = NativeTabBar()
        self.tab_bar.tab_switched.connect(self.tab_switched.emit)
        self.tab_bar.tab_closed.connect(self.tab_closed.emit)
        self.tab_bar.new_tab_requested.connect(self.new_tab_requested.emit)
        layout.addWidget(self.tab_bar)

        self.nav_bar = NativeNavBar()
        self.nav_bar.back_clicked.connect(self.back_clicked.emit)
        self.nav_bar.forward_clicked.connect(self.forward_clicked.emit)
        self.nav_bar.reload_clicked.connect(self.reload_clicked.emit)
        self.nav_bar.url_submitted.connect(self.url_submitted.emit)
        self.nav_bar.bookmark_toggle.connect(self.bookmark_toggle.emit)
        layout.addWidget(self.nav_bar)

        # Progress bar under nav bar
        self.progress_bar = NativeProgressBar()
        layout.addWidget(self.progress_bar)

    def set_url(self, url: str):
        self.nav_bar.set_url(url)

    def set_bookmarked(self, bookmarked: bool):
        """Update the star button visual."""
        self.nav_bar.set_bookmarked(bookmarked)

    def update_nav_buttons(self, can_back: bool, can_forward: bool):
        self.nav_bar.update_nav_buttons(can_back, can_forward)

    def update_tab_title(self, index: int, title: str):
        self.tab_bar.update_title(index, title)

    # Progress bar controls
    def start_progress(self):
        self.progress_bar.start_progress()

    def set_progress(self, percent: int):
        self.progress_bar.set_progress(percent)

    def complete_progress(self):
        self.progress_bar.complete_progress()

    def set_active_tab(self, index: int):
        self.tab_bar.set_active(index)
