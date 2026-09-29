"""Tampermonkey-style script injection for Quillon AdBlocker.

Uses QWebEngineScript with Greasemonkey metadata (@match, @exclude, @run-at)
for declarative per-site injection - exactly like Tampermonkey/Greasemonkey.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import (
    QWebEngineScript,
    QWebEngineScriptCollection,
    QWebEngineProfile,
)

# Diagnostic log - survives desktop-entry launches (no stdout available there)
DIAG_LOG = Path.home() / ".local" / "share" / "quillon" / "diag.log"


def log_diag(msg: str) -> None:
    try:
        DIAG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(DIAG_LOG, "a") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Script Templates with Greasemonkey Metadata
# ──────────────────────────────────────────────────────────────────────────────

# YouTube Nuclear AdBlock Scriptlet (runs at document-start for max effectiveness)
YOUTUBE_NUCLEAR_SCRIPT = r"""// ==UserScript==
// @name         Quillon YouTube Nuclear AdBlock
// @namespace    https://quillon.browser
// @version      1.0.0
// @description  Nuclear YouTube ad blocker - kills ads at source
// @author       Quillon
// @match        *://www.youtube.com/*
// @match        *://youtube.com/*
// @match        *://m.youtube.com/*
// @match        *://music.youtube.com/*
// @exclude      *://www.youtube.com/embed/*
// @exclude      *://www.youtube.com/live_chat*
// @run-at       document-start
// @grant        none
// ==/UserScript==

