"""BFSB — Browser for Safe Browsing.

A privacy-focused browser built on Qt WebEngine with local metasearch engine.
"""

__version__ = "1.0.0"
__author__ = "BFSB Team"

from .core import (
    PATHS,
    APP_CONFIG,
    SECURITY_CONFIG,
    CRYPTO_CONFIG,
    URLBlocker,
    CookieVault,
    SafePage,
    RequestInterceptor,
    create_web_view,
    create_web_profile,
    configure_web_settings,
)

from .ui import (
    DIMS,
    STYLES,
    C,
    BFSBWindow,
    TabBar,
    NavigationBar,
)

__all__ = [
    "PATHS",
    "APP_CONFIG",
    "SECURITY_CONFIG",
    "CRYPTO_CONFIG",
    "URLBlocker",
    "CookieVault",
    "SafePage",
    "RequestInterceptor",
    "create_web_view",
    "create_web_profile",
    "configure_web_settings",
    "DIMS",
    "STYLES",
    "C",
    "BFSBWindow",
    "TabBar",
    "NavigationBar",
]