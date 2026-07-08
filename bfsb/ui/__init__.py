"""UI package exports."""

from .styles import DIMS, STYLES, get_palette, C
from .components import IconButton, TabCloseButton, FaviconLabel, TabLabel, SecurityBadge, create_tab_widget
from .tabs import TabBar, TabWidget, NewTabButton, TabCloseButton as TabCloseButtonV2, TabLabel as TabLabelV2, FaviconLabel as FaviconLabelV2
from .navigation import NavigationBar, NavButton, AboutDialog
from .main_window import BFSBWindow
from .search_results import ResultCard, ResultsWidget, PaginationWidget, SearchResultsView

__all__ = [
    "DIMS",
    "STYLES",
    "get_palette",
    "C",
    "IconButton",
    "TabCloseButton",
    "FaviconLabel",
    "TabLabel",
    "SecurityBadge",
    "create_tab_widget",
    "TabBar",
    "TabWidget",
    "NewTabButton",
    "NavigationBar",
    "NavButton",
    "AboutDialog",
    "BFSBWindow",
    "ResultCard",
    "ResultsWidget",
    "PaginationWidget",
    "SearchResultsView",
]