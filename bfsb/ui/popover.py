"""BFSB-styled popover windows and hover-stability helper.

A popover is a small frameless window that anchors to the edge of
another widget — used to surface Bookmarks/History lists next to the
main menu without a full sub-panel.

Classes:
- :class:`BFSBPopover` — base frameless popup window with BFSB styling.
- :class:`PopoverHost` — 12 Hz cursor-polling helper that keeps a
  popover open while the cursor moves between the menu row and the
  popover itself. Closes when the cursor has been outside both
  rectangles for >150ms.
- :class:`ListPopover` — base for popovers with a search field, a
  scrollable list, and a footer button.
- :class:`BookmarksPopover` — list of bookmarks with search.
- :class:`HistoryPopover` — list of recent history with search and a
  Clear-history footer.

Why a polling timer: Qt 6 has no public "cursor left this region"
event. The previous 200-line ``MenuEventFilter`` polled at 30 Hz for
*every* menu; this class is scoped to a single feature and runs at
12 Hz only while a popover is open. Stop on ``aboutToHide`` and on
``popover.closed``.

Design tokens come from :mod:`bfsb.ui.styles` — no hardcoded colors.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QObject, QPoint, QSize, QTime, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .styles import C, R, S, T


# ─────────────────────────────────────────────────────────────
# Shared panel palette (matches the redesigned Downloads/History/
# Bookmarks tabs in downloads.py and side_panel.py). Declared as
# module-local constants — same pattern used in those two files —
# so the shared token system in ``styles.py`` (``C``, ``R``, ``S``,
# ``T``) is left untouched and still used for spacing/type scale.
# ─────────────────────────────────────────────────────────────

_PANEL       = "#191d3d"
_PANEL_2     = "#1f2450"
_LINE        = "#2a2f5c"
_TEXT        = "#e6e6f5"
_DIM         = "#8f93bd"
_VIOLET      = "#7c6cf7"
_VIOLET_SOFT = "rgba(124,108,247,.16)"
_BLUE        = "#4d8bf8"


# ─────────────────────────────────────────────────────────────
# Entry data class — what a single row in a list popover looks like.
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Entry:
    """One row in a list popover."""
    glyph: str          # leading icon, e.g. "★" or "⏱"
    label: str          # text shown in the row (already truncated)
    url: str            # payload — what the row "does" when activated
    subtitle: str = ""  # second line — host · time · visit count (Brave-style)


def _avatar_color(url: str) -> str:
    """Deterministic pleasant hue for a URL's letter-avatar (like Brave)."""
    h = sum((url or "x").encode("utf-8")) % 360
    return f"hsl({h}, 55%, 45%)"


# ─────────────────────────────────────────────────────────────
# BFSBPopover — base frameless popup window with BFSB styling.
# ─────────────────────────────────────────────────────────────