(function() {
    "use strict";

    // Hostname guard - makes script safe even if @match metadata is ignored/skipped by the engine
    var _h = location.hostname;
    if (_h !== "www.youtube.com" && _h !== "youtube.com" && _h !== "m.youtube.com" && _h !== "music.youtube.com") return;

    // Injection guard (hidden flag) - prevent double-injection on SPA navigation.
    // Non-enumerable: invisible to for-in / Object.keys / JSON.stringify.
    if (window.__quillonNuclearInjected) return;
    try {
        Object.defineProperty(window, "__quillonNuclearInjected",
            { value: true, enumerable: false, writable: false, configurable: false });
    } catch (e) { try { window.__quillonNuclearInjected = true; } catch (e2) {} }

    // STEALTH: mask Function.prototype.toString so every hooked native
    // reports "function X() { [native code] }" - defeats function-integrity
    // detection (String(fetch), String(JSON.parse), etc).
    var __quillonOrigToString = Function.prototype.toString;
    var __quillonSpoofed = new WeakMap();
    var __quillonSpoof = function(fn, name) {
        try { __quillonSpoofed.set(fn, "function " + name + "() { [native code] }"); } catch (e) {}
    };
    Function.prototype.toString = function() {
        try {
            if (__quillonSpoofed.has(this)) { return __quillonSpoofed.get(this); }
        } catch (e) {}
        return __quillonOrigToString.apply(this, arguments);
    };
    __quillonSpoof(Function.prototype.toString, "toString");

    // AD DOMAIN BLOCKLIST
    var AD_DOMAINS = [
        "doubleclick.net", "googlesyndication.com", "googleadservices.com",
        "adservice.google.com", "adnxs.com", "criteo.com", "criteo.net",
        "taboola.com", "outbrain.com", "rubiconproject.com", "casalemedia.com",
        "adform.net", "yieldmo.com", "sharethrough.com", "spotxchange.com",
        "teads.tv", "undertone.com", "smartadserver.com", "zedo.com",
        "popads.net", "adroll.com", "bidswitch.net", "openx.net", "w55c.net",
        "amazon-adsystem.com", "pangleglobal.com", "ironsource.mobi",
        "mads-eu.amazon.com", "advertising-api-eu.amazon.com",
        "udc.yahoo.com", "udcm.yahoo.com", "log.fc.yahoo.com",
        "metrika.yandex.ru", "appmetrica.yandex.ru",
        "redirector.googlevideo.com", "pangleglobal.com",
        "s.youtube.com", "pagead2.googlesyndication.com", "googleads.g.doubleclick.net",
        "securepubads.g.doubleclick.net", "pubads.g.doubleclick.net",
        "googletagservices.com", "googletagmanager.com",
        "connect.facebook.net", "staticxx.facebook.com"
    ];

    function isAdDomain(url) {
        try {
            var hostname = new URL(url).hostname;
            return AD_DOMAINS.some(function(domain) {
                return hostname === domain || hostname.endsWith("." + domain);
            });
        } catch (e) { return false; }
    }

    // HOOK FETCH
    var originalFetch = window.fetch;
    window.fetch = function(input, init) {
        try {
            if (window.__quillonAdblockDisabled) {
                return originalFetch.apply(this, arguments);
            }
            var url = typeof input === "string" ? input : (input && input.url) || "";
            if (isAdDomain(url)) {
                return Promise.resolve(new Response("", {status: 204, statusText: "No Content"}));
            }
        } catch(e) {}
        return originalFetch.apply(this, arguments);
    };
    __quillonSpoof(window.fetch, "fetch");

    // HOOK XHR
    var originalXHROpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {
        try {
            Object.defineProperty(this, "_quillonUrl",
                { value: url || "", enumerable: false, writable: true, configurable: true });
        } catch (e) { try { this._quillonUrl = url || ""; } catch (e2) {} }
        return originalXHROpen.apply(this, arguments);
    };
    __quillonSpoof(XMLHttpRequest.prototype.open, "open");
    var originalXHRSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function(data) {
        try {
            if (!window.__quillonAdblockDisabled && this._quillonUrl && isAdDomain(this._quillonUrl)) {
                this.abort();
                return;
            }
        } catch(e) {}
        return originalXHRSend.apply(this, arguments);
    };
    __quillonSpoof(XMLHttpRequest.prototype.send, "send");

    // HOOK WEBSOCKET
    var originalWebSocket = window.WebSocket;
    window.WebSocket = function(url, protocols) {
        if (!window.__quillonAdblockDisabled && isAdDomain(url)) {
            var dummy = {
                close: function() {},
                send: function() {},
                addEventListener: function() {},
                removeEventListener: function() {},
                readyState: 3,
                CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3
            };
            setTimeout(function() {
                if (dummy.onclose) dummy.onclose({code: 1000, reason: ""});
                if (dummy.onerror) dummy.onerror(new Event("error"));
            }, 0);
            return dummy;
        }
        return new originalWebSocket(url, protocols);
    };
    __quillonSpoof(window.WebSocket, "WebSocket");

    // YOUTUBE-SPECIFIC: CLEAN PLAYER RESPONSES
    var AD_PATH_PATTERNS = [
        "/api/stats/ads", "/api/stats/qoe", "/api/stats/watchtime", "/api/stats/playback",
        "/api/stats/heartbeat", "/api/stats/logger", "/api/stats/qual",
        "/pagead/", "/ptracking", "/get_midroll_info", "/get_midroll", "/pcs/activeview",
        "/youtubei/v1/log_event", "/youtubei/v1/player/ad_break", "/youtubei/v1/player/get_video_info",
        "/youtubei/v1/player/get_playback_data", "/youtubei/v1/player/get_watch_next_response",
        "/youtubei/v1/player/get_playlist", "/youtubei/v1/player/get_next_video",
        "/youtubei/v1/updated_metadata", "/get_video_info", "/ad_companion", "/pagead/adview",
        "/youtubei/v1/player/release", "/youtubei/v1/next", "/youtubei/v1/search"
    ];

    function isAdUrl(url) {
        if (!url) return false;
        for (var i = 0; i < AD_PATH_PATTERNS.length; i++) {
            if (url.indexOf(AD_PATH_PATTERNS[i]) !== -1) return true;
        }
        return false;
    }

    function isPlayerEndpoint(url) {
        if (!url) return false;
        return url.indexOf("/youtubei/v1/player") !== -1 ||
               url.indexOf("/youtubei/v1/next") !== -1 ||
               url.indexOf("/get_video_info") !== -1;
    }

    function cleanAdPayload(obj) {
        if (!obj || typeof obj !== "object") return;
        var adKeys = ["adPlacements", "adSlots", "adSchedules", "adBreaks", "adSignalsInfo",
                       "playerAds", "instreamAds", "bumperAds", "midrolls", "prerolls",
                       "postrolls", "adTagUrl", "adTagUrlList", "adParameters",
                       "adSlotRenderer", "promotedSparklesWebRenderer", "infeedAdRenderer",
                       "displayAdRenderer", "videoMastheadAdRenderer", "mealbarPromoRenderer",
                       "bannerPromoRenderer", "adModule"];
        for (var i = 0; i < adKeys.length; i++) { if (adKeys[i] in obj) delete obj[adKeys[i]]; }
        if ("adSignalsInfo" in obj) obj.adSignalsInfo = {};
        if ("playbackTracking" in obj) {
            var pt = obj.playbackTracking;
            if (pt && pt.trackingUrls) pt.trackingUrls = pt.trackingUrls.filter(function(u) { return !isAdUrl(u); });
        }
        if ("streamingData" in obj && obj.streamingData.adaptiveFormats) {
            obj.streamingData.adaptiveFormats = obj.streamingData.adaptiveFormats.filter(
                function(f) { return !(f.mimeType && f.mimeType.indexOf("ad") !== -1) && !(f.url && f.url.indexOf("/pagead/") !== -1); }
            );
        }
        for (var key in obj) {
            if (typeof obj[key] === "object" && obj[key] !== null) cleanAdPayload(obj[key]);
        }
    }

    function cleanJsonResponse(response) {
        return response.clone().json().then(function(data) {
            cleanAdPayload(data);
            var headers = new Headers();
            response.headers.forEach(function(v, k) {
                var lk = (k || "").toLowerCase();
                if (lk !== "content-length" && lk !== "content-encoding") { headers.set(k, v); }
            });
            return new Response(JSON.stringify(data), {
                status: response.status, statusText: response.statusText, headers: headers
            });
        }).catch(function() { return response; });
    }

    // Hook fetch for player endpoint cleaning
    var origFetch2 = window.fetch;
    window.fetch = function(input, init) {
        var url = typeof input === "string" ? input : (input && input.url) || "";
        var promise = origFetch2.apply(this, arguments);
        if (isPlayerEndpoint(url) && !window.__quillonAdblockDisabled) {
            return promise.then(cleanJsonResponse);
        }
        return promise;
    };
    __quillonSpoof(window.fetch, "fetch");

    // JSON.PARSE HOOK - catches player/ad responses regardless of transport
    // (fetch, XHR, anything that parses JSON). This is what kills in-video
    // ads served via XHR youtubei calls that the fetch hook never sees.
    // The gate searches the RAW TEXT (native indexOf, fast) instead of only
    // checking top-level keys - ad state nested deeper inside feed/next
    // structures previously slipped through to the player, which then
    // showed the ad container before failing and skipping (the flash).
    var originalJSONParse = JSON.parse;
    JSON.parse = function(text, reviver) {
        var data = originalJSONParse.apply(this, arguments);
        try {
            if (window.__quillonAdblockDisabled) { return data; }
            if (data && typeof data === "object" && typeof text === "string" &&
                (text.indexOf('"adPlacements"') !== -1 ||
                 text.indexOf('"adSlots"') !== -1 ||
                 text.indexOf('"playerAds"') !== -1 ||
                 text.indexOf('"adSignalsInfo"') !== -1 ||
                 text.indexOf('"adBreaks"') !== -1 ||
                 text.indexOf('"adSlotRenderer"') !== -1)) {
                cleanAdPayload(data);
            }
        } catch (e) {}
        return data;
    };
    __quillonSpoof(JSON.parse, "parse");

    // PROPERTY TRAPS - intercept YouTube's inline player/data assignment.
    // ytInitialPlayerResponse is set by an inline <script> in the HTML and
    // never passes through fetch/XHR - this trap strips ads at assignment.
    try {
        var _ytPR = undefined;
        Object.defineProperty(window, "ytInitialPlayerResponse", {
            get: function() { return _ytPR; },
            set: function(v) {
                try {
                    if (!window.__quillonAdblockDisabled) { cleanAdPayload(v); }
                } catch (e) {}
                _ytPR = v;
            },
            configurable: true
        });
    } catch (e) {}
    try {
        var _ytData = undefined;
        Object.defineProperty(window, "ytInitialData", {
            get: function() { return _ytData; },
            set: function(v) {
                try {
                    if (!window.__quillonAdblockDisabled && v && typeof v === "object") { cleanAdPayload(v); }
                } catch (e) {}
                _ytData = v;
            },
            configurable: true
        });
    } catch (e) {}

    // CSS COSMETIC INJECTION
    if (!window.__quillonCosmeticInjected) {
        try {
            Object.defineProperty(window, "__quillonCosmeticInjected",
                { value: true, enumerable: false, writable: false, configurable: false });
        } catch (e) { try { window.__quillonCosmeticInjected = true; } catch (e2) {} }
        var __quillonInjectCosmeticCSS = function(css) {
            if (!css || !css.trim()) return;
            var style = document.createElement("style");
            style.id = "quillon-cosmetic-style";
            style.textContent = css;
            var root = document.head || document.documentElement;
            if (root) { root.appendChild(style); }
            else {
                var observer = new MutationObserver(function() {
                    var r = document.head || document.documentElement;
                    if (r) { observer.disconnect(); r.appendChild(style); }
                });
                observer.observe(document, { childList: true, subtree: true });
            }
        };
        try {
            Object.defineProperty(window, "__quillonInjectCosmeticCSS",
                { value: __quillonInjectCosmeticCSS, enumerable: false, writable: false, configurable: false });
            Object.defineProperty(window, "__quillonCosmeticReady",
                { value: true, enumerable: false, writable: false, configurable: false });
        } catch (e) {}
    }

    // CONTINUOUS AD REMOVAL MUTATION OBSERVER
    if (!window.__quillonAdObserverActive) {
        try {
            Object.defineProperty(window, "__quillonAdObserverActive",
                { value: true, enumerable: false, writable: false, configurable: false });
        } catch (e) { try { window.__quillonAdObserverActive = true; } catch (e2) {} }
        var adObserver = new MutationObserver(function(mutations) {
            if (window.__quillonAdblockDisabled) { return; }
            mutations.forEach(function(mut) {
                mut.addedNodes.forEach(function(node) {
                    if (node.nodeType === 1) {
                        var selectors = [
                            '.ytd-ad-slot-renderer', '.ytd-display-ad-renderer', '.ytd-promoted-video-renderer',
                            '.ytd-compact-promoted-video-renderer', '.video-ads', '.ad-slot', '.ad-container',
                            '.ytp-ad-module', '.ytp-ad-overlay-container', '.ytp-ad-player-overlay',
                            '.ytd-video-masthead-ad-renderer', '.ytd-rich-section-renderer:has(.ytd-ad-slot-renderer)'
                        ];
                        selectors.forEach(function(sel) {
                            try {
                                var matches = node.matches ? [node].filter(function(n){ return n.matches && n.matches(sel); }) : [];
                                if (node.querySelectorAll && node.querySelectorAll) {
                                    var inside = node.querySelectorAll ? node.querySelectorAll(sel) : [];
                                    inside.forEach(function(el) { if (el && el.parentNode) el.parentNode.removeChild(el); });
                                }
                            } catch(e) {}
                        });
                        try {
                            if (node.matches && node.matches('.ytd-ad-slot-renderer, .video-ads, .ad-slot, .ytp-ad-module')) {
                                if (node.parentNode) node.parentNode.removeChild(node);
                            }
                        } catch(e) {}
                        try {
                            if (node.classList && (node.classList.contains('ytd-ad-slot-renderer') || node.classList.contains('video-ads') || node.classList.contains('ad-slot') || node.classList.contains('ytp-ad-module')) && node.parentNode) {
                                node.parentNode.removeChild(node);
                            }
                        } catch(e) {}
                    }
                });
            });
        });
        adObserver.observe(document, { childList: true, subtree: true });
    }

    // Inject aggressive YouTube ad-slot hiding immediately
    var ytCosmeticCSS =
        "ytd-ad-slot-renderer, ytd-promoted-sparkles-web-renderer," +
        "ytd-display-ad-renderer, ytd-infeed-ad-renderer," +
        "ytd-rich-item-renderer:has(ytd-ad-slot-renderer), ytd-video-renderer:has(ytd-ad-slot-renderer)," +
        "ytd-compact-promoted-video-renderer, ytd-promoted-video-renderer," +
        ".ytd-video-masthead-ad-renderer, .ytd-ad-slot-renderer, .ad-slot-renderer," +
        ".video-ads, .ad-container, [data-testid=\"ad-slot\"], [data-testid=\"ad-module\"]," +
        "ytd-mech-shelf-renderer, ytd-merch-shelf-renderer," +
        "#masthead-ad, #player-ads, .video-ads, .ad-slot," +
        "[data-ad-slot], [data-ad-client], [data-ad-name], [data-ad-break]," +
        "ins.adsbygoogle, ins.adsbygoogle iframe, .adsbygoogle," +
        ".ytp-ad-module, .ytp-ad-player-overlay, .ytp-ad-player-overlay-layout," +
        ".ytp-ad-player-overlay-flyout-cta, .ytp-ad-player-overlay-instream-info," +
        ".ytp-ad-overlay-container, .ytp-ad-image-overlay, .ytp-ad-text-overlay," +
        ".ytp-ad-text-container, .ytp-ad-action-interactor, .ytp-ad-surface," +
        ".ytp-ad-message-container, .ytp-ad-skip-button-container," +
        ".ytp-ad-skip-button-modern, .ytp-ad-preview-container, .ytp-ad-preview-section," +
        ".ytp-ad-bumper-progress, .ytp-ad-progress-bar, .ytp-ad-progress," +
        "[class*=\"ytp-ad-player-overlay\"], [class*=\"ytp-ad-module\"]," +
        ".ad-container, .ytd-ad-slot-renderer, .ytd-display-ad-renderer, .ytd-rich-section-renderer:has(.ytd-ad-slot-renderer)" +
        " {display: none !important; visibility: hidden !important; opacity: 0 !important;" +
        " height: 0 !important; width: 0 !important; pointer-events: none !important; overflow: hidden !important;}";
    try {
        Object.defineProperty(window, "__quillonYTAdCss",
            { value: ytCosmeticCSS, enumerable: false, writable: false, configurable: false });
    } catch (e) {}
    if (window.__quillonInjectCosmeticCSS && !window.__quillonAdblockDisabled) {
        window.__quillonInjectCosmeticCSS(ytCosmeticCSS);
    }
})();"""

# Global Cosmetic Filters Scriptlet (runs on all sites except YouTube)
GLOBAL_COSMETIC_SCRIPT = r"""// ==UserScript==
// @name         Quillon Global Cosmetic Filters
// @namespace    https://quillon.browser
// @version      1.0.0
// @description  Global cosmetic ad/hiding filters
// @author       Quillon
// @match        *://*/*
// @exclude      *://www.youtube.com/*
// @exclude      *://youtube.com/*
// @exclude      *://m.youtube.com/*
// @exclude      *://music.youtube.com/*
// @run-at       document-start
// @grant        none
// @world        main
// ==/UserScript==

