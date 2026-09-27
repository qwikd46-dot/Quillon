from __future__ import annotations

from PyQt6.QtCore import QByteArray, QSize, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class CategoryPopup(QWidget):
    closed = pyqtSignal()

    _TITLES = {
        "bookmarks": "Bookmarks",
        "history": "History",
        "downloads": "Downloads",
        "privacy": "Safe Browsing",
        "passwords": "Passwords",
        "settings": "Settings",
    }
    _RAIL = {
        "bookmarks": '<path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"/>',
        "history": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
        "downloads": '<path d="M12 3v12m0 0 4-4m-4 4-4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
        "privacy": '<path d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6z"/><path d="m9 12 2 2 4-4"/>',
        "passwords": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
        "settings": '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z"/>',
    }
    _POPUP_JS = r"""
(function() {
    window.__BFSB_POPUP__ = true;
    const style = document.createElement('style');
    style.textContent = `
        .sidebar, #chrome { display: none !important; }
        .main-col { margin-left: 0 !important; }
        body { background: #0e1220 !important; }
    `;
    document.head.appendChild(style);
    if (window.showCategoryFromHash) window.showCategoryFromHash();
})();
    """

    @staticmethod
    def _line_icon(paths: str, size: int = 18) -> QIcon:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            'fill="none" stroke="#9aa0b5" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            f'{paths}</svg>'
        )
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)

    def __init__(self, parent: QWidget, main_window) -> None:
        super().__init__(parent)
        self._main_window = main_window
        self._view = None
        self._panel_name = ""
        self.setObjectName("categoryPopupRoot")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget#categoryPopupRoot { background: rgba(0, 0, 0, 145); }
            QFrame#categoryPopupPanel {
                background: #131830;
                border: 1px solid #2b3355;
                border-radius: 16px;
            }
            QLabel#categoryPopupTitle {
                color: #e8eaf2;
                background: transparent;
                font-size: 16px;
                font-weight: 600;
            }
            QPushButton#categoryPopupClose {
                color: #9aa0b5;
                background: transparent;
                border: none;
                font-size: 20px;
                padding: 2px 7px;
            }
            QPushButton#categoryPopupClose:hover {
                color: #ffffff;
                background: #262c55;
                border-radius: 8px;
            }
            QFrame#categoryPopupRail {
                background: #0a0e1a;
                border-right: 1px solid #232a4a;
            }
            QToolButton#categoryPopupRailButton {
                color: #9aa0b5;
                background: transparent;
                border: none;
                border-radius: 8px;
                font-size: 16px;
                min-width: 30px;
                min-height: 30px;
            }
            QToolButton#categoryPopupRailButton:hover {
                color: #e8eaf2;
                background: #181e3d;
            }
            QToolButton#categoryPopupRailButton:checked {
                color: #7b5cff;
                background: #262c55;
            }
        """)
        self._panel = QFrame(self)
        self._panel.setObjectName("categoryPopupPanel")
        self._panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        header = QWidget(self._panel)
        header.setFixedHeight(50)
        header.setStyleSheet("background: transparent;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 0, 10, 0)
        self._title = QLabel("Category")
        self._title.setObjectName("categoryPopupTitle")
        header_layout.addWidget(self._title, 1)
        self._close_button = QPushButton("×")
        self._close_button.setObjectName("categoryPopupClose")
        self._close_button.setToolTip("Close")
        self._close_button.setAccessibleName("Close category popup")
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.clicked.connect(self.close_popup)
        header_layout.addWidget(self._close_button)
        panel_layout.addWidget(header)

        self._body_row = QWidget(self._panel)
        self._body_row.setStyleSheet("background: #0e1220;")
        body_row_layout = QHBoxLayout(self._body_row)
        body_row_layout.setContentsMargins(0, 0, 0, 0)
        body_row_layout.setSpacing(0)

        self._rail = QFrame(self._body_row)
        self._rail.setObjectName("categoryPopupRail")
        self._rail.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._rail.setFixedWidth(48)
        rail_layout = QVBoxLayout(self._rail)
        rail_layout.setContentsMargins(7, 10, 7, 10)
        rail_layout.setSpacing(5)
        self._rail_buttons = {}
        for name, icon_paths in self._RAIL.items():
            button = QToolButton(self._rail)
            button.setObjectName("categoryPopupRailButton")
            button.setIcon(self._line_icon(icon_paths))
            button.setIconSize(QSize(18, 18))
            button.setToolTip(self._TITLES[name])
            button.setAccessibleName(self._TITLES[name])
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, category=name: self.open_panel(category))
            rail_layout.addWidget(button)
            self._rail_buttons[name] = button
        rail_layout.addStretch(1)
        body_row_layout.addWidget(self._rail)

        self._body = QWidget(self._body_row)
        self._body.setStyleSheet("background: #0e1220;")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(0)
        body_row_layout.addWidget(self._body, 1)
        panel_layout.addWidget(self._body_row, 1)
        self.hide()

    def _set_active_category(self, panel: str) -> None:
        if panel not in self._TITLES:
            return
        self._panel_name = panel
        self._title.setText(self._TITLES[panel])
        for name, button in self._rail_buttons.items():
            button.setChecked(name == panel)

    def open_panel(self, panel: str) -> bool:
        if panel not in self._TITLES:
            return False
        if self._view is None:
            self._create_view()
        self._set_active_category(panel)
        home = getattr(getattr(self._main_window, "_server", None), "home_url", None)
        home = home or "http://127.0.0.1:8889/"
        target = home.rstrip("/") + "/#" + panel
        if self._view is not None and self._view.url().toString() != target:
            self._view.load(QUrl(target))
        self.show()
        self.raise_()
        QTimer.singleShot(0, self._sync_page_category)
        QTimer.singleShot(0, self._reposition)
        return True

    def _sync_page_category(self) -> None:
        if self._view is not None:
            self._view.page().runJavaScript(
                "window.showCategoryFromHash && window.showCategoryFromHash();"
            )

    def _create_view(self) -> None:
        from ..core.webengine import BFSBPage, create_web_view

        self._view = create_web_view(
            self._main_window._profile,
            self._main_window._blocker,
            page_class=BFSBPage,
            window=self._main_window,
        )
        self._view.page()._main_window = self._main_window
        self._view.setStyleSheet("background: #0e1220;")
        self._view.loadFinished.connect(self._on_view_load_finished)
        self._view.urlChanged.connect(self._on_view_url_changed)
        self._view.titleChanged.connect(self._on_view_title_changed)
        self._body_layout.addWidget(self._view)

    def _on_view_url_changed(self, url) -> None:
        fragment = url.toString().split("#", 1)[1] if "#" in url.toString() else ""
        self._set_active_category(fragment)

    def _on_view_title_changed(self, title: str) -> None:
        prefix = "BFSB Category — "
        if not title.startswith(prefix):
            return
        label = title[len(prefix):]
        for name, category_title in self._TITLES.items():
            if category_title == label:
                self._set_active_category(name)
                return

    def _on_view_load_finished(self, ok: bool) -> None:
        if ok and self._view is not None:
            self._view.page().runJavaScript(self._POPUP_JS)

    def close_popup(self) -> None:
        if not self.isVisible():
            return
        self.hide()
        self.closed.emit()

    def _reposition(self) -> None:
        width = min(840, max(320, self.width() - 40))
        height = min(640, max(260, self.height() - 40))
        self._panel.setGeometry(
            (self.width() - width) // 2,
            (self.height() - height) // 2,
            width,
            height,
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._reposition()

    def mousePressEvent(self, event) -> None:
        try:
            if event.button() == Qt.MouseButton.LeftButton and not self._panel.geometry().contains(event.position().toPoint()):
                self.close_popup()
                event.accept()
                return
            super().mousePressEvent(event)
        except Exception as error:
            print(f"[Popup] outside click failed: {error}")
            event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_popup()
            event.accept()
            return
        super().keyPressEvent(event)
