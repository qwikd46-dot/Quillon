"""Side panel — Downloads, History, and Bookmarks tabs.

Toggle with Ctrl+B. The panel hosts three tabs:

* :class:`DownloadsTab` — powered by :class:`DownloadManager`
  at ``quillon/ui/downloads.py``.
* :class:`HistoryPanel` — browsing history with search and day grouping.
* :class:`BookmarksPanel` — bookmarks with folder sidebar and card grid.

All three panels share the same design system (``--panel``, ``--panel-2``,
``--line``, ``--text``, ``--dim``, ``--violet``, ``--violet-soft``,
``--blue``, ``--ok``, ``--danger``) as module-local constants so the
shared token system in ``styles.py`` (``C``, ``R``, ``S``, ``T``,
``DIMS``) is not modified.
"""

from __future__ import annotations

import datetime
from typing import Optional

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QColor
from PyQt6.QtWidgets import (
    QDockWidget,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QScrollArea,
    QSizePolicy,
)

from .styles import C, T, S
from ..core.storage import BookmarkStore, HistoryStore, Bookmark, HistoryEntry


# ─────────────────────────────────────────────────────────────
# Shared panel palette
# ─────────────────────────────────────────────────────────────

_PANEL      = "#191d3d"
_PANEL_2    = "#1f2450"
_LINE       = "#2a2f5c"
_TEXT       = "#e6e6f5"
_DIM        = "#8f93bd"
_VIOLET     = "#7c6cf7"
_VIOLET_SOFT = "rgba(124,108,247,.16)"
_BLUE       = "#4d8bf8"
_OK         = "#6fe0a8"
_DANGER     = "#ff9a9a"


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _human_ago(ts: int) -> str:
    """Format a unix timestamp as '2m ago' / 'Yesterday' / '2026-07-19'."""
    if not ts:
        return ""
    now = datetime.datetime.now().timestamp()
    delta = int(now - ts)
    if delta < 0:
        return ""
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    if delta < 7 * 86400:
        return f"{delta // 86400}d ago"
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _host(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(url).hostname or url
    except Exception:
        return url


def _day_label(ts: int) -> str:
    """'TODAY', 'YESTERDAY', 'MONDAY', or 'JULY 19, 2026'."""
    if not ts:
        return ""
    now = datetime.datetime.now()
    dt = datetime.datetime.fromtimestamp(ts)
    delta = (now - dt).days
    if delta == 0:
        return "TODAY"
    if delta == 1:
        return "YESTERDAY"
    if delta < 7:
        return dt.strftime("%A").upper()
    return dt.strftime("%B %d, %Y").upper()


def _time_label(ts: int) -> str:
    if not ts:
        return ""
    dt = datetime.datetime.fromtimestamp(ts)
    return dt.strftime("%I:%M %p").lstrip("0")


# ─────────────────────────────────────────────────────────────
# Shared inline styles
# ─────────────────────────────────────────────────────────────

_HEADER_ICON_STYLE = """
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {violet}, stop:1 {blue});
    color: #ffffff;
    border-radius: 7px;
    border: none;
    font-size: 16px;
    font-weight: bold;
""".format(violet=_VIOLET, blue=_BLUE)

_COUNT_PILL_STYLE = """
    background: {panel_2};
    color: {dim};
    border: none;
    border-radius: 999px;
    padding: 2px 10px;
    font-size: 11.5px;
""".format(panel_2=_PANEL_2, dim=_DIM)

_SEARCH_STYLE = """
    QLineEdit {{
        background: {panel_2};
        color: {text};
        border: 1px solid {line};
        border-radius: 999px;
        padding: 7px 12px 7px 32px;
        font-size: 13px;
    }}
    QLineEdit:focus {{
        border: 1px solid {violet};
    }}
""".format(panel_2=_PANEL_2, text=_TEXT, line=_LINE, violet=_VIOLET)

_DIVIDER_STYLE = "background: {line}; border: none;".format(line=_LINE)


# ─────────────────────────────────────────────────────────────
# HistoryPanel
# ─────────────────────────────────────────────────────────────

class HistoryPanel(QWidget):
    """Browsing history with search and day grouping."""

    open_url = pyqtSignal(str)
    history_changed = pyqtSignal()

    def __init__(self, store: HistoryStore, parent=None):
        super().__init__(parent)
        self._store = store
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setStyleSheet("background: {panel}; border: none;".format(panel=_PANEL))
        hdr = QHBoxLayout(header)
        hdr.setContentsMargins(14, 12, 14, 12)
        hdr.setSpacing(10)

        icon = QLabel("⏱")
        icon.setFixedSize(28, 28)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(_HEADER_ICON_STYLE)
        hdr.addWidget(icon)

        title = QLabel("History")
        title.setStyleSheet(
            "color: {text}; font-size: 15px; font-weight: 600; "
            "background: transparent; border: none;".format(text=_TEXT)
        )
        hdr.addWidget(title)

        self._range_label = QLabel("Recent")
        self._range_label.setStyleSheet(_COUNT_PILL_STYLE)
        self._range_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.addWidget(self._range_label, alignment=Qt.AlignmentFlag.AlignRight)

        root.addWidget(header)

        div = QWidget()
        div.setFixedHeight(1)
        div.setStyleSheet(_DIVIDER_STYLE)
        root.addWidget(div)

        search_wrap = QWidget()
        search_wrap.setStyleSheet("background: {panel}; border: none;".format(panel=_PANEL))
        search_layout = QHBoxLayout(search_wrap)
        search_layout.setContentsMargins(12, 8, 12, 8)
        search_layout.setSpacing(0)

        search_icon = QLabel("⌕")
        search_icon.setFixedSize(18, 18)
        search_icon.setStyleSheet(
            "color: {dim}; background: transparent; border: none; "
            "font-size: 15px;".format(dim=_DIM)
        )
        search_layout.addWidget(search_icon)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search history")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(_SEARCH_STYLE)
        self._search.textChanged.connect(self._on_search)
        search_layout.addWidget(self._search)

        root.addWidget(search_wrap)

        self._list = QListWidget()
        self._list.setStyleSheet(
            """
            QListWidget {{
                background: {panel};
                border: none;
                outline: 0;
            }}
            QListWidget::item {{
                background: transparent;
                border: none;
                padding: 0;
                margin: 0;
            }}
            QListWidget::item:selected {{
                background: {panel_2};
            }}
            QListWidget::item:hover {{
                background: {panel_2};
            }}
            """.format(panel=_PANEL, panel_2=_PANEL_2)
        )
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context)
        root.addWidget(self._list, stretch=1)

    def focus_search(self):
        self._search.setFocus()
        self._search.selectAll()

    def refresh(self):
        self._refresh()

    def _on_search(self, _text):
        self._refresh()

    def _refresh(self):
        self._list.clear()
        query = self._search.text().strip()
        if query:
            entries = self._store.search(query, limit=100)
        else:
            entries = self._store.list_recent(200)

        if not entries:
            empty = QListWidgetItem("No history yet. Browse somewhere!")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            empty.setForeground(Qt.GlobalColor.gray)
            empty.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._list.addItem(empty)
            self._range_label.setText("Recent")
            return

        if entries and not query:
            most_recent = entries[0].last_visit
            delta = datetime.datetime.now().timestamp() - most_recent
            if delta < 86400:
                self._range_label.setText("Last 24 hours")
            elif delta < 7 * 86400:
                self._range_label.setText("Last 7 days")
            else:
                self._range_label.setText("All time")
        else:
            self._range_label.setText("Search results")

        groups: dict[str, list[HistoryEntry]] = {}
        for e in entries:
            day = _day_label(e.last_visit)
            groups.setdefault(day, []).append(e)

        for day, day_entries in groups.items():
            day_item = QListWidgetItem(day)
            day_item.setFlags(Qt.ItemFlag.NoItemFlags)
            day_item.setForeground(QColor(_DIM))
            day_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            day_item.setSizeHint(QSize(max(100, self._list.viewport().width() - 24), 28))
            day_font = day_item.font()
            day_font.setPointSize(10)
            day_font.setWeight(600)
            day_item.setFont(day_font)
            self._list.addItem(day_item)

            for e in day_entries:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, e.url)
                item.setToolTip("{title}\n{url}\nLast visit: {ago}".format(
                    title=e.title or e.url,
                    url=e.url,
                    ago=_human_ago(e.last_visit),
                ))
                item.setSizeHint(QSize(max(100, self._list.viewport().width() - 24), 52))

                host = _host(e.url)
                letter = (host[0] if host else (e.title[:1] if e.title else "#")).upper()

                row = QWidget()
                lay = QHBoxLayout(row)
                lay.setContentsMargins(8, 6, 8, 6)
                lay.setSpacing(10)

                time_lbl = QLabel(_time_label(e.last_visit))
                time_lbl.setFixedWidth(52)
                time_lbl.setStyleSheet(
                    "color: {dim}; font-size: 11px; background: transparent; border: none;".format(dim=_DIM)
                )
                lay.addWidget(time_lbl)

                avatar = QLabel(letter)
                avatar.setFixedSize(24, 24)
                avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
                avatar.setStyleSheet(
                    "background: {panel_2}; color: {violet}; border-radius: 6px; "
                    "font-size: 12px; font-weight: 700; border: none;".format(
                        panel_2=_PANEL_2, violet=_VIOLET
                    )
                )
                lay.addWidget(avatar)

                col = QVBoxLayout()
                col.setContentsMargins(0, 0, 0, 0)
                col.setSpacing(1)

                title_lbl = QLabel(e.title or e.url)
                title_lbl.setStyleSheet(
                    "color: {text}; font-size: 13px; font-weight: 500; "
                    "background: transparent; border: none;".format(text=_TEXT)
                )
                title_lbl.setWordWrap(False)
                col.addWidget(title_lbl)

                url_lbl = QLabel(e.url)
                url_lbl.setStyleSheet(
                    "color: {dim}; font-size: 11px; background: transparent; border: none;".format(dim=_DIM)
                )
                url_lbl.setWordWrap(False)
                col.addWidget(url_lbl)
                lay.addLayout(col, stretch=1)

                del_btn = QPushButton("✕")
                del_btn.setObjectName("row-delete")
                del_btn.setFixedSize(22, 22)
                del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                del_btn.setStyleSheet(
                    """
                    QPushButton#row-delete {{
                        background: transparent;
                        color: transparent;
                        border: none;
                        font-size: 12px;
                    }}
                    QWidget:hover QPushButton#row-delete {{
                        background: rgba(255,120,120,.14);
                        color: {danger};
                        border: none;
                        border-radius: 5px;
                    }}
                    """.format(danger=_DANGER)
                )
                del_btn.clicked.connect(lambda _, url=e.url: self._on_delete(url))
                lay.addWidget(del_btn)

                row.setStyleSheet("QWidget { background: transparent; }")
                row.installEventFilter(del_btn)
                self._list.setItemWidget(item, row)

        self._list.setUniformItemSizes(False)

    def _on_delete(self, url: str):
        entries = self._store.list_recent(500)
        for h in entries:
            if h.url == url:
                self._store.remove(h.id)
                break
        self._refresh()
        self.history_changed.emit()

    def _on_context(self, pos):
        item = self._list.itemAt(pos)
        if not item:
            return
        url = item.data(Qt.ItemDataRole.UserRole)
        if not url:
            return
        menu = QMenu(self)
        a_open = menu.addAction("Open")
        menu.addSeparator()
        a_delete = menu.addAction("Delete from history")
        a_delete_all = menu.addAction("Clear all history...")
        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
        if chosen == a_open:
            self.open_url.emit(url)
        elif chosen == a_delete:
            entries = self._store.list_recent(500)
            for h in entries:
                if h.url == url:
                    self._store.remove(h.id)
                    break
            self._refresh()
            self.history_changed.emit()
        elif chosen == a_delete_all:
            from ..confirm_dialog import ConfirmDialog
            count = self._store.count()
            if count == 0:
                return
            if ConfirmDialog.ask_destructive(
                self,
                "Clear all history?",
                f"Delete all {count} entries from your browsing history?",
                detail="This cannot be undone. Bookmarks are kept.",
                confirm_label="Clear all",
                cancel_label="Keep",
            ):
                self._store.clear()
                self._refresh()
                self.history_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self._list.viewport().width()
        if w != getattr(self, "_last_list_width", None):
            self._last_list_width = w
            self._refresh()


