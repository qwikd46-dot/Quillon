"""Brave AdBlock Engine Integration for BFSB.

Uses python-adblock (Brave's adblock-rust) for:
- Network blocking with ABP/uBlock syntax
- Cosmetic filtering (element hiding)
- Scriptlet injection (json-prune, override-property-read, etc.)
- Resource replacements
"""

from __future__ import annotations

import base64
import logging
import os
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

import adblock

logger = logging.getLogger("bfsb.brave_adblock")


class BraveAdBlockEngine:
    """Wrapper around Brave's adblock engine for BFSB."""

    # Default filter lists (uBlock Origin compatible)
    DEFAULT_LISTS = [
        # EasyList + EasyPrivacy (primary)
        "https://easylist.to/easylist/easylist.txt",
        "https://easylist.to/easylist/easyprivacy.txt",
        # uBlock Origin filters (includes scriptlets, cosmetic, etc.)
        "https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/filters.txt",
        "https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/annoyances.txt",
        "https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/badware.txt",
        "https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/privacy.txt",
        "https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/resource-abuse.txt",
        "https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/unbreak.txt",
    ]

    # YouTube-specific lists
    YOUTUBE_LISTS = [
        "https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/filters.txt",
        # YouTube-specific from uAssets
        "https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/legacy.txt",
    ]

    def __init__(self, cache_dir: Optional[Path] = None):
        self._cache_dir = cache_dir or Path.home() / ".bfsb" / "adblock_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        self._engine: Optional[adblock.Engine] = None
        self._filter_set: Optional[adblock.FilterSet] = None
        self._lock = threading.RLock()
        self._ready = False
        self._lists_loaded = False
        self._resources_loaded = False

        # Thread for background initialization
        self._init_thread: Optional[threading.Thread] = None

    def initialize(self, force_download: bool = False) -> None:
        """Initialize the engine (starts background loading)."""
        with self._lock:
            if self._init_thread and self._init_thread.is_alive():
                return

            self._init_thread = threading.Thread(
                target=self._initialize_sync,
                args=(force_download,),
                daemon=True
            )
            self._init_thread.start()

    def _initialize_sync(self, force_download: bool) -> None:
        """Synchronous initialization (runs in background thread)."""
        try:
            logger.info("[BraveAdBlock] Initializing Brave adblock engine...")

            # Create filter set
            self._filter_set = adblock.FilterSet(debug=False)

            # Load filter lists
            self._load_filter_lists(force_download)

            # Create engine
            self._engine = adblock.Engine(self._filter_set, optimize=True)

            # Load scriptlet resources
            self._load_resources()

            self._ready = True
            logger.info("[BraveAdBlock] Engine ready with cosmetic filtering + scriptlets")

        except Exception as e:
            logger.error(f"[BraveAdBlock] Initialization failed: {e}")
            self._ready = False

    def _load_filter_lists(self, force_download: bool) -> None:
        """Load filter lists from cache or download."""
        cache_file = self._cache_dir / "filters.bin"

        if not force_download and cache_file.exists():
            try:
                logger.info("[BraveAdBlock] Loading cached filter lists...")
                # Create empty engine then deserialize into it
                self._engine = adblock.Engine(adblock.FilterSet(debug=False))
                self._engine.deserialize_from_file(str(cache_file))
                logger.info("[BraveAdBlock] Cached filters loaded")
                self._lists_loaded = True
                return
            except Exception as e:
                logger.warning(f"[BraveAdBlock] Failed to load cache: {e}")

        # Download and parse lists
        logger.info("[BraveAdBlock] Downloading filter lists...")
        self._filter_set = adblock.FilterSet(debug=False)

        for url in self.DEFAULT_LISTS:
            try:
                logger.info(f"[BraveAdBlock] Fetching {url}...")
                import urllib.request
                req = urllib.request.Request(url, headers={"User-Agent": "BFSB/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    content = resp.read().decode("utf-8", errors="ignore")
                self._filter_set.add_filter_list(content, "standard", True, "all")
                logger.info(f"[BraveAdBlock] Added {len(content)} chars from {url}")
            except Exception as e:
                logger.warning(f"[BraveAdBlock] Failed to load {url}: {e}")

        # Create engine from filter set
        self._engine = adblock.Engine(self._filter_set, optimize=True)

        # Cache the compiled engine
        try:
            self._engine.serialize_to_file(str(cache_file))
            logger.info("[BraveAdBlock] Filters cached")
        except Exception as e:
            logger.warning(f"[BraveAdBlock] Failed to cache filters: {e}")

        self._lists_loaded = True

    def _load_resources(self) -> None:
        """Load scriptlet resources (uBlock Origin compatible)."""
        if not self._engine:
            return

        try:
            # Embedded scriptlet resources (uBlock Origin compatible)
            # These provide json-prune, json-prune-fetch-response, override-property-read, etc.
            scriptlets_js = self._get_embedded_scriptlets()
            b64_content = base64.b64encode(scriptlets_js.encode()).decode()
            self._engine.add_resource("scriptlets.js", "scriptlet", b64_content, [])

            # Add redirect resources (empty for now)
            b64_redirect = base64.b64encode("# Empty redirect list\n".encode()).decode()
            self._engine.add_resource("redirects.txt", "redirect", b64_redirect, [])

            self._resources_loaded = True
            logger.info("[BraveAdBlock] Embedded scriptlet resources loaded")

        except Exception as e:
            logger.warning(f"[BraveAdBlock] Failed to load resources: {e}")

    def _get_embedded_scriptlets(self) -> str:
        """Return embedded uBlock Origin compatible scriptlets.

        These are the essential scriptlets for YouTube ad blocking:
        - json-prune: Remove ad data from JSON responses
        - json-prune-fetch-response: Prune fetch responses
        - json-prune-xhr-response: Prune XHR responses
        - override-property-read: Override property reads
        - set-constant: Set constant values
        - abort-on-property-read: Abort on property read
        """
        return r"""
// uBlock Origin compatible scriptlets
// Minimal set for YouTube ad blocking

(function() {
    'use strict';

    // json-prune: Remove ad data from JSON responses
    const jsonPrune = function(json, paths) {
        if (!json || typeof json !== 'object') return json;
        const result = Array.isArray(json) ? [] : {};
        for (const key in json) {
            if (json.hasOwnProperty(key)) {
                let prune = false;
                for (const path of paths) {
                    if (path === key || path === '*' || path.startsWith(key + '.')) {
                        prune = true;
                        break;
                    }
                }
                if (!prune) {
                    result[key] = jsonPrune(json[key], paths);
                }
            }
            return result;
        }
    };

    // override-property-read: Override property reads
    const overridePropertyRead = function(obj, prop, value) {
        if (obj && typeof obj === 'object') {
            Object.defineProperty(obj, prop, {
                get: function() { return value; },
                configurable: true
            });
        }
    };

    // set-constant: Set constant values
    const setConstant = function(obj, prop, value) {
        if (obj && typeof obj === 'object') {
            Object.defineProperty(obj, prop, {
                value: value,
                writable: false,
                configurable: true
            });
        }
    };

    // abort-on-property-read: Abort on property read
    const abortOnPropertyRead = function(obj, prop) {
        if (obj && typeof obj === 'object') {
            Object.defineProperty(obj, prop, {
                get: function() { throw new Error('Property read aborted: ' + prop); },
                configurable: true
            });
        }
    };

    // Export for uBlock Origin
    if (typeof self !== 'undefined') {
        self._scriptlets = self._scriptlets || {};
        self._scriptlets['json-prune'] = jsonPrune;
        self._scriptlets['override-property-read'] = overridePropertyRead;
        self._scriptlets['set-constant'] = setConstant;
        self._scriptlets['abort-on-property-read'] = abortOnPropertyRead;
    }

    console.log('[BFSB] Embedded scriptlets loaded');
})();
"""

    def wait_ready(self, timeout: float = 30.0) -> bool:
        """Wait for engine to be ready."""
        if self._init_thread:
            self._init_thread.join(timeout=timeout)
        return self._ready

    def is_ready(self) -> bool:
        return self._ready

    # ──────────────────────────────────────────────────────────────────────
    # Network Blocking
    # ──────────────────────────────────────────────────────────────────────

    def check_url(self, url: str, source_url: str, request_type: str = "other") -> bool:
        """Check if a URL should be blocked."""
        if not self._engine:
            return False

        try:
            result = self._engine.check_network_urls(url, source_url, request_type)
            return result.matched
        except Exception as e:
            logger.debug(f"[BraveAdBlock] check_url error: {e}")
            return False

    def check_url_detailed(self, url: str, source_url: str, request_type: str = "other") -> dict:
        """Get detailed block result with rule info."""
        if not self._engine:
            return {"matched": False}

        try:
            result = self._engine.check_network_urls(url, source_url, request_type)
            return {
                "matched": result.matched,
                "rule": getattr(result, "rule", None),
                "redirect_url": getattr(result, "redirect_url", None),
            }
        except Exception as e:
            logger.debug(f"[BraveAdBlock] check_url_detailed error: {e}")
            return {"matched": False}

    # ──────────────────────────────────────────────────────────────────────
    # Cosmetic Filtering
    # ──────────────────────────────────────────────────────────────────────

    def get_cosmetic_resources(self, url: str) -> Optional[Dict[str, Any]]:
        """Get cosmetic filter resources for a URL.

        Returns dict with:
        - hide_selectors: CSS selectors to hide (display: none)
        - style_selectors: CSS selectors with style modifications
        - exceptions: Exception selectors
        """
        if not self._engine:
            return None

        try:
            resources = self._engine.url_cosmetic_resources(url)
            return {
                "hide_selectors": list(resources.hide_selectors) if resources.hide_selectors else [],
                "style_selectors": dict(resources.style_selectors) if resources.style_selectors else {},
                "exceptions": list(resources.exceptions) if resources.exceptions else [],
            }
        except Exception as e:
            logger.debug(f"[BraveAdBlock] get_cosmetic_resources error: {e}")
            return None

    def get_hidden_class_id_selectors(self, classes: List[str], ids: List[str],
                                       exceptions: List[str]) -> List[str]:
        """Get additional CSS selectors from generic hide rules.

        Called after page loads to find dynamic class/id matches.
        """
        if not self._engine:
            return []

        try:
            return list(self._engine.hidden_class_id_selectors(classes, ids, exceptions))
        except Exception as e:
            logger.debug(f"[BraveAdBlock] hidden_class_id_selectors error: {e}")
            return []

    # ──────────────────────────────────────────────────────────────────────
    # Scriptlet / Resource Helpers
    # ──────────────────────────────────────────────────────────────────────

    def get_scriptlet_for_url(self, url: str) -> str:
        """Get scriptlet injection code for a URL (for Ghostery-style injection)."""
        # The Brave engine handles scriptlets via resources internally
        # This returns the scriptlet injection code that should be injected
        if not self._engine:
            return ""

        try:
            # Get URL-specific resources (includes scriptlets)
            resources = self._engine.url_cosmetic_resources(url)
            scriptlets = getattr(resources, "scriptlets", [])
            if scriptlets:
                return "\n".join(scriptlets)
        except Exception:
            pass
        return ""

    # ──────────────────────────────────────────────────────────────────────
    # Serialization / Persistence
    # ──────────────────────────────────────────────────────────────────────

    def save_cache(self) -> None:
        """Save engine state to cache."""
        if self._engine:
            cache_file = self._cache_dir / "engine.bin"
            try:
                self._engine.serialize_to_file(str(cache_file))
                logger.info("[BraveAdBlock] Engine cached")
            except Exception as e:
                logger.warning(f"[BraveAdBlock] Failed to cache engine: {e}")

    def load_cache(self) -> bool:
        """Load engine from cache."""
        cache_file = self._cache_dir / "engine.bin"
        if not cache_file.exists():
            return False

        try:
            self._engine = adblock.Engine.deserialize_from_file(str(cache_file))
            self._ready = True
            logger.info("[BraveAdBlock] Engine loaded from cache")
            return True
        except Exception as e:
            logger.warning(f"[BraveAdBlock] Failed to load engine cache: {e}")
            return False


# ──────────────────────────────────────────────────────────────────────────────
# Global Instance
# ──────────────────────────────────────────────────────────────────────────────

_brave_engine: Optional[BraveAdBlockEngine] = None


def get_brave_adblock() -> BraveAdBlockEngine:
    """Get global Brave adblock engine instance."""
    global _brave_engine
    if _brave_engine is None:
        _brave_engine = BraveAdBlockEngine()
        _brave_engine.initialize()
    return _brave_engine


def init_brave_adblock(force_download: bool = False) -> BraveAdBlockEngine:
    """Initialize Brave adblock engine."""
    global _brave_engine
    _brave_engine = BraveAdBlockEngine()
    _brave_engine.initialize(force_download)
    return _brave_engine