(function() {
    'use strict';

    if (!window.__quillonCosmeticInjected) {
        try {
            Object.defineProperty(window, '__quillonCosmeticInjected',
                { value: true, enumerable: false, writable: false, configurable: false });
        } catch (e) { try { window.__quillonCosmeticInjected = true; } catch (e2) {} }
        var __quillonInjectCosmeticCSS = function(css) {
            if (!css || !css.trim()) return;
            const style = document.createElement('style');
            style.textContent = css;
            const root = document.head || document.documentElement;
            if (root) { root.appendChild(style); }
            else {
                const observer = new MutationObserver(function() {
                    const r = document.head || document.documentElement;
                    if (r) { observer.disconnect(); r.appendChild(style); }
                });
                observer.observe(document, { childList: true, subtree: true });
            }
        };
        try {
            Object.defineProperty(window, '__quillonInjectCosmeticCSS',
                { value: __quillonInjectCosmeticCSS, enumerable: false, writable: false, configurable: false });
            Object.defineProperty(window, '__quillonCosmeticReady',
                { value: true, enumerable: false, writable: false, configurable: false });
        } catch (e) {}
    }
})();"""


# ──────────────────────────────────────────────────────────────────────────────
# Ghostery Scriptlet Wrapper (injected per-URL via Ghostery)
# ──────────────────────────────────────────────────────────────────────────────

GHOSTERY_SCRIPTLET_TEMPLATE = r"""
// ==UserScript==
// @name         Quillon Ghostery Scriptlet: {name}
// @namespace    https://quillon.browser
// @version      1.0.0
// @description  Ghostery anti-adblock defuser / popup closer
// @author       Quillon + Ghostery
// @match        {match}
// @run-at       document-start
// @grant        none
// ==/UserScript==