class BFSBPopover(QWidget):
    """BFSB-styled frameless popup window.

    Anchors to the right edge of a parent widget by default. The popover
    is a top-level ``Qt.Popup`` window — it appears above other widgets,
    captures keyboard input, and is auto-closed by Qt when it loses
    focus unless we use a small host to keep it open.
    """

    closed = pyqtSignal()

    def __init__(
        self,
        parent_widget: QWidget,
        side: str = "right",
        width: int = 360,
        min_height: int = 360,
        qobject_parent: Optional[QWidget] = None,
    ) -> None:
        # The QObject parent is **the MainWindow** (passed in via
        # ``qobject_parent``), NOT the menu. The popover's logical
        # lifetime is tied to the main window (so it doesn't get
        # garbage-collected when the menu closes), but the popover
        # is a separate top-level *window* — we don't reparent it
        # to the menu.
        #
        # ``Qt.Tool`` is a "floating tool palette" — it floats above
        # the parent, doesn't appear in the taskbar, and (with
        # ``WA_ShowWithoutActivating``) does not steal focus from
        # the menu when shown. ``Qt.Popup`` would steal focus on
        # X11 and break menu hover events. ``Qt.ToolTip`` is
        # lighter but doesn't accept keyboard input — the search
        # field wouldn't work.
        #
        # Why a QObject parent is REQUIRED here: on X11 + software
        # rendering, creating a top-level ``Qt.Tool`` window with
        # QObject parent ``None`` causes the X server / WM combo to
        # crash the Qt process during show. Parenting to a stable
        # QWidget (the main window) gives the WM a parent to
        # associate the transient relationship with, which avoids
        # the crash.
        super().__init__(
            qobject_parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        # Don't grab focus from the menu when shown.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._parent_widget = parent_widget
        self._side = side
        self._apply_stylesheet()
        self.setFixedWidth(width)
        self.setMinimumHeight(min_height)

    # ── positioning ────────────────────────────────────────────

    def position_next_to(self) -> None:
        """Compute the screen position and move there.

        Called once after the layout has its size (in ``showEvent``)
        and again whenever the parent menu moves (rare).
        """
        if not self._parent_widget:
            return
        anchor_global = self._parent_widget.mapToGlobal(
            self._parent_widget.rect().topLeft()
        )
        anchor_global_right = self._parent_widget.mapToGlobal(
            self._parent_widget.rect().topRight()
        )

        screen = (
            QApplication.screenAt(anchor_global)
            or QApplication.primaryScreen()
        )
        if screen is None:
            return
        screen_geo = screen.availableGeometry()

        if self._side == "right":
            x = anchor_global_right.x() + 1
            y = anchor_global.y()
        else:
            x = anchor_global.x() - self.width() - 1
            y = anchor_global.y()

        # Clamp to screen so the popover is fully on-screen.
        x = max(screen_geo.left(), min(x, screen_geo.right() - self.width()))
        y = max(screen_geo.top(), min(y, screen_geo.bottom() - self.height()))
        self.move(x, y)

    def set_anchor(self, anchor_widget: QWidget) -> None:
        """Set (or change) the widget the popover anchors to.

        Called by the menu code each time the menu opens, so the
        popover positions next to the *current* menu instance, not
        a stale reference.

        We only update the internal reference — we do NOT reparent
        the window. Reparenting a top-level window to a QMenu is
        undefined (QMenu's children are its action items), and the
        X11 WM doesn't re-stack windows on a logical reparent.
        Instead, the popover stays a child of the BFSBWindow for
        lifetime, and we re-read the anchor's screen position on
        every show.
        """
        self._parent_widget = anchor_widget

    # ── show / close ───────────────────────────────────────────

    def show_and_focus(self, focus_widget: Optional[QWidget] = None) -> None:
        """Show the popover, position it, and (optionally) focus a child.

        ``popup()`` doesn't run a layout pass before showing; we have to
        adjustSize() first so width()/height() return real values, then
        position, then show.

        Note: we do NOT call ``focus_widget.activateWindow()`` here.
        The popover is a ``Qt.Tool`` window with
        ``WA_ShowWithoutActivating`` set, and on bare Xvfb (no
        window manager) activating a child of a non-activatable
        top-level can wedge or kill the Qt process. ``setFocus`` is
        enough to direct keyboard events to the search field; the
        popover stays a non-activating float.
        """
        import os as _os
        _dbg = _os.environ.get("BFSB_TEST") == "1"
        if _dbg:
            print(f"[BFSB_TEST] popover.show_and_focus: {type(self).__name__} adjustSize", flush=True)
        self.adjustSize()
        if _dbg:
            print(f"[BFSB_TEST] popover.show_and_focus: position_next_to", flush=True)
        self.position_next_to()
        if _dbg:
            print(f"[BFSB_TEST] popover.show_and_focus: show", flush=True)
        self.show()
        if _dbg:
            print(f"[BFSB_TEST] popover.show_and_focus: shown", flush=True)
        if focus_widget is not None:
            focus_widget.setFocus(Qt.FocusReason.OtherFocusReason)
            if _dbg:
                print(f"[BFSB_TEST] popover.show_and_focus: focus set", flush=True)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.closed.emit()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        # Esc closes the popover. Otherwise let children handle keys.
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    # ── styling ────────────────────────────────────────────────

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet(
            f"""
            BFSBPopover {{
                background: {_PANEL};
                color: {_TEXT};
                border: 1px solid {_LINE};
                border-radius: {R.LG}px;
            }}
            """
        )


# ─────────────────────────────────────────────────────────────
# PopoverHost — 12 Hz cursor-polling helper.
# ─────────────────────────────────────────────────────────────

class PopoverHost(QObject):
    """Keeps a popover open while the cursor moves between the menu
    row and the popover itself. Polls cursor at 12 Hz (every 80ms);
    closes when the cursor has been outside BOTH rectangles for
    >150ms (two missed polls).

    This is the only place in BFSB that polls the cursor. The previous
    30 Hz ``MenuEventFilter`` covered the whole menu permanently; this
    class is opt-in and only runs while a popover is open.
    """

    OUTSIDE_TIMEOUT_MS = 150

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(80)  # 12 Hz
        self._timer.timeout.connect(self._tick)
        self._out_since_ms: Optional[int] = None
        self._popover: Optional[BFSBPopover] = None
        self._anchor_widget: Optional[QWidget] = None

    def attach(
        self,
        popover: BFSBPopover,
        anchor_widget: QWidget,
    ) -> None:
        """Start polling for this popover.

        The popover stays open as long as the cursor is over the
        ``anchor_widget`` (e.g. the menu row) OR over the popover.
        Closes after :attr:`OUTSIDE_TIMEOUT_MS` of being outside both.

        The popover's ``closed`` signal is connected to ``_stop`` so
        we cleanly tear down the host when the popover closes
        naturally. We disconnect any previous popover's connection
        first — otherwise closing the *old* popover later would
        fire ``_stop`` against the *new* popover and clear its
        state.
        """
        import os as _os
        _dbg = _os.environ.get("BFSB_TEST") == "1"
        if _dbg:
            print(f"[BFSB_TEST] host.attach: enter, _popover={type(self._popover).__name__ if self._popover else None}, timer_active={self._timer.isActive()}", flush=True)
        if self._popover is popover and self._timer.isActive():
            if _dbg:
                print("[BFSB_TEST] host.attach: already hosting, return", flush=True)
            return  # already hosting this one
        # Disconnect the previous popover's closed signal, if any.
        if self._popover is not None and self._popover is not popover:
            if _dbg:
                print(f"[BFSB_TEST] host.attach: disconnect old {type(self._popover).__name__}", flush=True)
            try:
                self._popover.closed.disconnect(self._stop)
            except (TypeError, RuntimeError):
                # Slot wasn't connected (e.g. after detach()).
                pass
            # Switching to a different popover. Don't close() the
            # old one here — close+show races have wedged the X
            # server on bare Xvfb. The popovers are persistent
            # widgets; the old one stays as a hidden child of the
            # main window and gets reused on the next attach() call
            # that points to it. We only need to stop the polling
            # timer for now; the new attach() below starts it again.
            self._timer.stop()
            if _dbg:
                print(f"[BFSB_TEST] host.attach: switch (no close) old {type(self._popover).__name__}", flush=True)
        self._popover = popover
        self._anchor_widget = anchor_widget
        self._out_since_ms = None
        if not self._timer.isActive():
            if _dbg:
                print("[BFSB_TEST] host.attach: start timer", flush=True)
            self._timer.start()
        if _dbg:
            print(f"[BFSB_TEST] host.attach: connect closed for {type(popover).__name__}", flush=True)
        popover.closed.connect(self._stop)
        if _dbg:
            print(f"[BFSB_TEST] host.attach: exit", flush=True)

    def detach(self) -> None:
        """Stop polling and clear the active popover.

        Disconnects the popover's ``closed`` signal so a late close
        (after detach) doesn't fire ``_stop`` against a stale
        state. We use try/except because the signal may have been
        disconnected already (e.g. by a previous attach() cycle).
        """
        if self._popover is not None:
            try:
                self._popover.closed.disconnect(self._stop)
            except (TypeError, RuntimeError):
                pass
        self._timer.stop()
        self._popover = None
        self._anchor_widget = None
        self._out_since_ms = None

    def _stop(self) -> None:
        """Tear down on popover.closed.

        Bound to the active popover's ``closed`` signal in
        ``attach()``. If we get called for a popover that isn't
        the one we're currently hosting, it's a stale callback
        from a previous attach() cycle (the connection wasn't
        properly torn down). Ignore those — clearing state for
        the wrong popover would lose the active one.
        """
        # ``self.sender()`` is the QObject that emitted the
        # currently-handled signal. Falls back to None if called
        # outside a signal context (e.g. directly).
        sender = self.sender()
        if sender is not None and sender is not self._popover:
            return  # stale callback — not our active popover
        # Don't disconnect here: we ARE the closed-signal slot.
        # Calling disconnect on a signal that's mid-emit is unsafe
        # and Qt prints a warning.
        self._timer.stop()
        self._popover = None
        self._anchor_widget = None
        self._out_since_ms = None

    def _tick(self) -> None:
        if self._popover is None or not self._popover.isVisible():
            self._stop()
            return
        if self._anchor_widget is None:
            self._stop()
            return
        cursor = QCursor.pos()
        # Both checks map the cursor into the local coordinate system
        # of each widget. ``contains`` returns true if the local point
        # is inside the widget's rect.
        #
        # Defensive: ``self._anchor_widget`` is typed as QWidget but
        # in practice callers sometimes pass a QAction (which has no
        # rect()/mapFromGlobal()). If those methods are missing,
        # treat the anchor as "not under cursor" rather than
        # AttributeError-ing out of the tick slot — Qt would log a
        # warning and we'd lose polling. The popover check below
        # still works, so the popover stays open whenever the cursor
        # is over the popover itself.
        try:
            in_anchor = bool(
                self._anchor_widget.rect().contains(
                    self._anchor_widget.mapFromGlobal(cursor)
                )
            )
        except AttributeError:
            in_anchor = False
        in_popover = self._popover.rect().contains(
            self._popover.mapFromGlobal(cursor)
        )
        if in_anchor or in_popover:
            self._out_since_ms = None
            return
        now = QTime.currentTime().msecsSinceStartOfDay()
        if self._out_since_ms is None:
            self._out_since_ms = now
            return
        if now - self._out_since_ms > self.OUTSIDE_TIMEOUT_MS:
            self._popover.close()
            self._stop()


# ─────────────────────────────────────────────────────────────
# ListPopover — search field + scrollable list + footer button.
# ─────────────────────────────────────────────────────────────

class ListPopover(BFSBPopover):
    """Base for popovers with a search field, scrollable list, and footer.

    Subclasses implement :meth:`_entries` to provide data given a
    (possibly empty) search query. The popover emits :attr:`open_url`
    when a row is activated, and :attr:`show_sidebar` (or whatever
    the subclass wants) when the footer button is clicked.
    """

    open_url = pyqtSignal(str)
    show_sidebar = pyqtSignal()

    def __init__(
        self,
        parent_widget: QWidget,
        *,
        title: str,
        footer_label: str,
        width: int = 360,
        min_height: int = 360,
        qobject_parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            parent_widget,
            side="right",
            width=width,
            min_height=min_height,
            qobject_parent=qobject_parent,
        )
        self._title = title
        self._footer_label = footer_label
        self._build_ui()
        self._refresh()

    # ── UI ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(S.MD, S.MD, S.MD, S.MD)
        layout.setSpacing(S.SM)

        # Header label
        header = QLabel(self._title.upper())
        header.setStyleSheet(
            f"color: {_DIM}; font-size: {T.XS}px; "
            f"font-weight: {T.SEMIBOLD}; letter-spacing: 0.08em; "
            f"background: transparent; border: none;"
        )
        layout.addWidget(header)

        # Search field
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        # NOTE: setClearButtonEnabled intentionally not used — the
        # built-in × button inside a QLineEdit doesn't render well
        # with our custom stylesheet on X11 + software rendering and
        # has historically caused crashes. The user can clear the
        # field with Ctrl+A, Delete.
        self._search.setStyleSheet(
            f"""
            QLineEdit {{
                background: {_PANEL_2};
                color: {_TEXT};
                border: 1px solid {_LINE};
                border-radius: {R.MD}px;
                padding: 6px 10px;
                selection-background-color: {_VIOLET_SOFT};
            }}
            QLineEdit:focus {{
                border: 1px solid {_VIOLET};
            }}
            """
        )
        self._search.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._search)

        # List
        self._list = QListWidget()
        self._list.setStyleSheet(
            f"""
            QListWidget {{
                background: {_PANEL_2};
                border: 1px solid {_LINE};
                border-radius: {R.MD}px;
                padding: 4px;
                outline: 0;
            }}
            QListWidget::item {{
                padding: 7px 10px;
                border-radius: {R.SM}px;
                color: {_TEXT};
            }}
            QListWidget::item:hover {{
                background: {_PANEL};
            }}
            QListWidget::item:selected {{
                background: {_VIOLET_SOFT};
                color: {_TEXT};
            }}
            """
        )
        self._list.itemActivated.connect(self._on_activated)
        layout.addWidget(self._list, stretch=1)

        # Footer button
        self._footer = QPushButton(self._footer_label)
        self._footer.setCursor(Qt.CursorShape.PointingHandCursor)
        self._footer.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                color: {_DIM};
                border: 1px solid {_LINE};
                border-radius: {R.MD}px;
                padding: 8px 12px;
                text-align: left;
            }}
            QPushButton:hover {{
                background: {_VIOLET_SOFT};
                color: {_VIOLET};
                border: 1px solid {_VIOLET};
            }}
            """
        )
        self._footer.clicked.connect(self._on_footer_clicked)
        layout.addWidget(self._footer)

    # ── subclasses override these ───────────────────────────────

    @abstractmethod
    def _entries(self, query: str) -> list[Entry]:
        """Return the rows to show for ``query`` (already-truncated text)."""
        ...

    def _on_footer_clicked(self) -> None:
        """Default: emit show_sidebar. Subclasses can override."""
        self.show_sidebar.emit()
        self.close()

    # ── public ──────────────────────────────────────────────────

    def focus_search(self) -> None:
        self._search.setFocus()
        self._search.selectAll()

    def reload(self) -> None:
        """Re-fetch entries from the store and rebuild the list.

        Call this when the underlying data changes (a bookmark added,
        a history entry added, etc).
        """
        self._refresh()

    # ── internal ────────────────────────────────────────────────

    def _on_text_changed(self, _text: str) -> None:
        self._refresh()

    def _refresh(self) -> None:
        from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy
        from PyQt6.QtGui import QFont

        self._list.clear()
        self._list.setUniformItemSizes(False)
        query = self._search.text().strip()
        entries = self._entries(query)

        for e in entries:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, e.url)
            item.setToolTip(e.url)
            item.setSizeHint(QSize(self._list.viewport().width() - 24, 52))
            self._list.addItem(item)

            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(4, 4, 8, 4)
            lay.setSpacing(10)

            # Letter avatar — colored circle with the site's first letter
            # (matches Brave's history/bookmark rows).
            from urllib.parse import urlparse as _up
            host = ""
            try:
                host = _up(e.url).hostname or ""
            except Exception:
                host = ""
            letter = (host[0] if host else (e.label[:1] or "#")).upper()
            avatar = QLabel(letter)
            avatar.setFixedSize(30, 30)
            avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            avatar.setStyleSheet(
                f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
                f"stop:0 {_VIOLET}, stop:1 {_BLUE}); color: #ffffff;"
                f"border-radius: 15px; font-size: 14px; font-weight: 700;"
                f"border: none;"
            )
            lay.addWidget(avatar)

            # Title + subtitle column
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(1)
            title = QLabel(e.label)
            title.setStyleSheet(
                f"color: {_TEXT}; font-size: 13px; font-weight: 600;"
                f"background: transparent; border: none;"
            )
            col.addWidget(title)
            sub = e.subtitle or (host or e.url)
            if sub:
                sub_lbl = QLabel(sub)
                sub_lbl.setStyleSheet(
                    f"color: {_DIM}; font-size: 11px;"
                    f"background: transparent; border: none;"
                )
                col.addWidget(sub_lbl)
            lay.addLayout(col, stretch=1)
            row.setStyleSheet("QWidget { background: transparent; }")
            self._list.setItemWidget(item, row)

        if not entries:
            empty = QListWidgetItem("Nothing here yet.")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            empty.setForeground(Qt.GlobalColor.gray)
            self._list.addItem(empty)

        # Footer is visible only when there are more entries than shown.
        total = len(self._entries(""))
        self._footer.setVisible(total > 10)

    def _on_activated(self, item: QListWidgetItem) -> None:
        url = item.data(Qt.ItemDataRole.UserRole)
        if url:
            self.open_url.emit(url)
            self.close()


# ─────────────────────────────────────────────────────────────
# BookmarksPopover / HistoryPopover — concrete list popovers.
# ─────────────────────────────────────────────────────────────

def _truncate(s: str, n: int = 48) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _host_of(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(url).hostname or url
    except Exception:
        return url


def _human_ago(ts) -> str:
    """'just now' / '5m ago' / '3h ago' / '2d ago' / '2026-07-19'."""
    import datetime
    if not ts:
        return ""
    delta = int(datetime.datetime.now().timestamp() - ts)
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


def _history_subtitle(h) -> str:
    parts = [_host_of(h.url)]
    if getattr(h, "visit_count", 0) and h.visit_count > 1:
        parts.append(f"{h.visit_count} visits")
    ago = _human_ago(getattr(h, "last_visit", 0))
    if ago:
        parts.append(ago)
    return "  ·  ".join(parts)


class BookmarksPopover(ListPopover):
    """Popover showing a search-filtered list of bookmarks."""

    clear_requested = pyqtSignal()  # not used here, kept for parity

    def __init__(
        self,
        parent_widget: QWidget,
        bookmark_store,
        qobject_parent: Optional[QWidget] = None,
    ) -> None:
        self._store = bookmark_store
        super().__init__(
            parent_widget,
            title="Bookmarks",
            footer_label="Open all in sidebar  →",
            qobject_parent=qobject_parent,
        )

    def _entries(self, query: str) -> list[Entry]:
        if query:
            items = self._store.search(query, limit=50)
        else:
            all_bms = self._store.list_all(limit=300)
            pinned = [b for b in all_bms if b.pinned]
            others = [b for b in all_bms if not b.pinned]
            items = (pinned + others)[:10]
        return [
            Entry(
                glyph="★" if b.pinned else "☆",
                label=_truncate(b.title or b.url),
                url=b.url,
                subtitle=(_host_of(b.url) + ("  ·  📌 pinned" if b.pinned else "")),
            )
            for b in items
        ]


class HistoryPopover(ListPopover):
    """Popover showing a search-filtered list of recent history."""

    def __init__(
        self,
        parent_widget: QWidget,
        history_store,
        on_clear,
        qobject_parent: Optional[QWidget] = None,
    ) -> None:
        self._store = history_store
        self._on_clear = on_clear
        super().__init__(
            parent_widget,
            title="History",
            footer_label="Clear history…",
            qobject_parent=qobject_parent,
        )

    def _entries(self, query: str) -> list[Entry]:
        if query:
            items = self._store.search(query, limit=50)
        else:
            items = self._store.list_recent(limit=10)
        return [
            Entry(
                glyph="⏱",
                label=_truncate(h.title or h.url),
                url=h.url,
                subtitle=_history_subtitle(h),
            )
            for h in items
        ]

    def _on_footer_clicked(self) -> None:
        # Override default: don't open sidebar — call clear callback.
        self._on_clear()
        self.close()
