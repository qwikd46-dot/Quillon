"""Quillon downloads — manager, row widget, and side-panel tab.

Three classes:

- :class:`Download` — dataclass for an in-flight or finished download.
- :class:`DownloadManager` — owns the list of downloads, the
  ``~/Downloads`` directory, and the integration with Qt's
  ``QWebEngineDownloadItem``. Emits signals when downloads are added,
  progress, finish, or are removed.
- :class:`DownloadRow` — a single row in the panel showing a file-type
  icon tile, filename, metadata, progress bar (in-progress only), and
  right-aligned action buttons.
- :class:`DownloadsTab` — the tab in the side panel. Holds a scrollable
  list of :class:`DownloadRow`, a header with icon tile + title + item
  count pill, and a footer with "Clear finished" and "Open ~/Downloads"
  buttons.

Design uses the shared panel palette from the spec (``--panel``,
``--panel-2``, ``--line``, ``--violet``, ``--violet-soft``, ``--blue``,
``--text``, ``--dim``, ``--ok``, ``--danger``) defined as module-local
constants so the shared token system in ``styles.py`` is untouched.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (
    QObject,
    QStandardPaths,
    QUrl,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .styles import C, R, S, T


# ─────────────────────────────────────────────────────────────
# Palette (shared across the three redesigned panels)
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
# File-type icon mapping (maps existing filename extension to a
# Unicode glyph — no new type-detection logic, just a lookup).
# ─────────────────────────────────────────────────────────────

_FILE_ICONS = {
    ".pdf": "📄",
    ".zip": "🗜", ".tar": "🗜", ".gz": "🗜", ".rar": "🗜",
    ".7z": "🗜", ".bz2": "🗜",
    ".jpg": "🖼", ".jpeg": "🖼", ".png": "🖼", ".gif": "🖼",
    ".svg": "🖼", ".webp": "🖼", ".bmp": "🖼", ".ico": "🖼",
    ".mp3": "🎵", ".wav": "🎵", ".flac": "🎵", ".ogg": "🎵",
    ".m4a": "🎵", ".aac": "🎵",
    ".mp4": "🎬", ".mkv": "🎬", ".avi": "🎬", ".mov": "🎬",
    ".webm": "🎬",
    ".py": "📜", ".js": "📜", ".html": "📜", ".css": "📜",
    ".json": "📜", ".xml": "📜", ".sh": "📜", ".bat": "📜",
    ".doc": "📝", ".docx": "📝", ".txt": "📝", ".md": "📝",
    ".rtf": "📝",
    ".exe": "⚙", ".msi": "⚙", ".dmg": "⚙", ".deb": "⚙",
    ".rpm": "⚙", ".appimage": "⚙",
}
_DEFAULT_ICON = "📎"


def _file_glyph(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return _FILE_ICONS.get(ext, _DEFAULT_ICON)


def _human_ago(ts: Optional[float]) -> str:
    if not ts:
        return ""
    import datetime
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


# ─────────────────────────────────────────────────────────────
# Download — single in-memory record.
# ─────────────────────────────────────────────────────────────

@dataclass
class Download:
    """In-memory record for an in-flight or finished download."""
    id: str
    filename: str
    destination: Path
    state: str  # "in_progress" | "finished" | "cancelled" | "failed"
    received_bytes: int
    total_bytes: int
    received_bytes_at_start: int
    started_at: float
    finished_at: Optional[float]
    _qitem: object = field(default=None, repr=False)

    @property
    def progress(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return min(1.0, self.received_bytes / self.total_bytes)

    @property
    def speed_bps(self) -> float:
        elapsed = max(0.1, time.time() - self.started_at)
        return max(0.0, (self.received_bytes - self.received_bytes_at_start) / elapsed)

    @property
    def eta_seconds(self) -> float:
        if self.speed_bps <= 0 or self.total_bytes <= 0:
            return 0.0
        remaining = self.total_bytes - self.received_bytes
        return max(0.0, remaining / self.speed_bps)


# ─────────────────────────────────────────────────────────────
# Helpers — formatting.
# ─────────────────────────────────────────────────────────────

def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    units = ["KB", "MB", "GB", "TB"]
    val = n / 1024
    for u in units:
        if val < 1024:
            return f"{val:.1f} {u}"
        val /= 1024
    return f"{val:.1f} PB"


def _fmt_eta(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    if seconds < 60:
        return f"{int(seconds)}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


# ─────────────────────────────────────────────────────────────
# DownloadManager — owns the list and the XDG path.
# ─────────────────────────────────────────────────────────────

class DownloadManager(QObject):
    """Owns all downloads in the current session.

    Receives a ``QWebEngineDownloadItem`` via :meth:`add`, accepts it,
    wires up the progress/finished signals, and emits a download
    dataclass to listeners. The destination is always under the XDG
    downloads directory (``~/Downloads`` by default, override with
    ``XDG_DOWNLOAD_DIR``).
    """

    download_added = pyqtSignal(object)     # Download
    download_changed = pyqtSignal(object)   # Download (progress update)
    download_finished = pyqtSignal(object)  # Download
    download_removed = pyqtSignal(str)      # download id

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        raw_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DownloadLocation
        )
        self._downloads_dir = Path(raw_dir) if raw_dir else Path.home() / "Downloads"
        self._downloads_dir.mkdir(parents=True, exist_ok=True)
        self._downloads: dict[str, Download] = {}

    # ── public API ──────────────────────────────────────────────

    @property
    def downloads_dir(self) -> Path:
        """The XDG download directory (resolved, exists on disk)."""
        return self._downloads_dir

    def all(self) -> list[Download]:
        """Return all current downloads (in-flight and finished)."""
        return list(self._downloads.values())

    def download_path(self, suggested_name: str) -> Path:
        """Resolve a non-clobbering path in the downloads directory."""
        suggested_name = Path(suggested_name).name
        if not suggested_name:
            suggested_name = "download"
        target = self._downloads_dir / suggested_name
        if not target.exists():
            return target
        stem, suffix = target.stem, target.suffix
        n = 1
        while True:
            candidate = self._downloads_dir / f"{stem} ({n}){suffix}"
            if not candidate.exists():
                return candidate
            n += 1

    def add(self, qitem) -> Download:
        """Accept a ``QWebEngineDownloadItem`` and track it."""
        suggested = getattr(qitem, "suggestedFileName", lambda: "download")() or "download"
        dest = self.download_path(suggested)
        try:
            qitem.setPath(str(dest))
        except Exception:
            pass
        try:
            qitem.accept()
        except Exception:
            pass

        try:
            total = int(qitem.totalBytes() or 0)
        except Exception:
            total = 0

        dl = Download(
            id=str(uuid.uuid4()),
            filename=suggested,
            destination=dest,
            state="in_progress",
            received_bytes=0,
            total_bytes=total,
            received_bytes_at_start=0,
            started_at=time.time(),
            finished_at=None,
            _qitem=qitem,
        )
        self._downloads[dl.id] = dl

        try:
            qitem.downloadProgress.connect(
                lambda rec, tot, d=dl: self._on_progress(d, rec, tot)
            )
            qitem.finished.connect(lambda d=dl: self._on_qt_finished(d))
        except Exception:
            pass

        self.download_added.emit(dl)
        return dl

    def cancel(self, dl_id: str) -> None:
        """Cancel an in-flight download (no-op if not in progress)."""
        dl = self._downloads.get(dl_id)
        if dl is None or dl.state != "in_progress":
            return
        try:
            if dl._qitem is not None and hasattr(dl._qitem, "cancel"):
                dl._qitem.cancel()
        except Exception:
            pass

    def remove(self, dl_id: str) -> None:
        """Remove a download from the manager."""
        if dl_id in self._downloads:
            del self._downloads[dl_id]
            self.download_removed.emit(dl_id)

    def clear_finished(self) -> None:
        """Remove all downloads whose state is not ``in_progress``."""
        for dl_id in [d.id for d in self._downloads.values() if d.state != "in_progress"]:
            self.remove(dl_id)

    def open_destination(self, dl_id: str) -> None:
        """Open the file in the OS file manager."""
        dl = self._downloads.get(dl_id)
        if dl is None:
            return
        path = dl.destination
        if not path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._downloads_dir)))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def reveal_destination(self, dl_id: str) -> None:
        """Reveal the file's containing folder in the OS file manager."""
        dl = self._downloads.get(dl_id)
        if dl is None:
            return
        path = dl.destination
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._downloads_dir)))

    def open_downloads_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._downloads_dir)))

    # ── Qt signal handlers ──────────────────────────────────────

    def _on_progress(self, dl: Download, received: int, total: int) -> None:
        if dl.id not in self._downloads:
            return
        dl.received_bytes = max(0, int(received))
        if int(total) > 0:
            dl.total_bytes = int(total)
        self.download_changed.emit(dl)

    def _on_qt_finished(self, dl: Download) -> None:
        if dl.id not in self._downloads:
            return
        finished_ok = True
        try:
            if dl._qitem is not None and hasattr(dl._qitem, "state"):
                from PyQt6.QtWebEngineCore import QWebEngineDownloadItem
                st = dl._qitem.state()
                if st == QWebEngineDownloadItem.DownloadState.DownloadCompleted:
                    finished_ok = True
                elif st == QWebEngineDownloadItem.DownloadState.DownloadCancelled:
                    finished_ok = False
                    dl.state = "cancelled"
                else:
                    finished_ok = False
                    dl.state = "failed"
        except Exception:
            finished_ok = True
        if finished_ok:
            dl.state = "finished"
        dl.finished_at = time.time()
        self.download_finished.emit(dl)