{script}
"""


# ──────────────────────────────────────────────────────────────────────────────
# Script Manager
# ──────────────────────────────────────────────────────────────────────────────

class TampermonkeyScriptManager:
    """Manages Tampermonkey-style script injection using QWebEngineScript."""

    def __init__(self, profile: Optional[QWebEngineProfile] = None):
        self._profile = profile
        self._scripts = {}
        self._page_scripts = {}  # page_id -> script names

    def set_profile(self, profile: QWebEngineProfile) -> None:
        """Set the profile for global script registration."""
        self._profile = profile

    def register_global_scripts(self) -> None:
        """Register scripts that apply globally (to all pages in profile)."""
        if not self._profile:
            return

        collection = self._profile.scripts()

        # Global cosmetic script (all sites except YouTube)
        self._add_script_from_source(
            collection, GLOBAL_COSMETIC_SCRIPT, "quillon_global_cosmetic"
        )

        # YouTube nuclear script - register globally so it injects on all navigations
        # The script itself checks hostname and returns early for non-YouTube pages
        self._add_script_from_source(
            collection, YOUTUBE_NUCLEAR_SCRIPT, "quillon_youtube_nuclear"
        )

    def set_adblock_enabled(self, enabled: bool) -> None:
        """Toggle global script injection at runtime (no browser restart)."""
        if not self._profile:
            return
        collection = self._profile.scripts()
        for name in ("quillon_global_cosmetic", "quillon_youtube_nuclear"):
            for existing in collection.find(name):
                collection.remove(existing)
        if enabled:
            self.register_global_scripts()

    def set_page_adblock_enabled(self, page, enabled: bool) -> None:
        """Toggle page-level script injection for one open page."""
        if page is None:
            return
        try:
            collection = page.scripts()
            for existing in collection.find("quillon_youtube_nuclear_page"):
                collection.remove(existing)
            if enabled:
                self._add_script_from_source(
                    page.scripts(), YOUTUBE_NUCLEAR_SCRIPT, "quillon_youtube_nuclear_page"
                )
        except Exception:
            pass

    def register_page_scripts(self, page) -> None:
        """Fallback path: inject nuclear script directly into the page's own
        script collection. Belt-and-suspenders alongside profile-level
        registration - survives @match metadata quirks. The script's
        hostname guard makes this safe on non-YouTube pages."""
        if page is None:
            return
        try:
            self._add_script_from_source(
                page.scripts(), YOUTUBE_NUCLEAR_SCRIPT, "quillon_youtube_nuclear_page"
            )
        except Exception as e:
            print(f"[Tampermonkey] page-level injection failed: {e}")

    def inject_ghostery_scriptlet(self, page, url: str, script_content: str) -> None:
        """Inject a Ghostery scriptlet for a specific URL."""
        collection = page.scripts()
        script_name = f"quillon_ghostery_{abs(hash(script_content)) % 100000}"

        # Create script with @match for this specific URL
        script_source = GHOSTERY_SCRIPTLET_TEMPLATE.format(
            name=f"ghostery_{script_name}",
            match=url,
            script=script_content
        )

        self._add_script_from_source(collection, script_source, script_name)

    def _add_script_from_source(self, collection: QWebEngineScriptCollection,
                                 source: str, name: str) -> None:
        """Add a script with Greasemonkey metadata to a collection."""
        # Remove existing script with same name
        for existing in collection.find(name):
            collection.remove(existing)

        script = QWebEngineScript()
        script.setName(name)
        script.setSourceCode(source)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        collection.insert(script)


# ──────────────────────────────────────────────────────────────────────────────
# Convenience Functions
# ──────────────────────────────────────────────────────────────────────────────

_global_manager: Optional[TampermonkeyScriptManager] = None


def get_script_manager() -> TampermonkeyScriptManager:
    """Get the global script manager instance."""
    global _global_manager
    if _global_manager is None:
        _global_manager = TampermonkeyScriptManager()
    return _global_manager


def setup_tampermonkey_scripts(profile: QWebEngineProfile) -> TampermonkeyScriptManager:
    """Initialize and register global scripts on profile."""
    manager = get_script_manager()
    manager.set_profile(profile)
    manager.register_global_scripts()
    return manager


def inject_page_scripts(page) -> None:
    """Inject page-specific scripts (YouTube nuclear, etc.)."""
    manager = get_script_manager()
    manager.register_page_scripts(page)


def inject_ghostery_scriptlet(page, url: str, script: str) -> None:
    """Inject a Ghostery scriptlet for a specific URL."""
    manager = get_script_manager()
    manager.inject_ghostery_scriptlet(page, url, script)
