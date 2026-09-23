"""Native PyQt browser chrome — tab bar + URL bar + nav buttons.

This widget sits ABOVE the QStackedWidget containing web views.
Unlike HTML-based chrome, it persists across page navigations because
it's a real Qt widget, not part of the web page DOM.
"""

from __future__ import annotations

from typing import Callable, Optional, List
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLineEdit,
    QSizePolicy,
    QScrollArea,
    QLabel,
)
from PyQt6.QtGui import QIcon, QPixmap

from .styles import DIMS, C


# ══════════════════════════════════════════════════════════════════
# NATIVE TAB WIDGET
# ══════════════════════════════════════════════════════════════════

class NativeTab(QWidget):
    """A single tab in the native tab bar — styled like the new GUI."""

    clicked = pyqtSignal(int)   # emits index
    closed = pyqtSignal(int)    # emits index

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
        self._index = index
        self._active = False
        self.setFixedHeight(36)
        self.setMinimumWidth(120)
        self.setMaximumWidth(220)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 30, 0)
        layout.setSpacing(4)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(f"""
            color: {self._TEXT_DIM};
            font-size: 13px;
            background: transparent;
            border: none;
        """)
        self.title_label.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.title_label, 1)

        from PyQt6.QtWidgets import QToolButton

        self.close_btn = QToolButton(self)
        self.close_btn.setText("✕")
        self.close_btn.setFixedSize(18, 16)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                border: none;
                color: {self._TEXT_DIM};
                font-size: 11px;
                border-radius: 8px;
            }}
            QToolButton:hover {{
                background: rgba(123, 92, 255, 0.25);
                color: {self._TEXT};
            }}
        """)
        self.close_btn.clicked.connect(lambda: self.closed.emit(self._index))
        self._close_visible = False
        self.close_btn.setVisible(False)

        self._update_style()

    def _update_style(self):
        if self._active:
            self.setStyleSheet(f"""
                QWidget {{
                    background: {self._BG_TAB_ACTIVE};
                    border: 1px solid {self._ACCENT};
                    border-radius: 10px;
                }}
            """)
            self.title_label.setStyleSheet(f"""
                color: {self._TEXT};
                font-size: 13px;
                font-weight: 500;
                background: transparent;
                border: none;
            """)
            self._set_close_visible(True)
        else:
            self.setStyleSheet(f"""
                QWidget {{
                    background: {self._BG_TAB};
                    border: 1px solid {self._BORDER};
                    border-radius: 10px;
                }}
                QWidget:hover {{
                    background: {self._HOVER};
                }}
            """)
            self.title_label.setStyleSheet(f"""
                color: {self._TEXT_DIM};
                font-size: 13px;
                background: transparent;
                border: none;
            """)
            self._set_close_visible(False)

    def _set_close_visible(self, visible: bool):
        self._close_visible = visible
        self.close_btn.setVisible(visible)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep the close button pinned at the right edge (it's not in the
        # layout — the title keeps a 30px right margin free for it).
        self.close_btn.move(self.width() - 28, 10)

    def set_active(self, active: bool):
        if self._active != active:
            self._active = active
            self._update_style()

    def set_title(self, title: str):
        self.title_label.setText(title)
        self.title_label.setToolTip(title)

    def set_index(self, index: int):
        self._index = index

    # Click handling: use mousePressEvent on the tab widget AND title label
    # This is safe on plain QWidget (segfault was only on QWebEngineView)
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._index)
        super().mousePressEvent(event)

    def enterEvent(self, event):
        if not self._active:
            self._set_close_visible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self._active:
            self._set_close_visible(False)
        super().leaveEvent(event)


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
        self.setFixedHeight(46)
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
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:horizontal { height: 0px; }
        """)

        self._tabs_container = QWidget()
        self._tabs_container.setStyleSheet("background: transparent;")
        self._tabs_layout = QHBoxLayout(self._tabs_container)
        self._tabs_layout.setContentsMargins(0, 0, 0, 0)
        self._tabs_layout.setSpacing(8)
        self._tabs_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
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
        layout.addWidget(self.new_tab_btn)

        self._tabs: List[NativeTab] = []
        self._active_index = -1

    def add_tab(self, index: int, title: str = "New Tab") -> NativeTab:
        tab = NativeTab(index, title)
        tab.clicked.connect(self._on_tab_clicked)
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
        self._set_active(index)
        self.tab_switched.emit(index)

    def _on_tab_closed(self, index: int):
        self.tab_closed.emit(index)

    def set_active(self, index: int):
        self._set_active(index)

    def update_title(self, index: int, title: str):
        if 0 <= index < len(self._tabs):
            self._tabs[index].set_title(title)

    def count(self) -> int:
        return len(self._tabs)

    def clear(self):
        for tab in self._tabs:
            self._tabs_layout.removeWidget(tab)
            tab.deleteLater()
        self._tabs.clear()
        self._active_index = -1


# ══════════════════════════════════════════════════════════════════
# NATIVE NAVIGATION BAR (URL bar + buttons)
# ══════════════════════════════════════════════════════════════════