# ─────────────────────────────────────────────────────────────
# Shared row styles
# ─────────────────────────────────────────────────────────────

_ROW_STYLE = f"""
    QWidget {{
        background: {_PANEL};
        border-bottom: 1px solid {_LINE};
        border-radius: 0px;
    }}
"""

_ICON_TILE_STYLE = f"""
    background: {_VIOLET_SOFT};
    color: {_VIOLET};
    border-radius: 7px;
    border: none;
    font-size: 15px;
    font-weight: bold;
"""

_NAME_STYLE = f"""
    color: {_TEXT};
    font-size: 13.5px;
    font-weight: 500;
    background: transparent;
    border: none;
"""

_META_STYLE = f"""
    color: {_DIM};
    font-size: 11.5px;
    background: transparent;
    border: none;
"""

_META_OK_STYLE = f"""
    color: {_OK};
    font-size: 11.5px;
    background: transparent;
    border: none;
    font-weight: 500;
"""

_PROGRESS_STYLE = f"""
    QProgressBar {{
        background: {_PANEL_2};
        border: none;
        border-radius: 2px;
        height: 4px;
    }}
    QProgressBar::chunk {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {_VIOLET}, stop:1 {_BLUE});
        border-radius: 2px;
    }}
"""

_ACTION_BTN_STYLE = f"""
    QPushButton {{
        background: {_PANEL_2};
        color: {_TEXT};
        border: 1px solid {_LINE};
        border-radius: 8px;
        padding: 0;
        font-size: 11px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background: {_VIOLET_SOFT};
        color: {_VIOLET};
        border: 1px solid {_VIOLET};
    }}
"""

