"""UI package exports."""

from .styles import DIMS, STYLES, get_palette, C
from .components import IconButton, TabCloseButton, FaviconLabel, TabLabel, SecurityBadge, create_tab_widget
from .tabs import TabBar, TabWidget, NewTabButton, TabCloseButton as TabCloseButtonV2, TabLabel as TabLabelV2, FaviconLabel as FaviconLabelV2
from .navigation import NavigationBar, NavButton, AboutDialog
from .menu import QuillonMenu
from .main_window import QuillonWindow
from .search_results import ResultCard, ResultsWidget, PaginationWidget, SearchResultsView
from .browser_chrome import BrowserChrome, NativeTabBar, NativeNavBar, NativeTab
from .category_popup import CategoryPopup
from .popover import (
    QuillonPopover,
    PopoverHost,
    ListPopover,
    BookmarksPopover,
    HistoryPopover,
    Entry,
)
from .downloads import DownloadManager, DownloadRow, DownloadsTab, Download

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
    "QuillonMenu",
    "QuillonWindow",
    "ResultCard",
    "ResultsWidget",
    "PaginationWidget",
    "SearchResultsView",
    "BrowserChrome",
    "NativeTabBar",
    "NativeNavBar",
    "NativeTab",
    "CategoryPopup",
    "QuillonPopover",
    "PopoverHost",
    "ListPopover",
    "BookmarksPopover",
    "HistoryPopover",
    "Entry",
    "DownloadManager",
    "DownloadRow",
    "DownloadsTab",
    "Download",
]