class NativeNavBar(QWidget):
    """Navigation bar with back/forward/reload + URL input + menu."""

    back_clicked = pyqtSignal()
    forward_clicked = pyqtSignal()
    reload_clicked = pyqtSignal()
    url_submitted = pyqtSignal(str)
    menu_clicked = pyqtSignal()
    bookmark_toggle = pyqtSignal()  # emitted when user clicks star

    def __init__(self):
        super().__init__()
        self.setFixedHeight(52)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("""
            QWidget { background: #0e1220; border: none; }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(6)

        # Back button
        self.btn_back = self._make_nav_button("←")
        self.btn_back.setToolTip("Back")
        self.btn_back.clicked.connect(self.back_clicked.emit)
        layout.addWidget(self.btn_back)

        # Forward button
        self.btn_forward = self._make_nav_button("→")
        self.btn_forward.setToolTip("Forward")
        self.btn_forward.clicked.connect(self.forward_clicked.emit)
        layout.addWidget(self.btn_forward)

        # Reload button
        self.btn_reload = self._make_nav_button("⟳")
        self.btn_reload.setToolTip("Reload")
        self.btn_reload.clicked.connect(self.reload_clicked.emit)
        layout.addWidget(self.btn_reload)

        # URL input (pill-shaped, matches the GUI address bar)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Search privately or enter URL...")
        self.url_input.setStyleSheet("""
            QLineEdit {
                background: #141933;
                border: 1px solid #232a4a;
                border-radius: 20px;
                padding: 0 18px;
                color: #e8eaf2;
                font-size: 14px;
                min-height: 38px;
                max-height: 38px;
            }
            QLineEdit:focus {
                border: 1px solid #7b5cff;
                background: #141933;
            }
        """)
        self.url_input.returnPressed.connect(self._on_url_submit)
        layout.addWidget(self.url_input, 1)

        # Bookmarks star button. Hollow star = not bookmarked, filled = bookmarked.
        # Fill-only state — no border/circle, no focus rect, no yellow anywhere.
        self.btn_bookmark = QPushButton("☆")
        self.btn_bookmark.setFixedSize(30, 30)
        self.btn_bookmark.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_bookmark.setToolTip("Bookmark this page")
        self.btn_bookmark.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: #9aa0b5;
                font-size: 17px;
                border-radius: 15px;
                padding: 0;
                margin: 0;
                outline: none;
            }}
            QPushButton:hover {{
                background: rgba(123, 92, 255, 0.15);
                color: #7b5cff;
            }}
            QPushButton:focus,
            QPushButton:focus:hover,
            QPushButton:focus:pressed {{ outline: none; border: none; background: transparent; }}
            QPushButton[bookmarked="true"] {{
                background: rgba(123, 92, 255, 0.12);
                color: #7b5cff;
            }}
            QPushButton:hover[bookmarked="true"] {{
                background: rgba(123, 92, 255, 0.2);
                color: #7b5cff;
            }}
        """)
        self.btn_bookmark.setProperty("bookmarked", False)
        self.btn_bookmark.clicked.connect(self._on_bookmark_clicked)
        layout.addWidget(self.btn_bookmark)

        # Menu button — fill-only violet hover, no border anywhere
        self.btn_menu = QPushButton("⋮")
        self.btn_menu.setFixedSize(30, 30)
        self.btn_menu.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_menu.setToolTip("Menu")
        self.btn_menu.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #9aa0b5;
                font-size: 16px;
                border-radius: 15px;
                padding: 0;
                margin: 0;
                outline: none;
            }
            QPushButton:hover {
                background: rgba(123, 92, 255, 0.15);
                color: #e8eaf2;
            }
            QPushButton:focus,
            QPushButton:focus:hover,
            QPushButton:focus:pressed { outline: none; border: none; background: transparent; }
        """)
        self.btn_menu.clicked.connect(self.menu_clicked.emit)
        layout.addWidget(self.btn_menu)

        self._update_nav_buttons(False, False)

    def _on_bookmark_clicked(self):
        self.bookmark_toggle.emit()

    def set_bookmarked(self, bookmarked: bool):
        """Update the star visual based on bookmark state."""
        self.btn_bookmark.setText("★" if bookmarked else "☆")
        self.btn_bookmark.setProperty("bookmarked", bookmarked)
        # re-apply style so the property change takes effect
        self.btn_bookmark.style().unpolish(self.btn_bookmark)
        self.btn_bookmark.style().polish(self.btn_bookmark)

    def _make_nav_button(self, text: str) -> QPushButton:
        btn = QPushButton(text)
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
        # Don't update while user is typing
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

        self._anim_timer = None
        self._target_width = 0

    def start_progress(self):
        """Show and animate progress from 0% (called by main_window on load start)."""
        self.show()
        self._bar.show()
        self._bar.setFixedWidth(0)
        self._target_width = 0
        self._animate_progress()

    def _animate_progress(self):
        """Animate progress bar like a real browser."""
        if self._target_width >= self.width() * 0.9:
            return

        import random
        increment = random.randint(2, 6)
        self._target_width = min(self._target_width + increment, int(self.width() * 0.9))
        self._bar.setFixedWidth(self._target_width)

        from PyQt6.QtCore import QTimer
        if not self._anim_timer:
            self._anim_timer = QTimer()
            self._anim_timer.timeout.connect(self._animate_progress)
        self._anim_timer.start(100)

    def set_progress(self, percent: int):
        """Set explicit progress (0-100)."""
        if percent >= 100:
            self.complete_progress()
        else:
            self._target_width = int(self.width() * percent / 100)
            self._bar.setFixedWidth(self._target_width)
            self.show()
            self._bar.show()

    def complete_progress(self):
        """Finish animation and hide (called by main_window on load finish)."""
        if self._anim_timer:
            self._anim_timer.stop()
            self._anim_timer = None
        self._bar.setFixedWidth(self.width())

        from PyQt6.QtCore import QTimer
        QTimer.singleShot(200, self._hide)

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
    menu_clicked = pyqtSignal()
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
        self.nav_bar.menu_clicked.connect(self.menu_clicked.emit)
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

    def menu_button(self):
        """Return the nav bar's menu button (used as the menu anchor)."""
        return self.nav_bar.btn_menu
