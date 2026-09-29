"""Quillon — Browser for Safe Browsing.

A privacy-focused browser built on Qt WebEngine with local metasearch engine.

Exports resolve on first use (PEP 562). Importing ``quillon`` eagerly pulled
in ``quillon.ui`` and therefore PyQt6, which any headless consumer of
``quillon.core`` -- mitmdump loading the proxy addon, most of all -- paid for
on every start.
"""

__version__ = "1.0.0"
__author__ = "Quillon Team"

from importlib import import_module as _import_module

_SUBMODULES = {
    "PATHS": "core",
    "APP_CONFIG": "core",
    "SECURITY_CONFIG": "core",
    "CRYPTO_CONFIG": "core",
    "URLBlocker": "core",
    "CookieVault": "core",
    "SafePage": "core",
    "RequestInterceptor": "core",
    "create_web_view": "core",
    "create_web_profile": "core",
    "configure_web_settings": "core",
    "DIMS": "ui",
    "STYLES": "ui",
    "C": "ui",
    "QuillonWindow": "ui",
    "TabBar": "ui",
    "NavigationBar": "ui",
}

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
    "QuillonWindow",
    "TabBar",
    "NavigationBar",
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
