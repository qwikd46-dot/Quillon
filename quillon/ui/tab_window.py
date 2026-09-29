#!/usr/bin/env python3
"""TabWindow – independent top‑level window for a Quillon tab.
Each TabWindow holds its own QWebEngineView and communicates with the
WindowManager (formerly MainWindow) via signals.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QMainWindow
from PyQt6.QtWebEngineWidgets import QWebEngineView


class TabWindow(QMainWindow):
    """A standalone browser window representing a single tab.

    - `index` is assigned by the manager when the window is created.
    - Signals inform the manager about load completion and window closure.
    """

    loadFinished = pyqtSignal(int, bool)  # (index, ok)
    windowClosed = pyqtSignal(int)       # index

    def __init__(self, index: int, url: str | None = None, parent=None):
        super().__init__(parent)
        self.index = index
        self.setWindowTitle(f"Quillon – Tab {index + 1}")
        self.view = QWebEngineView(self)
        self.setCentralWidget(self.view)
        self.view.loadFinished.connect(self._on_load_finished)
        # Load the initial URL (home or custom)
        if url:
            self.view.load(url)
        else:
            # Fallback to home (managed by server)
            self.view.load("http://127.0.0.1:8889/")

    def _on_load_finished(self, ok: bool):
        # Emit to manager for synchronization / UI updates
        self.loadFinished.emit(self.index, ok)

    def closeEvent(self, event):
        # Notify manager before the window actually disappears
        self.windowClosed.emit(self.index)
        super().closeEvent(event)

    def navigate(self, url: str):
        """Navigate this window to a new URL."""
        self.view.load(url)

    def get_url(self) -> str:
        return self.view.url().toString()

    def get_title(self) -> str:
        return self.view.title() or f"Tab {self.index + 1}"