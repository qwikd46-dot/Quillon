"""Quillon dropdown menu.

Port of the HTML overflow-menu design (``quillon-menu.html``) into the
native ``QMenu``. The look mirrors the HTML mockup: dark surface,
violet-accented rows with an inset left-border on hover, 24x24 icon
tiles, dimmed section headers, line-styled separators, and the
HTML's exact palette so the new 3-dots menu feels like a native
counterpart of the web mockup.

Design tokens come directly from ``quillon-menu.html``'s CSS custom
properties (``--menu``, ``--violet``, ``--line``, etc.) and are kept
as module-local constants so the shared token system in ``styles.py``
(``C``, ``R``, ``S``, ``T``, ``DIMS``) is not modified.

The menu still subclasses ``QMenu`` so the existing hover/popover
plumbing in ``MainWindow`` (``_show_main_menu``,
``_on_menu_row_hovered``, ``aboutToHide``, ``menu.isVisible()``)
continues to work unchanged.
"""

from __future__ import annotations

from typing import Callable, Optional, Union

from PyQt6.QtCore import Qt, QRect, QTimer
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
)
from PyQt6.QtWidgets import QMenu

from .styles import C, R, S, T


# ── palette (quillon-menu.html CSS custom properties) ─────────────────
_MENU_BG      = "#191d3d"
_MENU_BG_2    = "#1f2450"
_LINE         = "#2a2f5c"
_TEXT         = "#e6e6f5"
_DIM          = "#8f93bd"
_VIOLET       = "#7c6cf7"
_VIOLET_SOFT  = "rgba(124,108,247,.16)"
_BLUE         = "#4d8bf8"
_DANGER       = "#ff9a9a"
_DANGER_SOFT  = "rgba(255,120,120,.14)"


# Sentinel for section-header actions. The stylesheet inspects each
# item's property via property selector `[quillon-role="section"]` to
# pick the right styling.
_SECTION_ROLE = "section"


def _icon_tile(glyph: str, danger: bool = False) -> QIcon:
    """Render a 24x24 rounded tile matching the HTML's ``.quillon-ic``.

    Normal items use the soft-violet tile. The erase glyph (``⌫``)
    used by "Clear browsing data" renders as the danger (red) tile
    to match the HTML's ``quillon-item--danger`` row.
    """
    size = 24
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    rect = QRect(0, 0, size, size)

    if danger:
        p.setBrush(QColor(255, 120, 120, 36))   # rgba(255,120,120,.14)
        glyph_color = QColor(_DANGER)
    else:
        p.setBrush(QColor(124, 108, 247, 41))   # rgba(124,108,247,.16)
        glyph_color = QColor(_VIOLET)

    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(rect, 7, 7)

    p.setPen(glyph_color)
    p.setBrush(Qt.BrushStyle.NoBrush)
    font = QFont()
    font.setPixelSize(14)
    font.setWeight(QFont.Weight.Bold)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    p.setFont(font)
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)
    p.end()

    return QIcon(px)


class QuillonMenu(QMenu):
    """Quillon-styled dropdown menu.

    Behaves like a normal ``QMenu``: outside-click detection,
    keyboard navigation, and focus handling are all provided by Qt.
    Rows are added with ``add_item()``, sections with
    ``add_section()``, dividers with ``addSeparator()``.

    The ``hovered`` and ``aboutToHide`` signals are inherited from
    ``QMenu`` — payload for ``hovered`` is the ``QAction`` of the
    row the cursor entered, same as native.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setContentsMargins(S.SM, S.SM, S.SM, S.SM)
        self.setMinimumWidth(290)
        self._apply_stylesheet()

    # ── convenience API (mirrors what MainWindow expects) ─────────

    def add_section(self, label: str) -> QAction:
        """Add a non-clickable section header (e.g. 'Navigation')."""
        action = QAction(label.upper(), self)
        action.setEnabled(False)
        action.setProperty("quillon-role", _SECTION_ROLE)
        self.addAction(action)
        return action

    def add_item(
        self,
        icon: Union[str, QIcon, None],
        label: str,
        shortcut: str = "",
        on_triggered: Optional[Callable[[], None]] = None,
    ) -> QAction:
        """Add a regular row.

        ``icon`` may be a Unicode glyph (e.g. ``"★"``) or a ``QIcon``.
        String glyphs are rendered as 24x24 violet icon tiles matching
        the HTML mockup; the ``⌫`` glyph renders as the danger (red)
        tile for the "Clear browsing data" row.
        """
        action = QAction(self)
        if isinstance(icon, QIcon):
            action.setIcon(icon)
        elif icon:
            is_danger = icon == "⌫"
            action.setIcon(_icon_tile(icon, danger=is_danger))
            if is_danger:
                action.setProperty("quillon-danger", True)
        action.setText(label)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutVisibleInContextMenu(True)
        if on_triggered is not None:
            action.triggered.connect(
                lambda _checked=False, fn=on_triggered: QTimer.singleShot(0, fn)
            )
        self.addAction(action)
        return action

    def add_separator(self) -> None:
        """Themed separator."""
        self.addSeparator()

    def popup_at(self, global_point) -> None:
        """Show the menu at the given global point."""
        self.popup(global_point)

    # ── styling ─────────────────────────────────────────────────────

    def _apply_stylesheet(self) -> None:
        """Apply the HTML overflow-menu palette."""
        self.setStyleSheet(
            f"""
            QMenu {{
                background: {_MENU_BG};
                color: {_TEXT};
                border: 1px solid {_LINE};
                border-radius: 16px;
                padding: 8px;
                font-family: Inter, system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
                font-size: 13.5px;
                font-weight: 400;
            }}
            QMenu::item {{
                background: transparent;
                color: {_TEXT};
                padding: 10px 12px;
                margin: 1px 0;
                border-radius: 10px;
                border-left: 2px solid transparent;
                font-size: 13.5px;
                font-weight: 400;
            }}
            QMenu::item:selected {{
                background: {_MENU_BG_2};
                color: {_TEXT};
                border-left: 2px solid {_VIOLET};
            }}
            QMenu::item:disabled {{
                color: {_DIM};
            }}
            QMenu::item[quillon-role="section"] {{
                color: {_DIM};
                font-size: 11.5px;
                font-weight: 500;
                letter-spacing: 0.08em;
                padding: 6px 14px 2px 14px;
                margin: 0;
                border-left: 2px solid transparent;
            }}
            QMenu::item[quillon-role="section"]:selected {{
                background: transparent;
                color: {_DIM};
                border-left: 2px solid transparent;
            }}
            QMenu::item[quillon-danger="true"] {{
                color: {_DANGER};
            }}
            QMenu::item[quillon-danger="true"]:selected {{
                background: {_MENU_BG_2};
                color: {_DANGER};
                border-left: 2px solid {_DANGER};
            }}
            QMenu::icon {{
                width: 35px;
                padding: 0 11px 0 0;
            }}
            QMenu::separator {{
                background: {_LINE};
                height: 1px;
                margin: 4px 14px;
                border: 0;
            }}
            QMenu::right-arrow {{
                image: none;
            }}
            """
        )