# ─────────────────────────────────────────────────────────────
# BookmarksPanel
# ─────────────────────────────────────────────────────────────

class BookmarksPanel(QWidget):
    """Bookmarks with folder sidebar and compact browser-style card list."""

    open_url = pyqtSignal(str)
    bookmark_state_changed = pyqtSignal()

    def __init__(self, store: BookmarkStore, parent=None):
        super().__init__(parent)
        self._store = store
        self._selected_folder_id: Optional[int] = None  # None = All
        self._current_cols: Optional[int] = None
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QWidget()
        sidebar.setFixedWidth(118)
        sidebar.setStyleSheet(
            "background: {panel}; border: none; border-right: 1px solid {line};".format(
                panel=_PANEL, line=_LINE
            )
        )
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(8, 10, 8, 10)
        sb_layout.setSpacing(4)

        folders_label = QLabel("FOLDERS")
        folders_label.setStyleSheet(
            "color: {dim}; background: transparent; border: none; "
            "font-size: 10px; font-weight: 700; letter-spacing: 1px;".format(dim=_DIM)
        )
        sb_layout.addWidget(folders_label)

        self._folder_list = QListWidget()
        self._folder_list.setStyleSheet(
            """
            QListWidget {
                background: transparent;
                border: none;
                outline: 0;
                padding: 0;
            }
            QListWidget::item {
                background: transparent;
                border: none;
                padding: 0;
                margin: 0;
            }
            QListWidget::item:selected {
                background: transparent;
                border: none;
            }
            QListWidget::item:hover {
                background: transparent;
                border: none;
            }
            """
        )
        self._folder_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._folder_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._folder_list.setSpacing(4)
        self._folder_list.itemClicked.connect(self._on_folder_clicked)
        sb_layout.addWidget(self._folder_list)

        new_folder_btn = QPushButton("＋ New Folder")
        new_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_folder_btn.setStyleSheet(
            """
            QPushButton {{
                background: transparent;
                color: {dim};
                border: none;
                border-radius: 6px;
                padding: 8px 10px;
                font-size: 12px;
                text-align: left;
            }}
            QPushButton:hover {{
                background: {panel_2};
                color: {text};
            }}
            """.format(dim=_DIM, panel_2=_PANEL_2, text=_TEXT)
        )
        new_folder_btn.clicked.connect(self._on_new_folder)
        sb_layout.addWidget(new_folder_btn)

        root.addWidget(sidebar)

        main = QWidget()
        main.setStyleSheet("background: {panel}; border: none;".format(panel=_PANEL))
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(12, 12, 10, 10)
        main_layout.setSpacing(8)

        self._card_list = QListWidget()
        self._card_list.setStyleSheet(
            """
            QListWidget {{
                background: transparent;
                border: none;
                outline: 0;
            }}
            QListWidget::item {{
                background: transparent;
                border: none;
                padding: 0;
                margin: 0;
            }}
            QListWidget::item:selected {{
                background: {panel_2};
            }}
            QListWidget::item:hover {{
                background: {panel_2};
            }}
            """.format(panel_2=_PANEL_2)
        )
        self._card_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._card_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._card_list.setSpacing(6)
        self._card_list.setUniformItemSizes(False)
        main_layout.addWidget(self._card_list)

        root.addWidget(main, stretch=1)

    def focus_search(self):
        pass

    def refresh(self):
        self._refresh()

    def _on_new_folder(self) -> None:
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name.strip():
            return
        self._store.create_folder(name.strip())
        self._refresh()
        self.bookmark_state_changed.emit()

    def _select_folder(self, folder_id: Optional[int]):
        self._selected_folder_id = folder_id
        for i in range(self._folder_list.count()):
            item = self._folder_list.item(i)
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) == folder_id)
        self._refresh_grid()

    def _on_folder_clicked(self, item: QListWidgetItem):
        fid = item.data(Qt.ItemDataRole.UserRole)
        if fid == self._selected_folder_id:
            fid = None
        self._select_folder(fid)

    def _folder_btn_style(self, selected: bool) -> str:
        if selected:
            return """
                QWidget {{
                    background: {violet_soft};
                    border: 1px solid rgba(124,108,247,.32);
                    border-radius: 7px;
                }}
                QLabel {{
                    color: {text};
                    background: transparent;
                    border: none;
                    font-size: 10.5px;
                    font-weight: 600;
                }}
            """.format(violet_soft=_VIOLET_SOFT, text=_TEXT)
        return """
            QWidget {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 7px;
            }}
            QWidget:hover {{
                background: {panel_2};
            }}
            QLabel {{
                color: {dim};
                background: transparent;
                border: none;
                font-size: 10.5px;
                font-weight: 400;
            }}
        """.format(panel_2=_PANEL_2, dim=_DIM)

    def _refresh(self):
        self._folder_list.clear()

        _SKIP_FOLDERS = {"Bookmarks Bar", "Other Bookmarks"}

        all_count = len(self._store.list_all(limit=10000))

        folders = [
            (fid, fname)
            for fid, fname in self._store.list_folders()
            if fname not in _SKIP_FOLDERS
        ]

        for fid, fname in folders:
            bms = [b for b in self._store.list_all(limit=10000) if b.folder_id == fid]
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, fid)
            item.setSizeHint(QSize(102, 34))
            widget = QWidget()
            layout = QHBoxLayout(widget)
            layout.setContentsMargins(7, 5, 6, 5)
            layout.setSpacing(6)
            icon = QLabel("▸")
            icon.setFixedWidth(14)
            icon.setStyleSheet(
                "color: {blue}; font-size: 12px; background: transparent; border: none;".format(blue=_BLUE)
            )
            layout.addWidget(icon)
            name = QLabel(fname)
            name.setStyleSheet(
                "color: {text}; background: transparent; border: none; font-size: 10.5px; font-weight: 400;".format(text=_TEXT)
            )
            name.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            layout.addWidget(name, stretch=1)
            count_lbl = QLabel(str(len(bms)))
            count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            count_lbl.setStyleSheet(
                "color: {dim}; background: transparent; border: none; font-size: 10px;".format(dim=_DIM)
            )
            layout.addWidget(count_lbl)
            widget.setStyleSheet(self._folder_btn_style(fid == self._selected_folder_id))
            self._folder_list.addItem(item)
            self._folder_list.setItemWidget(item, widget)

        for i in range(self._folder_list.count()):
            item = self._folder_list.item(i)
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) == self._selected_folder_id)

        self._refresh_grid()

    def _refresh_grid(self):
        while self._card_list.count():
            item = self._card_list.takeItem(0)
            widget = self._card_list.itemWidget(item)
            if widget:
                widget.deleteLater()
            del item

        if self._selected_folder_id is None:
            bookmarks = self._store.list_all(limit=300)
        else:
            bookmarks = [b for b in self._store.list_all(limit=10000) if b.folder_id == self._selected_folder_id]

        if not bookmarks:
            empty = QListWidgetItem("No bookmarks here yet.")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            empty.setForeground(Qt.GlobalColor.gray)
            empty.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._card_list.addItem(empty)
            return

        for bm in bookmarks:
            item = QListWidgetItem()
            card = self._build_card(bm)
            item.setSizeHint(QSize(self._card_list.viewport().width() - 8, 72))
            self._card_list.addItem(item)
            self._card_list.setItemWidget(item, card)

    def _build_card(self, bm: Bookmark) -> QWidget:
        card = QWidget()
        card.setStyleSheet(
            """
            QWidget {{
                background: {panel};
                border: 1px solid {line};
                border-radius: 8px;
            }}
            QWidget:hover {{
                background: {panel_2};
            }}
            """.format(panel=_PANEL, line=_LINE, panel_2=_PANEL_2)
        )
        lay = QHBoxLayout(card)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(12)

        host = _host(bm.url)
        letter = (host[0] if host else (bm.title[:1] if bm.title else "#")).upper()
        avatar = QLabel(letter)
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            "background: {panel_2}; color: {violet}; border-radius: 6px; "
            "font-size: 13px; font-weight: 700; border: none;".format(
                panel_2=_PANEL_2, violet=_VIOLET
            )
        )
        lay.addWidget(avatar)

        info = QWidget()
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        title = QLabel(bm.title or bm.url)
        title.setStyleSheet(
            "color: {text}; font-size: 13px; font-weight: 500; background: transparent; border: none;".format(text=_TEXT)
        )
        title.setWordWrap(False)
        info_layout.addWidget(title)

        url_lbl = QLabel(bm.url)
        url_lbl.setStyleSheet(
            "color: {dim}; font-size: 11px; background: transparent; border: none;".format(dim=_DIM)
        )
        url_lbl.setWordWrap(False)
        info_layout.addWidget(url_lbl)

        meta = QWidget()
        meta_layout = QHBoxLayout(meta)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(8)

        folder_name = bm.folder or ""
        if not folder_name:
            try:
                folders = self._store.list_folders()
                folder_name = next((f for fid, f in folders if fid == bm.folder_id), "")
            except Exception:
                folder_name = ""
        if folder_name:
            tag = QLabel(folder_name)
            tag.setStyleSheet(
                "color: {violet}; background: {violet_soft}; border-radius: 4px; "
                "padding: 2px 6px; font-size: 10px; font-weight: 500; border: none;".format(
                    violet=_VIOLET, violet_soft=_VIOLET_SOFT
                )
            )
            meta_layout.addWidget(tag)

        time_lbl = QLabel(_human_ago(getattr(bm, "updated_at", 0)))
        time_lbl.setStyleSheet(
            "color: {dim}; background: transparent; border: none; font-size: 10px;".format(dim=_DIM)
        )
        meta_layout.addWidget(time_lbl)
        info_layout.addWidget(meta)
        lay.addWidget(info, stretch=1)

        more_btn = QPushButton("⋮")
        more_btn.setFixedSize(24, 24)
        more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        more_btn.setStyleSheet(
            """
            QPushButton {{
                background: transparent;
                color: {dim};
                border: none;
                font-size: 16px;
            }}
            QPushButton:hover {{
                color: {text};
            }}
            """.format(dim=_DIM, text=_TEXT)
        )
        more_btn.clicked.connect(
            lambda _, url=bm.url: self._on_card_context(card, card.mapToGlobal(card.rect().center()), url)
        )
        lay.addWidget(more_btn)

        card.setProperty("bookmark_url", bm.url)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        # Use event filter instead of overriding mousePressEvent (lambda crash fix)
        def _card_mouse_press(event, url=bm.url):
            if event.button() == Qt.MouseButton.LeftButton:
                self.open_url.emit(url)
                event.accept()
        card.installEventFilter(self)
        card._click_url = bm.url
        card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        card.customContextMenuRequested.connect(
            lambda pos, c=card, url=bm.url: self._on_card_context(c, c.mapToGlobal(pos), url)
        )
        return card

    def _on_card_context(self, card, pos, url: str):
        bm = self._store.get(url)
        if not bm:
            return
        menu = QMenu(self)
        a_open = menu.addAction("Open")
        a_open_new = menu.addAction("Open in new tab")
        menu.addSeparator()
        pin_label = "Unpin from bookmarks bar" if bm.pinned else "Pin to bookmarks bar"
        a_pin = menu.addAction(pin_label)
        a_rename = menu.addAction("Rename...")
        menu.addSeparator()
        a_delete = menu.addAction("Delete bookmark")
        chosen = menu.exec(pos)
        if chosen == a_open:
            self.open_url.emit(url)
        elif chosen == a_open_new:
            self.open_url.emit(url)
        elif chosen == a_pin:
            self._store.set_pinned(url, not bm.pinned)
            self._refresh()
            self.bookmark_state_changed.emit()
        elif chosen == a_rename:
            new_title, ok = QInputDialog.getText(
                self, "Rename bookmark", "Title:", text=bm.title
            )
            if ok and new_title:
                self._store.set_title(url, new_title)
                self._refresh()
                self.bookmark_state_changed.emit()
        elif chosen == a_delete:
            self._store.remove(url)
            self._refresh()
            self.bookmark_state_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._recalc_columns()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._recalc_columns)

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.MouseButtonPress:
            url = getattr(obj, '_click_url', None)
            if url and event.button() == Qt.MouseButton.LeftButton:
                self.open_url.emit(url)
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def _recalc_columns(self):
        w = self._card_list.viewport().width()
        if w <= 0:
            return
        new_cols = 1 if w < 610 else 2
        if new_cols != self._current_cols:
            self._current_cols = new_cols
            self._refresh_grid()