_HEADER_ICON_STYLE = f"""
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {_VIOLET}, stop:1 {_BLUE});
    color: #ffffff;
    border-radius: 7px;
    border: none;
    font-size: 16px;
    font-weight: bold;
"""

_COUNT_PILL_STYLE = f"""
    background: {_PANEL_2};
    color: {_DIM};
    border: 1px solid {_LINE};
    border-radius: 999px;
    padding: 2px 10px;
    font-size: 11.5px;
    border: none;
"""

_FOOTER_BTN_STYLE = f"""
    QPushButton {{
        background: transparent;
        color: {_TEXT};
        border: 1px solid {_LINE};
        border-radius: 8px;
        padding: 7px 14px;
        font-size: 12.5px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background: {_VIOLET_SOFT};
        color: {_VIOLET};
        border: 1px solid {_VIOLET};
    }}
"""


# ─────────────────────────────────────────────────────────────
# DownloadRow — single row in the side-panel tab.
# ─────────────────────────────────────────────────────────────

class DownloadRow(QWidget):
    """A single download row: icon tile, filename, metadata, progress bar,
    and right-aligned action buttons."""

    cancel_requested = pyqtSignal(str)   # download id
    open_requested = pyqtSignal(str)     # download id (open file)
    reveal_requested = pyqtSignal(str)   # download id (show in folder)

    def __init__(self, download: Download, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._dl = download
        self._build_ui()
        self.update_from(download)

    def _build_ui(self) -> None:
        self.setStyleSheet(_ROW_STYLE)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        # Left icon tile
        self._icon = QLabel()
        self._icon.setFixedSize(28, 28)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setStyleSheet(_ICON_TILE_STYLE)
        layout.addWidget(self._icon)

        # Middle: name + meta + progress
        mid = QVBoxLayout()
        mid.setSpacing(3)
        mid.setContentsMargins(0, 0, 0, 0)

        self._name = QLabel()
        self._name.setStyleSheet(_NAME_STYLE)
        self._name.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._name.setWordWrap(False)
        mid.addWidget(self._name)

        self._meta = QLabel()
        self._meta.setStyleSheet(_META_STYLE)
        self._meta.setAlignment(Qt.AlignmentFlag.AlignLeft)
        mid.addWidget(self._meta)

        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(4)
        self._bar.setStyleSheet(_PROGRESS_STYLE)
        mid.addWidget(self._bar)

        layout.addLayout(mid, stretch=1)

        # Right: action buttons
        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.setContentsMargins(0, 0, 0, 0)

        self._btn_primary = QPushButton()
        self._btn_secondary = QPushButton()
        for btn in (self._btn_primary, self._btn_secondary):
            btn.setFixedSize(28, 28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(_ACTION_BTN_STYLE)

        self._btn_primary.clicked.connect(self._on_primary)
        self._btn_secondary.clicked.connect(self._on_secondary)

        actions.addWidget(self._btn_primary)
        actions.addWidget(self._btn_secondary)
        layout.addLayout(actions)

    def _on_primary(self) -> None:
        if self._dl.state == "in_progress":
            self.cancel_requested.emit(self._dl.id)
        else:
            self.open_requested.emit(self._dl.id)

    def _on_secondary(self) -> None:
        if self._dl.state == "in_progress":
            self.cancel_requested.emit(self._dl.id)
        else:
            self.reveal_requested.emit(self._dl.id)

    def update_from(self, dl: Download) -> None:
        """Refresh the row to reflect the current state of the download."""
        self._dl = dl
        self._icon.setText(_file_glyph(dl.filename))

        name = dl.filename
        if len(name) > 50:
            stem, _, suffix = name.rpartition(".")
            short = stem[:30] + "…" + stem[-10:] + ("." + suffix if suffix else "")
            name = short
        self._name.setText(name)
        self._name.setToolTip(dl.filename)

        if dl.state == "in_progress":
            self._bar.setRange(0, 1000)
            self._bar.setValue(int(dl.progress * 1000))
            self._bar.show()
            rec = _fmt_bytes(dl.received_bytes)
            tot = _fmt_bytes(dl.total_bytes) if dl.total_bytes > 0 else "?"
            eta = _fmt_eta(dl.eta_seconds)
            self._meta.setText(f"{rec} / {tot}  ·  {eta} left")
            self._meta.setStyleSheet(_META_STYLE)
            self._btn_primary.setText("Pause")
            self._btn_primary.setToolTip("Cancel download")
            self._btn_secondary.setText("Cancel")
            self._btn_secondary.setToolTip("Cancel download")
        elif dl.state == "finished":
            self._bar.hide()
            size = _fmt_bytes(dl.received_bytes)
            ago = _human_ago(dl.finished_at)
            self._meta.setText(f"✓ Complete  ·  {size}  ·  {ago}")
            self._meta.setStyleSheet(_META_OK_STYLE)
            self._btn_primary.setText("Open")
            self._btn_primary.setToolTip("Open file")
            self._btn_secondary.setText("Show in folder")
            self._btn_secondary.setToolTip("Show in folder")
        elif dl.state == "cancelled":
            self._bar.hide()
            self._meta.setText("Cancelled")
            self._meta.setStyleSheet(_META_STYLE)
            self._btn_primary.setText("Open")
            self._btn_primary.setToolTip("Open containing folder")
            self._btn_secondary.setText("Show in folder")
            self._btn_secondary.setToolTip("Show in folder")
        else:  # failed
            self._bar.hide()
            self._meta.setText("Failed")
            self._meta.setStyleSheet(_META_STYLE)
            self._btn_primary.setText("Open")
            self._btn_primary.setToolTip("Open containing folder")
            self._btn_secondary.setText("Show in folder")
            self._btn_secondary.setToolTip("Show in folder")


# ─────────────────────────────────────────────────────────────
# DownloadsTab — tab in the side panel.
# ─────────────────────────────────────────────────────────────

class DownloadsTab(QWidget):
    """Side-panel tab listing all downloads.

    Holds a scrollable list of :class:`DownloadRow` and a footer with
    "Clear finished" and "Open ~/Downloads" buttons. Subscribes to
    the manager's signals.
    """

    def __init__(
        self,
        manager: DownloadManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._rows: dict[str, DownloadRow] = {}
        self._build_ui()
        self._wire()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setStyleSheet(f"background: {_PANEL}; border: none;")
        hdr_layout = QHBoxLayout(header)
        hdr_layout.setContentsMargins(14, 12, 14, 12)
        hdr_layout.setSpacing(10)

        icon = QLabel("↓")
        icon.setFixedSize(28, 28)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(_HEADER_ICON_STYLE)
        hdr_layout.addWidget(icon)

        title = QLabel("Downloads")
        title.setStyleSheet(
            f"color: {_TEXT}; font-size: 15px; font-weight: 600; "
            f"background: transparent; border: none;"
        )
        hdr_layout.addWidget(title)

        self._count_pill = QLabel("0 items")
        self._count_pill.setStyleSheet(_COUNT_PILL_STYLE)
        self._count_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr_layout.addWidget(self._count_pill, alignment=Qt.AlignmentFlag.AlignRight)

        layout.addWidget(header)

        # Divider under header
        divider = QWidget()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {_LINE}; border: none;")
        layout.addWidget(divider)

        # Scrollable area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"""
            QScrollArea {{ background: {_PANEL}; border: none; }}
            QScrollBar:vertical {{
                background: {_PANEL};
                width: 8px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {_LINE};
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            """
        )
        self._list_holder = QWidget()
        self._list_layout = QVBoxLayout(self._list_holder)
        self._list_layout.setContentsMargins(8, 8, 8, 8)
        self._list_layout.setSpacing(0)
        self._list_layout.addStretch(1)
        scroll.setWidget(self._list_holder)
        layout.addWidget(scroll, stretch=1)

        # Empty-state label
        self._empty = QLabel("No downloads yet.\nFiles you download will appear here.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(
            f"color: {_DIM}; font-size: 13px; "
            f"background: transparent; border: none; padding: 32px 16px;"
        )
        self._list_layout.insertWidget(0, self._empty)
        self._empty.show()

        # Footer
        footer = QHBoxLayout()
        footer.setContentsMargins(12, 10, 12, 12)
        footer.setSpacing(8)

        self._clear_btn = QPushButton("Clear finished")
        self._open_dir_btn = QPushButton(f"Open {self._manager.downloads_dir.name}")
        for btn in (self._clear_btn, self._open_dir_btn):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(_FOOTER_BTN_STYLE)

        self._clear_btn.clicked.connect(self._on_clear_clicked)
        self._open_dir_btn.clicked.connect(self._manager.open_downloads_dir)
        footer.addWidget(self._clear_btn)
        footer.addWidget(self._open_dir_btn)
        footer.addStretch(1)
        layout.addLayout(footer)

    def _wire(self) -> None:
        self._manager.download_added.connect(self._on_added)
        self._manager.download_changed.connect(self._on_changed)
        self._manager.download_finished.connect(self._on_finished)
        self._manager.download_removed.connect(self._on_removed)

    # ── manager signal handlers ─────────────────────────────────

    def _on_added(self, dl: Download) -> None:
        row = DownloadRow(dl)
        row.cancel_requested.connect(self._manager.cancel)
        row.open_requested.connect(self._manager.open_destination)
        row.reveal_requested.connect(self._manager.reveal_destination)
        self._rows[dl.id] = row
        self._list_layout.insertWidget(0, row)
        self._empty.hide()
        self._update_count()

    def _on_changed(self, dl: Download) -> None:
        row = self._rows.get(dl.id)
        if row is not None:
            row.update_from(dl)

    def _on_finished(self, dl: Download) -> None:
        row = self._rows.get(dl.id)
        if row is not None:
            row.update_from(dl)
        self._update_count()

    def _on_removed(self, dl_id: str) -> None:
        row = self._rows.pop(dl_id, None)
        if row is not None:
            self._list_layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        if not self._rows:
            self._empty.show()
        self._update_count()

    def _on_clear_clicked(self) -> None:
        self._manager.clear_finished()

    def _update_count(self) -> None:
        count = len(self._rows)
        self._count_pill.setText(f"{count} item{'s' if count != 1 else ''}")
