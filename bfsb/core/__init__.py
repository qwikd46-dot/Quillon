"""Core package exports."""

from .config import PATHS, APP_CONFIG, SECURITY_CONFIG, CRYPTO_CONFIG
from .crypto import HARDWARE_FINGERPRINT, KEY_MANAGER
from .blocker import URLBlocker
from .cookies import CookieVault
from .webengine import (
    SafePage,
    RequestInterceptor,
    create_web_profile,
    configure_web_settings,
    create_web_view,
    BFSBPage,
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

__all__ = [
    "PATHS",
    "APP_CONFIG",
    "SECURITY_CONFIG",
    "CRYPTO_CONFIG",
    "HARDWARE_FINGERPRINT",
    "KEY_MANAGER",
    "URLBlocker",
    "CookieVault",
    "SafePage",
    "RequestInterceptor",
    "create_web_profile",
    "configure_web_settings",
    "create_web_view",
    "BFSBPage",
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