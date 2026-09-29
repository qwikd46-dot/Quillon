"""Custom confirmation dialog — themed to match Quillon.

A modal centred on the parent window with:
- dark navy background, navy accent buttons
- title, body message, icon (optional)
- Yes / No / Cancel buttons (most callers only use Yes + No)

This replaces the system QMessageBox so the prompt doesn't look like a
random Windows/Linux/GNOME pop with grey buttons.
"""

from __future__ import annotations

from typing import Optional, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QGraphicsDropShadowEffect,
)

from .styles import C


_OUTCOME_ROLE = Qt.ItemDataRole.UserRole


class ConfirmDialog(QDialog):
    """Return value:
        int — the role (Role.Yes / Role.No / Role.Cancel) of the clicked button.
    """

    ROLE_YES = 1001
    ROLE_NO = 1002
    ROLE_CANCEL = 1003

    def __init__(
        self,
        parent,
        title: str,
        message: str,
        detail: str = "",
        *,
        danger: bool = False,
        confirm_label: str = "Confirm",
        cancel_label: str = "Cancel",
        show_danger: bool = True,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        # Drop the default chrome — we draw our own padding.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # The outer frame holds the drop-shadow + rounded background.
        outer = QFrame(self)
        outer.setObjectName("confirmCard")
        outer.setStyleSheet(f"""
            #confirmCard {{
                background: rgba(20, 20, 36, 0.96);
                border: 1px solid {C.BORDER_2};
                border-radius: 16px;
            }}
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 12)
        shadow.setColor(Qt.GlobalColor.black)
        outer.setGraphicsEffect(shadow)

        root = QVBoxLayout(outer)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(6)

        # Title
        title_label = QLabel(title)
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setWeight(QFont.Weight.DemiBold)
        title_label.setFont(title_font)
        title_label.setStyleSheet(f"color: {C.TEXT_0}; background: transparent;")
        title_label.setWordWrap(True)
        root.addWidget(title_label)

        # Message
        if message:
            msg_label = QLabel(message)
            msg_label.setStyleSheet(
                f"color: {C.TEXT_1}; font-size: 13px; background: transparent;"
            )
            msg_label.setWordWrap(True)
            msg_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            root.addWidget(msg_label)
        root.addSpacing(4)

        # Optional detail (small grey block)
        if detail:
            detail_label = QLabel(detail)
            detail_label.setStyleSheet(
                f"color: {C.TEXT_2}; font-size: 12px; "
                f"background: rgba(0,0,0,0.25); padding: 8px 10px; "
                f"border-radius: 8px;"
            )
            detail_label.setWordWrap(True)
            detail_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            root.addWidget(detail_label)

        root.addSpacing(14)

        # Buttons row — Cancel on the left (secondary), Confirm on right (primary)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)

        self._roles: list[tuple[QPushButton, int]] = []
        if show_danger:
            cancel_btn = self._make_button(
                cancel_label,
                primary=False,
                danger=danger,
            )
            cancel_btn.clicked.connect(
                lambda: self._finish(self.ROLE_CANCEL)
            )
            self._roles.append((cancel_btn, self.ROLE_CANCEL))
            btn_row.addWidget(cancel_btn)

        confirm_btn = self._make_button(
            confirm_label, primary=True, danger=danger
        )
        confirm_btn.clicked.connect(
            lambda: self._finish(self.ROLE_YES)
        )
        self._roles.append((confirm_btn, self.ROLE_YES))
        confirm_btn.setDefault(True)
        confirm_btn.setAutoDefault(True)

        if not show_danger:
            self._roles.reverse()  # button order shouldn't matter

        btn_row.addWidget(confirm_btn)
        root.addLayout(btn_row)

        # Wrap outer in a layout that puts it centred on the dialog
        wrap = QVBoxLayout(self)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addWidget(outer)

        # Make sure sizing is sane
        self.setMinimumWidth(440)
        self.setMaximumWidth(560)
        outer.adjustSize()
        self.adjustSize()

        # Escape cancels, Enter confirms
        self.escape_shrtcut = None  # placeholder; Qt handles ESC by default
        self._result: Optional[int] = None

    def _make_button(self, label: str, *, primary: bool, danger: bool) -> QPushButton:
        btn = QPushButton(label)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if primary and danger:
            # Red filled — destructive
            base = "#ff4757"
            hover = "#ff5d6b"
            text = "#ffffff"
        elif primary:
            # Navy filled
            base = "#7c6af5"
            hover = "#8c7cff"
            text = "#ffffff"
        else:
            # Grey ghost
            base = "rgba(255,255,255,0.04)"
            hover = "rgba(255,255,255,0.08)"
            text = C.TEXT_1
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {base};
                color: {text};
                border: none;
                border-radius: 10px;
                padding: 9px 22px;
                font-size: 13px;
                font-weight: 600;
                min-width: 90px;
                outline: none;
            }}
            QPushButton:hover {{
                background: {hover};
            }}
            QPushButton:focus {{ outline: none; }}
            QPushButton:pressed {{
                padding-top: 10px;
                padding-bottom: 8px;
            }}
        """)
        return btn

    def _finish(self, role: int) -> None:
        self._result = role
        self.accept()

    @classmethod
    def confirm(
        cls,
        parent,
        title: str,
        message: str,
        detail: str = "",
        danger: bool = False,
        confirm_label: str = "Confirm",
        cancel_label: str = "Cancel",
    ) -> bool:
        """Returns True if user confirmed, False otherwise."""
        dlg = cls(
            parent,
            title,
            message,
            detail=detail,
            danger=danger,
            confirm_label=confirm_label,
            cancel_label=cancel_label,
            show_danger=True,
        )
        # Centre on parent
        if parent is not None:
            dlg.adjustSize()
            pg = parent.geometry()
            sz = dlg.size()
            dlg.move(
                pg.x() + (pg.width() - sz.width()) // 2,
                pg.y() + (pg.height() - sz.height()) // 2,
            )
        # Modal
        dlg.exec()
        return dlg._result == cls.ROLE_YES

    @classmethod
    def ask_destructive(
        cls,
        parent,
        title: str,
        message: str,
        detail: str = "",
        confirm_label: str = "Delete",
        cancel_label: str = "Cancel",
    ) -> bool:
        """Destructive operation prompt — confirms with red button."""
        return cls.confirm(
            parent,
            title,
            message,
            detail=detail,
            danger=True,
            confirm_label=confirm_label,
            cancel_label=cancel_label,
        )