# ─────────────────────────────────────────────────────────────
# SidePanel — dock hosting Downloads, History, and Bookmarks tabs.
# ─────────────────────────────────────────────────────────────

class SidePanel(QDockWidget):
    """Right-side panel — Downloads, History, and Bookmarks tabs.

    The dock title is "Downloads" to preserve the existing dock
    identifier. The tabs are shown inside the dock widget body.
    """

    open_url = pyqtSignal(str)        # open a URL in the active tab

    def __init__(
        self,
        downloads: DownloadManager,
        parent=None,
        bookmark_store: Optional[BookmarkStore] = None,
        history_store: Optional[HistoryStore] = None,
    ) -> None:
        super().__init__("Downloads", parent)
        self.setObjectName("downloads_panel")

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setStyleSheet("background: {panel}; border: none;".format(panel=_PANEL))
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)

        top_bar = QWidget()
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(16, 12, 16, 12)

        title_group = QWidget()
        title_layout = QHBoxLayout(title_group)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(8)
        title_icon = QLabel("▤")
        title_icon.setFixedSize(24, 24)
        title_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_icon.setStyleSheet(_HEADER_ICON_STYLE)
        title_text = QLabel("Side Panel")
        title_text.setStyleSheet(
            "color: {text}; font-size: 14px; font-weight: 600; "
            "background: transparent; border: none;".format(text=_TEXT)
        )
        title_layout.addWidget(title_icon)
        title_layout.addWidget(title_text)

        close_btn = QPushButton("✕")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            "background: transparent; color: {dim}; border: none; font-size: 15px; padding: 2px 4px;".format(dim=_DIM)
        )
        close_btn.clicked.connect(self.close)

        top_bar_layout.addWidget(title_group)
        top_bar_layout.addStretch()
        top_bar_layout.addWidget(close_btn)

        header_layout.addWidget(top_bar)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet(
            """
            QTabWidget::pane {{
                background: {panel};
                border: none;
            }}
            QTabBar::tab {{
                background: transparent;
                color: {dim};
                padding: 10px 16px;
                border: none;
                border-bottom: 2px solid transparent;
                font-size: 12.5px;
                font-weight: 500;
            }}
            QTabBar::tab:selected {{
                color: {text};
                border-bottom: 2px solid {violet};
            }}
            QTabBar::tab:hover {{
                color: {text};
                background: {panel_2};
            }}
            """.format(panel=_PANEL, dim=_DIM, text=_TEXT, violet=_VIOLET, panel_2=_PANEL_2)
        )

        from .downloads import DownloadsTab
        self._downloads_tab = DownloadsTab(downloads)
        self._tabs.addTab(self._downloads_tab, "Downloads")

        self._history_panel = None
        if history_store is not None:
            self._history_panel = HistoryPanel(history_store)
            self._history_panel.open_url.connect(self.open_url)
            self._tabs.addTab(self._history_panel, "History")

        self._bookmarks_panel = None
        if bookmark_store is not None:
            self._bookmarks_panel = BookmarksPanel(bookmark_store)
            self._bookmarks_panel.open_url.connect(self.open_url)
            self._tabs.addTab(self._bookmarks_panel, "Bookmarks")

        header_layout.addWidget(self._tabs)
        layout.addWidget(header)

        search_bar = QWidget()
        search_bar.setStyleSheet(
            "background: {panel}; border-bottom: 1px solid {line};".format(panel=_PANEL, line=_LINE)
        )
        search_layout = QHBoxLayout(search_bar)
        search_layout.setContentsMargins(12, 8, 12, 8)
        search_layout.setSpacing(8)

        search_icon = QLabel("⌕")
        search_icon.setStyleSheet(
            "color: {dim}; background: transparent; border: none; font-size: 15px;".format(dim=_DIM)
        )
        search_layout.addWidget(search_icon)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search bookmarks...")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setStyleSheet(
            """
            QLineEdit {{
                background: {panel_2};
                color: {text};
                border: 1px solid {line};
                border-radius: 999px;
                padding: 7px 12px 7px 8px;
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border: 1px solid {violet};
            }}
            """.format(panel_2=_PANEL_2, text=_TEXT, line=_LINE, violet=_VIOLET)
        )
        search_layout.addWidget(self._search_input)

        add_btn = QPushButton("＋ Add Bookmark")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setStyleSheet(
            """
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {violet}, stop:1 {blue});
                border: none;
                color: #ffffff;
                padding: 6px 12px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {violet_hover}, stop:1 {blue_hover});
            }}
            QPushButton:pressed {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {violet}, stop:1 {violet});
            }}
            """.format(
                violet=_VIOLET, blue=_BLUE,
                violet_hover="#9185f8", blue_hover="#6a9df9",
            )
        )
        add_btn.clicked.connect(self._on_add_bookmark)
        search_layout.addWidget(add_btn)

        layout.addWidget(search_bar)
        self.setWidget(body)
        self.setMinimumWidth(420)
        self.setMaximumWidth(520)

    def _on_add_bookmark(self) -> None:
        url, ok = QInputDialog.getText(
            self, "Add Bookmark", "URL:", text="https://"
        )
        if not ok or not url.strip():
            return
        url = url.strip()
        title = url
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or url
            title = host or url
        except Exception:
            pass
        if self._bookmarks_panel is not None:
            self._bookmarks_panel._store.add(url=url, title=title)
            self._bookmarks_panel.refresh()
            self._bookmarks_panel.bookmark_state_changed.emit()

    # ── backwards-compat shims ──────────────────────────────────

    def refresh_bookmarks(self) -> None:
        if self._bookmarks_panel is not None:
            self._bookmarks_panel.refresh()

    def refresh_history(self) -> None:  # noqa: D401
        if self._history_panel is not None:
            self._history_panel.refresh()

    def select_tab(self, which: str) -> None:
        target = {
            "downloads": self._downloads_tab,
            "history": self._history_panel,
            "bookmarks": self._bookmarks_panel,
        }.get(which)
        if target is not None:
            self._tabs.setCurrentWidget(target)

    def on_bookmarks_changed(self) -> None:
        self.refresh_bookmarks()

    def on_history_changed(self) -> None:
        self.refresh_history()

    def focus_search_active(self) -> None:
        current = self._tabs.currentWidget()
        if hasattr(current, "focus_search"):
            current.focus_search()
