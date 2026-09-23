"""Core package exports."""

from .config import PATHS, APP_CONFIG, SECURITY_CONFIG, CRYPTO_CONFIG
from .crypto import HARDWARE_FINGERPRINT, KEY_MANAGER
from .blocker import URLBlocker
from .cookies import CookieVault
from .brave_adblock import (
    BraveAdBlockEngine,
    get_brave_adblock,
    init_brave_adblock,
)
from .tampermonkey_scripts import (
    setup_tampermonkey_scripts,
    inject_ghostery_scriptlet,
    TampermonkeyScriptManager,
)
from .proxy_manager import BFSBProxyManager, run_proxy
from .webengine import (
    SafePage,
    RequestInterceptor,
    create_web_profile,
    configure_web_settings,
    create_web_view,
    BFSBPage,
    set_bfsb_action_target,
)
from .server import BFSHBServer, get_server, shutdown_server
from .search import (
    SearchManager,
    SyncSearchManager,
    SearchResult,
    SearchResponse,
    MergedSearchResponse,
    EngineType,
)

from .storage import BrowserDB, BookmarkStore, Bookmark, HistoryStore, HistoryEntry
from .storage import import_chrome_json, import_firefox_json

__all__ = [
    "PATHS",
    "APP_CONFIG",
    "SECURITY_CONFIG",
    "CRYPTO_CONFIG",
    "HARDWARE_FINGERPRINT",
    "KEY_MANAGER",
    "URLBlocker",
    "CookieVault",
    "BrowserDB",
    "BookmarkStore",
    "Bookmark",
    "HistoryStore",
    "HistoryEntry",
    "import_chrome_json",
    "import_firefox_json",
    "SafePage",
    "RequestInterceptor",
    "create_web_profile",
    "configure_web_settings",
    "create_web_view",
    "BFSBPage",
    "BFSBProxy",
    "run_proxy",
    "BFSHBServer",
    "get_server",
    "shutdown_server",
    "SearchManager",
    "SyncSearchManager",
    "SearchResult",
    "SearchResponse",
    "MergedSearchResponse",
    "EngineType",
]