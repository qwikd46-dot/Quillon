from pathlib import Path

from PyQt6.QtCore import QByteArray, QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class ExternalSideOverlay(QWidget):
    home_requested = pyqtSignal()
    panel_requested = pyqtSignal(str)
    collapsed_changed = pyqtSignal(bool)
    closed = pyqtSignal()

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(232)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._active = False
        self._collapsed = False
        self._adblock_enabled = True
        self._buttons = []
        self._section_labels = []
        self._button_labels = {}

        self._panel = QFrame(self)
        self._panel.setObjectName("externalSidePanel")
        self._panel.setFixedWidth(232)
        self._panel.setStyleSheet("""
            QFrame#externalSidePanel {
                background: #0a0e1a;
                border: none;
                border-right: 1px solid #161c33;
            }
            QLabel {
                color: #e8eaf2;
                font-size: 14px;
            }
            QLabel#overlayMark {
                color: #ffffff;
                background: transparent;
                border-radius: 10px;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#overlayBrand {
                color: #e8eaf2;
                font-size: 16px;
                font-weight: 600;
            }
            QLabel#overlaySubtitle, QLabel#overlaySection {
                color: #6b7194;
                font-size: 10px;
            }
            QLabel#overlaySubtitle { font-weight: 400; }
            QLabel#overlaySection {
                text-transform: uppercase;
                letter-spacing: 1.2px;
                padding: 14px 12px 8px 12px;
            }
            QPushButton {
                color: #9aa0b5;
                background: transparent;
                border: none;
                border-radius: 10px;
                padding: 10px 12px;
                text-align: left;
                font-size: 14px;
            }
            QPushButton[collapsed="true"] { padding: 10px 0; text-align: center; }
            QPushButton:focus { outline: none; border: none; background: transparent; }
            QPushButton:hover, QPushButton:checked {
                color: #e8eaf2;
                background: #181e3d;
            }
            QPushButton:checked { color: #7b5cff; }
        """)

        panel_layout = QVBoxLayout(self._panel)
        self._panel_layout = panel_layout
        panel_layout.setContentsMargins(12, 18, 12, 18)
        panel_layout.setSpacing(0)

        brand_row = QHBoxLayout()
        self._brand_row = brand_row
        brand_row.setContentsMargins(10, 4, 10, 20)
        brand_row.setSpacing(12)
        self._mark = QLabel()
        self._mark.setObjectName("overlayMark")
        self._mark.setFixedSize(34, 34)
        self._mark.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._mark.setCursor(Qt.CursorShape.PointingHandCursor)
        self._brand_left_spacer = brand_row.addStretch(0)
        icon_path = Path(__file__).resolve().parents[2] / "bfsb_icon.png"
        self._logo_normal_pixmap = QPixmap(str(icon_path)).scaled(
            34,
            34,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._logo_hovered = False
        self._mark.installEventFilter(self)
        self._mark.setPixmap(self._logo_normal_pixmap)
        self._mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_row.addWidget(self._mark)
        self._brand_text = QWidget()
        brand_text = QVBoxLayout(self._brand_text)
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(0)
        self._brand = QLabel("BFSB")
        self._brand.setObjectName("overlayBrand")
        self._subtitle = QLabel("Browser For Safe Browsing")
        self._subtitle.setObjectName("overlaySubtitle")
        self._subtitle.setWordWrap(True)
        brand_text.addWidget(self._brand)
        brand_text.addWidget(self._subtitle)
        brand_row.addWidget(self._brand_text, 1)
        self._brand_right_spacer = brand_row.addStretch(0)
        self._brand_left_index = 0
        self._brand_text_index = 2
        self._brand_right_index = 3
        panel_layout.addLayout(brand_row)

        self._add_section(panel_layout, "Menu")
        self._home_button = self._add_button(
            panel_layout,
            "Home",
            self.home_requested.emit,
            '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>',
        )
        self._home_button.setCheckable(True)
        self._home_button.setChecked(True)
        self._add_button(
            panel_layout,
            "Bookmarks",
            lambda: self.panel_requested.emit("bookmarks"),
            '<path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"/>',
        )
        self._add_button(
            panel_layout,
            "History",
            lambda: self.panel_requested.emit("history"),
            '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
        )
        self._add_button(
            panel_layout,
            "Downloads",
            lambda: self.panel_requested.emit("downloads"),
            '<path d="M12 3v12m0 0 4-4m-4 4-4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
        )

        self._add_section(panel_layout, "Privacy")
        self._add_button(
            panel_layout,
            "Safe Browsing",
            lambda: self.panel_requested.emit("privacy"),
            '<path d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6z"/><path d="m9 12 2 2 4-4"/>',
        )
        self._add_button(
            panel_layout,
            "Passwords",
            lambda: self.panel_requested.emit("passwords"),
            '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
        )

        panel_layout.addStretch(1)
        panel_layout.addSpacing(10)
        footer_line = QFrame()
        footer_line.setObjectName("overlayFooterLine")
        footer_line.setFixedHeight(1)
        footer_line.setStyleSheet("background: #161c33; border: none;")
        panel_layout.addWidget(footer_line)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addStretch(1)
        settings_button = self._add_button(
            footer,
            "Settings",
            lambda: self.panel_requested.emit("settings"),
            '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z"/>',
        )
        settings_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        footer.addStretch(1)
        panel_layout.addLayout(footer)

        self.setStyleSheet("background: transparent;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()

    def _update_logo_pixmap(self) -> None:
        path = '<path d="M9 6l6 6-6 6"/>' if self._collapsed else '<path d="M15 18l-6-6 6-6"/>'
        self._logo_collapse_pixmap = self._line_icon(path, 18).pixmap(QSize(18, 18))
        self._mark.setPixmap(
            self._logo_collapse_pixmap if self._logo_hovered else self._logo_normal_pixmap
        )

    def _set_logo_hovered(self, hovered: bool) -> None:
        self._logo_hovered = bool(hovered)
        self._update_logo_pixmap()

    def eventFilter(self, watched, event):
        if watched is self._mark:
            if event.type() == QEvent.Type.Enter:
                self._set_logo_hovered(True)
            elif event.type() == QEvent.Type.Leave:
                self._set_logo_hovered(False)
            elif event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.toggle_collapsed()
                    return True
        return super().eventFilter(watched, event)

    def _add_section(self, layout, text):
        label = QLabel(text)
        label.setObjectName("overlaySection")
        layout.addWidget(label)
        self._section_labels.append(label)
        return label

    def _add_button(self, layout, text, callback, icon_paths: str = ""):
        button = QPushButton(text)
        if icon_paths:
            button.setIcon(self._line_icon(icon_paths))
            button.setIconSize(QSize(18, 18))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedHeight(38)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.clicked.connect(callback)
        button.setProperty("fullText", text)
        button.setProperty("collapsed", False)
        layout.addWidget(button)
        self._buttons.append(button)
        self._button_labels[button] = text
        return button

    def set_adblock_state(self, enabled: bool):
        self._adblock_enabled = bool(enabled)

    def set_browser_active(self, active: bool):
        self._active = bool(active)
        if active:
            self._home_button.setChecked(False)
            self.open_overlay()
        else:
            self.close_overlay()

    def open_overlay(self):
        if not self._active:
            return
        self._panel.setGeometry(0, 0, self._panel.width(), self.height())
        self.show()
        self.raise_()

    def close_overlay(self):
        if not self.isVisible():
            return
        self.hide()
        self.closed.emit()

    def toggle_overlay(self):
        if self.isVisible():
            self.close_overlay()
        else:
            self.open_overlay()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool, emit: bool = True) -> None:
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        width = 64 if self._collapsed else 232
        self.setFixedWidth(width)
        self._panel.setFixedWidth(width)
        if self._collapsed:
            self._panel_layout.setContentsMargins(0, 18, 0, 18)
            self._brand_row.setContentsMargins(0, 4, 0, 20)
            self._brand_row.setStretch(self._brand_left_index, 1)
            self._brand_row.setStretch(self._brand_text_index, 0)
            self._brand_row.setStretch(self._brand_right_index, 1)
            self._brand_row.setAlignment(self._mark, Qt.AlignmentFlag.AlignCenter)
        else:
            self._panel_layout.setContentsMargins(12, 18, 12, 18)
            self._brand_row.setContentsMargins(10, 4, 10, 20)
            self._brand_row.setStretch(self._brand_left_index, 0)
            self._brand_row.setStretch(self._brand_text_index, 1)
            self._brand_row.setStretch(self._brand_right_index, 0)
            self._brand_row.setAlignment(self._mark, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for label in self._section_labels:
            label.setVisible(not self._collapsed)
        self._brand_text.setVisible(not self._collapsed)
        self._brand.setVisible(not self._collapsed)
        self._subtitle.setVisible(not self._collapsed)
        for button in self._buttons:
            full_text = self._button_labels.get(button, button.text())
            if self._collapsed:
                button.setText("")
                button.setToolTip(full_text)
            else:
                button.setText(full_text)
                button.setToolTip("")
            button.setProperty("collapsed", self._collapsed)
            button.style().unpolish(button)
            button.style().polish(button)
        self._update_logo_pixmap()
        if emit:
            self.collapsed_changed.emit(self._collapsed)

    def toggle_collapsed(self):
        self.set_collapsed(not self._collapsed)

    def resizeEvent(self, event):
        self._panel.setGeometry(0, 0, self._panel.width(), self.height())
        super().resizeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.toggle_collapsed()
            event.accept()
            return
        super().keyPressEvent(event)
