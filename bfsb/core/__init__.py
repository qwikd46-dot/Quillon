"""Core package exports.

Names resolve on first use (PEP 562) rather than at import time. The
eager version ran ``webengine`` -- and with it the whole PyQt6 stack --
on every ``import bfsb.core``, including from mitmdump, which only ever
wants a single leaf module and has no GUI at all.
"""

from importlib import import_module as _import_module

_SUBMODULES = {
    "PATHS": "config",
    "APP_CONFIG": "config",
    "SECURITY_CONFIG": "config",
    "CRYPTO_CONFIG": "config",
    "HARDWARE_FINGERPRINT": "crypto",
    "KEY_MANAGER": "crypto",
    "URLBlocker": "blocker",
    "CookieVault": "cookies",
    "PasswordVault": "passwords",
    "VaultIntegrityError": "secure_vault",
    "VaultKeyError": "secure_vault",
    "VaultKeyProvider": "secure_vault",
    "VaultStore": "secure_vault",
    "BraveAdBlockEngine": "brave_adblock",
    "get_brave_adblock": "brave_adblock",
    "init_brave_adblock": "brave_adblock",
    "setup_tampermonkey_scripts": "tampermonkey_scripts",
    "inject_ghostery_scriptlet": "tampermonkey_scripts",
    "TampermonkeyScriptManager": "tampermonkey_scripts",
    "BFSBProxyManager": "proxy_manager",
    "run_proxy": "proxy_manager",
    "SafePage": "webengine",
    "RequestInterceptor": "webengine",
    "create_web_profile": "webengine",
    "configure_web_settings": "webengine",
    "create_web_view": "webengine",
    "BFSBPage": "webengine",
    "set_bfsb_action_target": "webengine",
    "BFSHBServer": "server",
    "get_server": "server",
    "shutdown_server": "server",
    "SearchManager": "search",
    "SyncSearchManager": "search",
    "SearchResult": "search",
    "SearchResponse": "search",
    "MergedSearchResponse": "search",
    "EngineType": "search",
    "BrowserDB": "storage",
    "BookmarkStore": "storage",
    "Bookmark": "storage",
    "HistoryStore": "storage",
    "HistoryEntry": "storage",
    "import_chrome_json": "storage",
    "import_firefox_json": "storage",
}

__all__ = [
    "PATHS",
    "APP_CONFIG",
    "SECURITY_CONFIG",
    "CRYPTO_CONFIG",
    "HARDWARE_FINGERPRINT",
    "KEY_MANAGER",
    "URLBlocker",
    "CookieVault",
    "PasswordVault",
    "VaultIntegrityError",
    "VaultKeyError",
    "VaultKeyProvider",
    "VaultStore",
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
    "BFSBProxyManager",
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


def __getattr__(name):
    module_name = _SUBMODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(_import_module(f".{module_name}", __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
