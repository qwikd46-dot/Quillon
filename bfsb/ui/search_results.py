"""Native Search Results Widget — Qt-based results display.

Replaces SearXNG templates with native Qt widgets for:
- Better performance
- No HTML injection needed
- Full control over styling via design system
- Virus-proof (no HTML parsing at render time)
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QDesktopServices, QPixmap, QColor, QIcon
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QFrame,
    QPushButton,
    QSizePolicy,
    QGridLayout,
)

from bfsb.core.search.models import SearchResult
from bfsb.core.search.manager import MergedSearchResponse
from bfsb.ui.styles import DIMS, STYLES, get_palette


class ResultCard(QFrame):
    """Individual search result card — clickable, styled, virus-proof."""
    
    clicked = pyqtSignal(str)  # Emits URL
    
    def __init__(self, result: SearchResult, rank: int):
        super().__init__()
        self.result = result
        self.rank = rank
        self._setup_ui()
        self._apply_style()
    
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        # Header: Favicon + URL + Title
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 16, 16, 8)
        header_layout.setSpacing(12)
        
        # Favicon
        self.favicon_label = QLabel()
        self.favicon_label.setFixedSize(20, 20)
        self.favicon_label.setScaledContents(True)
        header_layout.addWidget(self.favicon_label)
        
        # Title + URL column
        meta = QWidget()
        meta_layout = QVBoxLayout(meta)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(4)
        
        # URL breadcrumb
        self.url_label = QLabel(self._format_url(self.result.url))
        self.url_label.setWordWrap(True)
        meta_layout.addWidget(self.url_label)
        
        # Title
        self.title_label = QLabel(self.result.title)
        self.title_label.setWordWrap(True)
        meta_layout.addWidget(self.title_label)
        
        header_layout.addWidget(meta, 1)
        layout.addWidget(header)
        
        # Snippet
        if self.result.snippet:
            self.snippet_label = QLabel(self.result.snippet)
            self.snippet_label.setWordWrap(True)
            self.snippet_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            layout.addWidget(self.snippet_label)
        
        # Footer: Domain badge + engine badges
        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 4, 16, 12)
        footer_layout.setSpacing(8)
        
        # Source domain
        self.domain_label = QLabel(self.result.source_domain)
        footer_layout.addWidget(self.domain_label)
        
        footer_layout.addStretch()
        
        # Engine badges
        for engine in self.result.engines:
            badge = QLabel(engine)  # engine is already a string (engine.value from merger)
            badge.setProperty("engineBadge", True)
            footer_layout.addWidget(badge)
        
        layout.addWidget(footer)
    
    def _format_url(self, url: str) -> str:
        """Format URL as breadcrumb."""
        try:
            parsed = urlparse(url)
            parts = [p for p in parsed.path.split('/') if p]
            domain = parsed.netloc.replace('www.', '')
            if parts:
                return f"{domain} / {' / '.join(parts[:3])}"
            return domain
        except Exception:
            return url
    
    def _apply_style(self) -> None:
        self.setProperty("resultCard", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.NoFrame)
        
        # Set object names for styling
        self.url_label.setObjectName("resultUrl")
        self.title_label.setObjectName("resultTitle")
        self.snippet_label.setObjectName("resultSnippet")
        self.domain_label.setObjectName("resultDomain")
    
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.result.url)
        super().mousePressEvent(event)
    
    def enterEvent(self, event) -> None:
        self.setProperty("hovered", True)
        self.style().unpolish(self)
        self.style().polish(self)
        super().enterEvent(event)
    
    def leaveEvent(self, event) -> None:
        self.setProperty("hovered", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().leaveEvent(event)


class ResultsWidget(QWidget):
    """Main results container — scrollable list of result cards."""
    
    resultClicked = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self._cards: list[ResultCard] = []
        self._setup_ui()
    
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Scroll area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        # Container widget
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(24, 24, 24, 24)
        self.container_layout.setSpacing(16)
        self.container_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll)
    
    def set_results(self, response: MergedSearchResponse) -> None:
        """Display search results."""
        self.clear()
        
        if response.total_results == 0:
            self._show_empty_state(response.query)
            return
        
        for i, result in enumerate(response.results, 1):
            card = ResultCard(result, i)
            card.clicked.connect(self.resultClicked.emit)
            self._cards.append(card)
            self.container_layout.addWidget(card)
        
        # Add stretch at bottom
        self.container_layout.addStretch()
    
    def _show_empty_state(self, query: str) -> None:
        """Show empty state message."""
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        icon = QLabel("🔍")
        icon.setStyleSheet("font-size: 48px;")
        empty_layout.addWidget(icon)
        
        msg = QLabel(f'No results for "{query}"')
        msg.setObjectName("emptyMessage")
        empty_layout.addWidget(msg)
        
        hint = QLabel("Try different keywords or check your spelling")
        hint.setObjectName("emptyHint")
        empty_layout.addWidget(hint)
        
        self.container_layout.addWidget(empty)
        self.container_layout.addStretch()
    
    def clear(self) -> None:
        """Remove all result cards."""
        for card in self._cards:
            card.deleteLater()
        self._cards.clear()
        
        # Remove empty state if present
        for i in range(self.container_layout.count()):
            item = self.container_layout.itemAt(i)
            if item and item.widget():
                item.widget().deleteLater()
    
    def scroll_to_top(self) -> None:
        """Scroll to top of results."""
        self.scroll.verticalScrollBar().setValue(0)


class PaginationWidget(QWidget):
    """Pagination controls at bottom of results."""
    
    pageChanged = pyqtSignal(int)
    
    def __init__(self):
        super().__init__()
        self.current_page = 1
        self.total_pages = 1
        self._setup_ui()
    
    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 24)
        layout.setSpacing(8)
        
        # Previous button
        self.prev_btn = QPushButton("← Previous")
        self.prev_btn.setObjectName("paginationBtn")
        self.prev_btn.clicked.connect(self._prev_page)
        layout.addWidget(self.prev_btn)
        
        # Page numbers
        self.pages_container = QWidget()
        self.pages_layout = QHBoxLayout(self.pages_container)
        self.pages_layout.setContentsMargins(0, 0, 0, 0)
        self.pages_layout.setSpacing(4)
        layout.addWidget(self.pages_container, 1)
        
        # Next button
        self.next_btn = QPushButton("Next →")
        self.next_btn.setObjectName("paginationBtn")
        self.next_btn.clicked.connect(self._next_page)
        layout.addWidget(self.next_btn)
    
    def set_pages(self, current: int, total: int) -> None:
        """Update pagination display."""
        self.current_page = current
        self.total_pages = max(1, total)
        
        # Update buttons
        self.prev_btn.setEnabled(current > 1)
        self.next_btn.setEnabled(current < self.total_pages)
        
        # Rebuild page numbers
        for i in range(self.pages_layout.count()):
            item = self.pages_layout.itemAt(i)
            if item and item.widget():
                item.widget().deleteLater()
        
        # Show up to 5 pages around current
        start = max(1, current - 2)
        end = min(self.total_pages, current + 2)
        
        if start > 1:
            self._add_page_btn(1)
            if start > 2:
                ellipsis = QLabel("…")
                ellipsis.setObjectName("pageEllipsis")
                self.pages_layout.addWidget(ellipsis)
        
        for page in range(start, end + 1):
            self._add_page_btn(page, page == current)
        
        if end < self.total_pages:
            if end < self.total_pages - 1:
                ellipsis = QLabel("…")
                ellipsis.setObjectName("pageEllipsis")
                self.pages_layout.addWidget(ellipsis)
            self._add_page_btn(self.total_pages)
    
    def _add_page_btn(self, page: int, active: bool = False) -> None:
        btn = QPushButton(str(page))
        btn.setObjectName("pageBtn")
        btn.setCheckable(True)
        btn.setChecked(active)
        btn.setFixedSize(36, 36)
        if active:
            btn.setProperty("active", True)
        btn.clicked.connect(lambda: self.pageChanged.emit(page))
        self.pages_layout.addWidget(btn)
    
    def _prev_page(self) -> None:
        if self.current_page > 1:
            self.pageChanged.emit(self.current_page - 1)
    
    def _next_page(self) -> None:
        if self.current_page < self.total_pages:
            self.pageChanged.emit(self.current_page + 1)


class SearchResultsView(QWidget):
    """Complete search results view — combines results + pagination."""
    
    navigateRequested = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.current_query = ""
        self.current_page = 1
        self._setup_ui()
    
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Results
        self.results = ResultsWidget()
        self.results.resultClicked.connect(self.navigateRequested.emit)
        layout.addWidget(self.results, 1)
        
        # Pagination
        self.pagination = PaginationWidget()
        self.pagination.pageChanged.connect(self._on_page_changed)
        layout.addWidget(self.pagination)
    
    def display(self, response: MergedSearchResponse) -> None:
        """Display search response."""
        self.current_query = response.query
        self.results.set_results(response)
        
        # Estimate total pages (rough)
        total_pages = max(1, (response.total_results + 9) // 10)
        self.pagination.set_pages(response.page, total_pages)
    
    def _on_page_changed(self, page: int) -> None:
        """Handle page change — emit signal for parent to fetch."""
        self.current_page = page
        # Parent should fetch new results and call display()
    
    def clear(self) -> None:
        """Clear results."""
        self.results.clear()
        self.pagination.set_pages(1, 1)