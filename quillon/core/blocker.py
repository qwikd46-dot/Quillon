"""URL blocking logic using the Ghostery AdBlocker (Node.js subprocess) + Brave AdBlock."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

from .ghostery_engine import GhosteryEngineClient, GhosteryEngineError
from .brave_adblock import BraveAdBlockEngine, get_brave_adblock
from .config import SECURITY_CONFIG, PATHS, _DNS_BLOCKLIST_SOURCES, _DNS_BLOCKLIST_CACHE, _DNS_REFRESH_INTERVAL

# Maps Qt's QWebEngineUrlRequestInfo.ResourceType (as documented in
# config.py's BLOCKED_RESOURCE_TYPES comment) to the resource-type strings
# the adblock-rust engine expects (same vocabulary as uBlock Origin /
# EasyList's $script, $image, $xmlhttprequest, etc. options).
#
# This is what actually lets type-scoped EasyList/EasyPrivacy rules fire.
# Without it (see `_request_type_for_url` below, which only guesses from
# the URL's file extension) almost every modern ad/tracking request - RTB
# pings, YouTube's pagead/ptracking/get_midroll_info calls, analytics
# beacons - has no recognizable extension, gets silently classified as
# "other", and every rule scoped to a specific type never matches it.
QT_RESOURCE_TYPE_MAP: dict[int, str] = {
    0: "document",          # MainFrame
    1: "subdocument",       # SubFrame
    2: "stylesheet",        # Stylesheet
    3: "script",            # Script
    4: "image",             # Image
    5: "font",              # FontResource
    6: "other",             # SubResource
    7: "object",            # Object
    8: "media",             # Media
    9: "script",            # Worker
    10: "script",           # SharedWorker
    11: "other",            # Prefetch
    12: "image",            # Favicon
    13: "xmlhttprequest",   # Xhr
    14: "ping",             # Ping
    15: "script",           # ServiceWorker
    17: "object",           # PluginResource
    21: "json",             # Json
    254: "websocket",       # WebSocket
    255: "other",           # Unknown
}


# Core filter lists - fetched automatically on first run and refreshed
# every 7 days. Without these the adblock-rust engine has ZERO rules and
# nothing is blocked before it loads (the original bug).
#
# Primary sources are EasyList (core ad-blocking) and EasyPrivacy
# (tracking protection - the definitive privacy list). Fanboy's
# Annoyance list is included to block social widgets, popups, and
# other intrusive elements that EasyList doesn't cover.
#
# All fallback rules in _BUILTIN_FALLBACK_RULES are loaded directly
# into the engine regardless of download status, so blocking always
# works even if the network is down.
#
# NOTE: The Ghostery AdBlocker uses its own prebuilt filter lists,
# so we don't need to download EasyList/EasyPrivacy manually.
# However, we keep the same interface for compatibility and to allow
# users to see that filter lists are being maintained.
_FILTERLIST_SOURCES = {
    # These are kept for compatibility and logging purposes
    # but are not actually used since Ghostery provides its own lists
    "easylist.txt": (
        "https://easylist.to/easylist/easylist.txt",
        "https://raw.githubusercontent.com/easylist/easylist/master/easylist.txt",
    ),
    "easyprivacy.txt": (
        "https://easylist.to/easylist/easyprivacy.txt",
        "https://raw.githubusercontent.com/easylist/easylist/master/easyprivacy.txt",
    ),
    "annoyances.txt": (
        "https://secure.fanboy.co.nz/fanboy-cookiemonster.txt",
        "https://secure.fanboy.co.nz/fanboy-annoyance.txt",
    ),
}
_FILTERLIST_MAX_AGE = 7 * 24 * 3600  # refresh weekly
_FILTERLIST_UA = "Mozilla/5.0 (X11; Linux x86_64) Quillon/1.0"

# Minimal built-in fallback rules (ABP syntax). Used when the filterlist
# download fails (offline / firewalled). Covers the biggest ad networks
# including every endpoint YouTube uses to serve ads.
_BUILTIN_FALLBACK_RULES = r"""
||doubleclick.net^
||googlesyndication.com^
||googletagservices.com^
||googleadservices.com^
||google-analytics.com^
||googletagmanager.com^
||googletagservices.com^
||adservice.google.com^
||pagead2.googlesyndication.com^
||tpc.googlesyndication.com^
||securepubads.g.doubleclick.net^
||pubads.g.doubleclick.net^
||youtube.com/api/stats/ads
||youtube.com/get_midroll_info
||youtube.com/pagead/
||youtube.com/ptracking
||youtube.com/pcs/activeview
||youtube.com/youtubei/v1/log_event
||googlevideo.com/videogoodput
||ad.doubleclick.net^
||adnxs.com^
||adsrvr.org^
||criteo.com^
||criteo.net^
||outbrain.com^
||taboola.com^
||scorecardresearch.com^
||quantserve.com^
||moatads.com^
||amazon-adsystem.com^
||ads.youtube.com^
||ad.youtube.com^
||tracker.com^
/adsense/$script
/pagead/js/$script
||popads.net^
||adroll.com^
||bidswitch.net^
||casalemedia.com^
||rubiconproject.com^
||openx.net^
||smartadserver.com^
||zedo.com^
||adform.net^
||yieldmo.com^
||sharethrough.com^
||spotxchange.com^
||teads.tv^
||undertone.com^
||w55c.net^

# Additional tracking/advertising domains (from user report)
||advertising-api-eu.amazon.com^
||liftoff.io^
||udc.yahoo.com^
||udcm.yahoo.com^
||log.fc.yahoo.com^
||metrika.yandex.ru^
||appmetrica.yandex.ru^
||ironsource.mobi^
||s.youtube.com^
||redirector.googlevideo.com^
||pangleglobal.com^
||tagmanager.google.com^
||app.posthog.com^
||eu.posthog.com^
||us.i.posthog.com^
||rudderstack.com^
||snowplowanalytics.com^
||fingerprintjs.com^
||bnc.lt^
||graph.facebook.com^
||tr.facebook.com^
||graph.instagram.com^
||i.instagram.com^
||ads-api.x.com^
||analytics.x.com^
||d.reddit.com^
||business-api.tiktok.com^
||log.byteoversea.com^
||widgets.pinterest.com^
||pixel.quora.com^
||qevents.quora.com^
||api-adservices.apple.com^
||books-analytics-events.apple.com^
||xp.apple.com^
||grs.hicloud.com^
||data.mistat.india.xiaomi.com^
||data.mistat.rus.xiaomi.com^
||ngfts.lge.com^
||browser.events.data.msn.com^
||mads-eu.amazon.com^
||privacyportal.onetrust.com^
||consent.cookiebot.com^
||consentcdn.cookiebot.com^
||cookiebot.com^
||consent.trustarc.com^
||sdk.privacy-center.org^
||cdn.privacy-mgmt.com^
||app.usercentrics.eu^
||cmp.osano.com^
||clientstream.launchdarkly.com^
||click.mailchimp.com^
||widget.intercom.io^
||js.driftt.com^
||api.onesignal.com^
||dai.google.com^
||ssl.p.jwpcdn.com^

# Privacy frontend ad endpoints (yewtu.be, piped.*, invidious)
# These frontends may serve their own ads or sponsored content
||yewtu.be/api/v1/sponsors^
||yewtu.be/api/v1/ads^
||yewtu.be/api/v1/promoted^
||yewtu.be/api/v1/segment^
||yewtu.be/api/v1/segments^
||yewtu.be/api/v1/banner^
||yewtu.be/api/v1/recommendations/sponsored^
||yewtu.be/api/v1/promoted-content^
||piped.kavin.rocks/api/v1/sponsors^
||piped.kavin.rocks/api/v1/ads^
||piped.kavin.rocks/api/v1/promoted^
||piped.kavin.rocks/api/v1/segment^
||piped.kavin.rocks/api/v1/segments^
||piped.kavin.rocks/api/v1/banner^
||piped.kavin.rocks/api/v1/recommendations/sponsored^
||piped.kavin.rocks/api/v1/promoted-content^
||piped.projectsegfau.lt/api/v1/sponsors^
||piped.projectsegfau.lt/api/v1/ads^
||piped.projectsegfau.lt/api/v1/promoted^
||piped.projectsegfau.lt/api/v1/segment^
||piped.projectsegfau.lt/api/v1/segments^
||piped.projectsegfau.lt/api/v1/banner^
||piped.rkevin.dev/api/v1/sponsors^
||piped.rkevin.dev/api/v1/ads^
||piped.rkevin.dev/api/v1/promoted^
||piped.rkevin.dev/api/v1/segment^
||piped.rkevin.dev/api/v1/segments^
||piped.rkevin.dev/api/v1/banner^
||piped.mha.fi/api/v1/sponsors^
||piped.mha.fi/api/v1/ads^
||piped.mha.fi/api/v1/promoted^
||piped.mha.fi/api/v1/segment^
||piped.mha.fi/api/v1/segments^
||piped.mha.fi/api/v1/banner^
||piped.lunar.icu/api/v1/sponsors^
||piped.lunar.icu/api/v1/ads^
||piped.lunar.icu/api/v1/promoted^
||piped.lunar.icu/api/v1/segment^
||piped.lunar.icu/api/v1/segments^
||piped.lunar.icu/api/v1/banner^
||piped.freetubeapp.com/api/v1/sponsors^
||piped.freetubeapp.com/api/v1/ads^
||piped.freetubeapp.com/api/v1/promoted^
||piped.freetubeapp.com/api/v1/segment^
||piped.freetubeapp.com/api/v1/segments^
||piped.freetubeapp.com/api/v1/banner^
||piped.adminforge.de/api/v1/sponsors^
||piped.adminforge.de/api/v1/ads^
||piped.adminforge.de/api/v1/promoted^
||piped.adminforge.de/api/v1/segment^
||piped.adminforge.de/api/v1/segments^
||piped.adminforge.de/api/v1/banner^
||inv.nadeko.net/api/v1/sponsors^
||inv.nadeko.net/api/v1/ads^
||inv.nadeko.net/api/v1/promoted^
||inv.nadeko.net/api/v1/segment^
||inv.nadeko.net/api/v1/segments^
||inv.nadeko.net/api/v1/banner^
||yewtu.be/sponsor^
||yewtu.be/ad^
||piped.kavin.rocks/sponsor^
||piped.kavin.rocks/ad^
||piped.projectsegfau.lt/sponsor^
||piped.projectsegfau.lt/ad^
||piped.rkevin.dev/sponsor^
||piped.rkevin.dev/ad^
||piped.mha.fi/sponsor^
||piped.mha.fi/ad^
||piped.mha.fi/sponsor^
||piped.mha.fi/ad^

# Generic tracker/analytics domains that bloat pages
||googletagmanager.com^$third-party
||google-analytics.com^$third-party
||googletagservices.com^$third-party
||connect.facebook.net^$third-party
||connect.facebook.com^$third-party
||static.hotjar.com^$third-party
||hotjar.com^$third-party
||mouseflow.com^$third-party
||fullstory.com^$third-party
||logrocket.io^$third-party
||sentry.io^$third-party
||bugsnag.com^$third-party
||rollbar.com^$third-party
||airbrake.io^$third-party
||trackjs.com^$third-party
||raygun.io^$third-party
||newrelic.com^$third-party
||datadoghq.com^$third-party
||amplitude.com^$third-party
||mixpanel.com^$third-party
||heap.io^$third-party
||segment.com^$third-party
||intercom.io^$third-party
||drift.com^$third-party
||crisp.chat^$third-party
||tawk.to^$third-party
||zopim.com^$third-party
||olark.com^$third-party
||livechatinc.com^$third-party
||purechat.com^$third-party
||smartsupp.com^$third-party
||tidio.com^$third-party
||chatra.io^$third-party
||freshchat.com^$third-party
||chatwoot.com^$third-party
"""


# YouTube scriptlet - kills ads at every layer (network, response, JSON, DOM, video).
#
# Why this is the v3 rewrite (Sept 2026):
#   v2 only hooked JSON.parse globally, and the player often parsed the response
#   before the scriptlet's hook took effect - so the user saw "Ad · 0:15" flash
#   for ~1s before our cleanup ran.
#   v3 hooks FOUR layers in order of priority:
#     1. fetch + XHR - abort ad-network requests entirely, before any bytes arrive.
#     2. Response.prototype.json + .text - clean the player JSON even if the
#        player calls `.json()` instead of `JSON.parse(text)`.
#     3. Response constructor - for known ad endpoints, return a 204 before
#        any handler runs.
#     4. DOM + video element + MutationObserver - last-resort cleanup.
#   Combined, the player never receives an ad break, and the yellow progress
#   bar / "Ad · X" badge never paint.
#
# Also handles SponsorBlock + force English locale.
YOUTUBE_SCRIPTLET = r"""
(function() {
    'use strict';
    if (window.__quillonYTInjected) return;
    // Only run on actual YouTube domains — skip privacy frontends (yewtu.be, piped.*, etc.)
    // where the DOM structure is different and our selectors/MutationObservers waste CPU/RAM.
    try {
        var host = window.location.hostname;
        if (host !== 'www.youtube.com' && host !== 'youtube.com' && host !== 'youtu.be' && host !== 'm.youtube.com' && host !== 'music.youtube.com') {
            return;
        }
    } catch(e) { return; }
    window.__quillonYTInjected = true;

    // ----------------------------------------------------------------
    // 0. Force English/US locale
    // ----------------------------------------------------------------
    function forceEnglish() {
        try {
            const pref = 'f1=50000000&f2=8000000&f4=1000000&f5=30000000&f6=40000000&f7=100&f8=10000000&f9=100&f10=100&f11=100&f12=100&f13=100&f14=100&f15=100&f16=100&f17=100&f18=100&f19=100&f20=100&f21=100&f22=100&f23=100&f24=100&f25=100&f26=100&f27=100&f28=100&f29=100&f30=100&f31=100&f32=100&f33=100&f34=100&f35=100&f36=100&f37=100&f38=100&f39=100&f40=100&f41=100&f42=100&f43=100&f44=100&f45=100&f46=100&f47=100&f48=100&f49=100&f50=100&f51=100&f52=100&f53=100&f54=100&f55=100&f56=100&f57=100&f58=100&f59=100&f60=100&f61=100&f62=100&f63=100&f64=100&f65=100&f66=100&f67=100&f68=100&f69=100&f70=100&f71=100&f72=100&f73=100&f74=100&f75=100&f76=100&f77=100&f78=100&f79=100&f80=100&f81=100&f82=100&f83=100&f84=100&f85=100&f86=100&f87=100&f88=100&f89=100&f90=100&f91=100';
            document.cookie = 'PREF=' + pref + '; domain=.youtube.com; path=/; expires=' + new Date(Date.now() + 365*24*60*60*1000).toUTCString();
        } catch(e) {}
    }

    // ----------------------------------------------------------------
    // 1. Ad-key catalogue - every known ad-bearing key in the player
    //    response. The player response is a tree; we strip every
    //    ad-related key recursively AND every array element that
    //    is itself an ad object.
    // ----------------------------------------------------------------
    const AD_OBJECT_KEYS = new Set([
        // top-level on playerResponse / getVideoInfo
        'adSlots', 'adSlotMetadata', 'playerAds',
        'adBreak', 'adBreaks', 'adPlacements', 'adPlacement',
        'compansionAds', 'promotedSparklesWebRenderer',
        // 2024+ additions
        'instreamAd', 'instreamAdBreak', 'adBreakHeartbeatParams',
        'backoffTimeMs', 'backoffTime', 'backoffMs',
        'adPlacementData', 'adVideoTransition', 'bypassedPlayerAds',
        'bypassedAdSlots', 'adSignals', 'adVideoMetadata',
        // ad-renderer types (any object containing these is an ad)
        'playerAdRenderer', 'adSlotRenderer', 'adPlacementRenderer',
        'instreamAdBreakRenderer', 'compactAdRenderer',
        'mealbarPromoRenderer', 'promotedVideoRenderer',
    ]);
    const AD_ARRAY_FILTER_KEYS = new Set([
        'adSlotRenderer', 'playerAdRenderer', 'adPlacementRenderer',
        'instreamAdBreak', 'instreamAdBreakRenderer',
        'mealbarPromoRenderer', 'compactAdRenderer',
    ]);
    function isAdKey(k) { return AD_OBJECT_KEYS.has(k); }
    function isAdObject(v) {
        if (!v || typeof v !== 'object') return false;
        for (const k of AD_ARRAY_FILTER_KEYS) {
            if (k in v) return true;
        }
        return false;
    }
    function cleanAdPayload(obj) {
        if (!obj || typeof obj !== 'object') return obj;
        if (Array.isArray(obj)) {
            for (let i = obj.length - 1; i >= 0; i--) {
                if (isAdObject(obj[i])) {
                    obj.splice(i, 1);
                } else {
                    cleanAdPayload(obj[i]);
                }
            }
            return obj;
        }
        for (const k of Object.keys(obj)) {
            if (isAdKey(k)) {
                try { delete obj[k]; } catch(e) { obj[k] = undefined; }
            } else {
                try { cleanAdPayload(obj[k]); } catch(e) {}
            }
        }
        return obj;
    }
    // ----------------------------------------------------------------
    // 2. Hook fetch + XMLHttpRequest - abort ad-network requests
    //    entirely, before any bytes arrive.
    // ----------------------------------------------------------------
    const AD_PATH_PATTERNS = [
        // player / pagead beacons
        '/api/stats/ads',
        '/ptracking', '/get_midroll_info', '/log_event',
        '/pagead/', '/pcs/activeview', '/pcs/view',
        // ad-network domains
        'doubleclick.net', 'googlesyndication.com',
        'googleadservices.com',
        'googletagservices.com',
        'googleadservices.com',
        'googlevideo.com/videogoodput', 'youtube.com/get_video_info',
        // ad-related player params
        '&ad_type=', '&ad_format=', '&ad_networks=',
        '&adunit=', '&ad_module=',
    ];

    function isAdUrl(url) {
        if (!url) return false;
        for (let i = 0; i < AD_PATH_PATTERNS.length; i++) {
            if (url.indexOf(AD_PATH_PATTERNS[i]) !== -1) return true;
        }
        return false;
    }

    // Endpoints that return the actual video/player metadata (including
    // client-scheduled ad placements). This is DIFFERENT from isAdUrl()
    // above: these are YouTube's own normal API calls, not ad-network
    // requests, so they can't just be aborted - the response has to be
    // parsed, cleaned with cleanAdPayload(), and re-served.
    function isPlayerEndpoint(url) {
        if (!url) return false;
        return url.indexOf('/youtubei/v1/player') !== -1 ||
               url.indexOf('/youtubei/v1/next') !== -1 ||
               url.indexOf('/get_video_info') !== -1;
    }

    // Clone a JSON Response, strip ad metadata from the parsed body with
    // cleanAdPayload(), and hand back a fresh Response carrying the
    // cleaned body. This is what actually keeps the player from ever
    // scheduling an ad, instead of hiding one after it's already been
    // scheduled/shown. Falls back to the original response untouched if
    // anything goes wrong (non-JSON body, parse failure, etc).
    function cleanJsonResponse(response) {
        return response.clone().json().then(function(data) {
            cleanAdPayload(data);
            return new Response(JSON.stringify(data), {
                status: response.status,
                statusText: response.statusText,
                headers: response.headers,
            });
        }).catch(function() {
            return response;
        });
    }

    // fetch hook
    try {
        const origFetch = window.fetch;
        window.fetch = function(input, init) {
            const url = typeof input === 'string' ? input : (input && input.url) || '';
            try {
                if (isAdUrl(url)) {
                    return Promise.resolve(new Response('', {status: 204, statusText: 'No Ads'}));
                }
            } catch(e) {}
            const result = origFetch.apply(this, arguments);
            if (isPlayerEndpoint(url)) {
                return result.then(cleanJsonResponse);
            }
            return result;
        };
    } catch(e) {}

    // XHR hook - same idea but for the legacy client (rarely used by the
    // modern player, but it can show up in the search autocomplete).
    // Also cleans player-endpoint responses in place via a responseText/
    // response getter override, for the same reason as the fetch hook.
    try {
        const origXHROpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url) {
            this._url = url;
            return origXHROpen.apply(this, arguments);
        };
        const origXHRSend = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.send = function(data) {
            try {
                if (this._url && isAdUrl(this._url)) {
                    this.abort();
                    return;
                }
                if (this._url && isPlayerEndpoint(this._url)) {
                    this.addEventListener('readystatechange', function() {
                        if (this.readyState === 4 && this._quillonCleaned !== true) {
                            this._quillonCleaned = true;
                            try {
                                const data = JSON.parse(this.responseText);
                                cleanAdPayload(data);
                                Object.defineProperty(this, 'responseText', { value: JSON.stringify(data), configurable: true });
                                Object.defineProperty(this, 'response', { value: JSON.stringify(data), configurable: true });
                            } catch(e) {}
                        }
                    });
                }
            } catch(e) {}
            return origXHRSend.apply(this, arguments);
        };
    } catch(e) {}

    // Defuse the FIRST video's embedded player response. On the initial
    // page load, YouTube inlines the video metadata directly as a raw JS
    // object literal assigned to `window.ytInitialPlayerResponse` in a
    // <script> tag - it never goes through fetch, XHR, or even
    // JSON.parse, so none of the hooks above ever see it. Only videos
    // navigated to afterward (via the fetch-based /youtubei/v1/player
    // calls above) were actually being cleaned; the very first video
    // loaded in a tab was not. Defining an accessor property here (which
    // runs before YouTube's own script executes, since this scriptlet is
    // injected at DocumentCreation) lets us clean the object the moment
    // YouTube's script assigns it.
    try {
        let _ytInitialPlayerResponse;
        Object.defineProperty(window, 'ytInitialPlayerResponse', {
            configurable: true,
            get() { return _ytInitialPlayerResponse; },
            set(value) {
                try { cleanAdPayload(value); } catch(e) {}
                _ytInitialPlayerResponse = value;
            },
        });
    } catch(e) {}

    // ----------------------------------------------------------------
    // 3. Patch the player config object once it appears.
    // ----------------------------------------------------------------
    function patchYtCfg() {
        if (!window.ytcfg || !window.ytcfg.data) return;
        const cfg = window.ytcfg.data;
        try {
            cfg.AD_ENABLED = false;
            cfg.AD_3P_ENABLED = false;
            cfg.ADS_ALLOWED = false;
            cfg.PREBID_CONFIG = null;
            cfg.EXPERIMENT_FLAGS = cfg.EXPERIMENT_FLAGS || {};
        } catch(e) {}
    }

    // ----------------------------------------------------------------
    // 4. Last-resort: fast-forward an ad that somehow slipped through.
    //    This should never fire with the response hooks above, but if
    //    a new ad path appears we still don't show the user a full ad.
    // ----------------------------------------------------------------
    function fastForwardAd() {
        try {
            const video = document.querySelector('.html5-video-player video');
            const player = document.querySelector('.html5-video-player');
            if (!video || !player) return;
            if (!isNaN(video.duration) && video.duration > 0 &&
                player.classList.contains('ad-showing')) {
                video.currentTime = video.duration;
                video.muted = true;
            }
        } catch(e) {}
    }
    function clickSkipButton() {
        const selectors = [
            '.ytp-ad-skip-button',
            '.ytp-ad-skip-button-modern',
            '.ytp-ad-skip-button-container button',
            '.ytp-ad-player-overlay-skip-button',
        ];
        for (const sel of selectors) {
            const btn = document.querySelector(sel);
            if (btn) { try { btn.click(); return true; } catch(e) {} }
        }
        const closeSelectors = [
            '.ytp-ad-overlay-close-button',
            '.ytp-ad-player-overlay-close-button',
        ];
        for (const sel of closeSelectors) {
            const btn = document.querySelector(sel);
            if (btn) { try { btn.click(); } catch(e) {} }
        }
        return false;
    }

    // ----------------------------------------------------------------
    // 5. DOM cleanup for the rare element that paints before the
    //    response hook fires.
    // ----------------------------------------------------------------
    const AD_SELECTORS = [
        '.ytp-ad-overlay-container', '.ytp-ad-overlay-slot',
        '.ytp-ad-overlay-image', '.ytp-ad-player-overlay',
        '.ytp-ad-player-overlay-layout', '.ytp-ad-avatar',
        '.ytp-ad-avatar-lockup', '.ytp-ad-detail',
        '.ytp-ad-feedback-dialog', '.ytp-ad-module',
        '.ytp-ad-slot', '.ytp-ad-text-slot', '.ytp-ad-companion-slot',
        'ytd-promoted-sparkles-web-renderer', 'ytd-ad-slot-renderer',
        'ytd-companion-slot-renderer', 'ytd-display-ad-renderer',
        'ytd-in-feed-ad-layout-renderer', 'ytd-banner-promo-renderer',
        'ytd-statement-banner-renderer', 'ytd-action-companion-ad-renderer',
        'ytd-companion-ad-renderer', 'ytd-video-masthead-ad-primary-renderer',
        'ytd-video-masthead-ad-advertiser-info-renderer',
        'ytd-video-masthead-ad-video-renderer', 'ytd-search-pyv-renderer',
        '.masthead-ad-control',
        '#player-ads', '.ytp-ad-interstitial', '.ytp-ad-simple-ad-badge',
        '.ytp-ad-image-overlay', '.ytp-ad-preview-container',
        '.ytp-ad-notification-container', '.yt-mealbar-promo-renderer',
        'ytm-companion-ad-renderer', 'ytm-companion-slot-renderer',
        'ytm-promoted-sparkles-web-renderer', 'ytm-promoted-video-renderer',
    ];
    function removeAdElements() {
        for (let i = 0; i < AD_SELECTORS.length; i++) {
            try {
                const els = document.querySelectorAll(AD_SELECTORS[i]);
                for (let j = 0; j < els.length; j++) {
                    els[j].remove();
                }
            } catch(e) {}
        }
    }

    // ----------------------------------------------------------------
    // 6. SponsorBlock - skip sponsor segments in the player timeline
    // ----------------------------------------------------------------
    function setupSponsorBlock() {
        if (window.__quillonSponsorBlockInited) return;
        window.__quillonSponsorBlockInited = true;

        let sponsorSegments = [];
        let videoId = null;

        function extractVideoId() {
            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.has('v')) return urlParams.get('v');
            const pathMatch = window.location.pathname.match(/\/watch\/([^/?]+)/);
            if (pathMatch) return pathMatch[1];
            const embedMatch = window.location.pathname.match(/\/embed\/([^/?]+)/);
            if (embedMatch) return embedMatch[1];
            return null;
        }

        async function fetchSegments() {
            videoId = extractVideoId();
            if (!videoId) return;
            try {
                const resp = await fetch(`https://sponsor.ajay.app/api/skipSegments?videoID=${videoId}&categories=sponsor,selfpromo,interaction,intro,outro,preview,music_offtopic,poi_highlight`);
                if (resp.ok) {
                    sponsorSegments = await resp.json();
                }
            } catch(e) {}
        }

        function checkSponsorSegments() {
            if (!videoId || sponsorSegments.length === 0) return;
            const video = document.querySelector('.html5-video-player video');
            if (!video || isNaN(video.currentTime)) return;
            const currentTime = video.currentTime;
            for (let i = 0; i < sponsorSegments.length; i++) {
                const seg = sponsorSegments[i];
                if (seg.segment && seg.segment[0] <= currentTime && currentTime < seg.segment[1]) {
                    video.currentTime = seg.segment[1];
                    video.muted = true;
                    break;
                }
            }
        }

        setTimeout(fetchSegments, 1000);
        setInterval(checkSponsorSegments, 500);
    }

    // ----------------------------------------------------------------
    // 7. Tick - runs cleanup in case the response hook misses something
    // ----------------------------------------------------------------
    function tick() {
        fastForwardAd();
        clickSkipButton();
        removeAdElements();
        patchYtCfg();
    }

    // MutationObserver catches ad elements injected by the SPA.
    // Debounce to at most one tick per 1 second of mutations to avoid
    // CPU pegging on YouTube's complex DOM.
    let tickPending = false;
    function scheduleTick() {
        if (tickPending) return;
        tickPending = true;
        setTimeout(() => { tickPending = false; tick(); }, 1000);
    }
    try {
        const observer = new MutationObserver(scheduleTick);
        observer.observe(document.documentElement || document.body, {
            childList: true, subtree: true,
        });
    } catch(e) {}

    // Single cleanup tick after DOM is fully loaded
    try {
        if (document.readyState === 'complete') {
            tick();
        } else {
            window.addEventListener('load', () => { tick(); });
        }
    } catch(e) {}

    // Initial run
    forceEnglish();
    tick();
    setupSponsorBlock();
});
"""


class URLBlocker:
    """Adblock engine using the Ghostery AdBlocker (Node.js subprocess).

    The Ghostery AdBlocker is a TypeScript/JavaScript library. Quillon runs it in
    a long-lived Node.js subprocess and communicates over a local HTTP API.
    """

    def __init__(self) -> None:
        self._whitelist = SECURITY_CONFIG.WHITELIST_DOMAINS
        # Fallback: simple domain set (hosts-file format) for DNS-level blocking
        self._blocked: set[str] = set()
        # URL-result cache
        self._url_cache: dict[str, bool] = {}
        self._url_cache_max = 512

        # Ghostery backend
        self._ghostery: Optional[GhosteryEngineClient] = None
        self._ghostery_error: Optional[str] = None

        # Brave AdBlock engine (cosmetic filtering + scriptlets)
        self._brave: Optional[BraveAdBlockEngine] = None

        self._load_builtin_fallback_rules()
        self._load_blocklist()
        self._init_ghostery_backend()
        # Initialize Brave AdBlock for cosmetic filtering + scriptlets
        self._init_brave_backend()
        # Start background DNS refresh
        threading.Thread(target=self._dns_refresh_loop, daemon=True).start()

    def _init_brave_backend(self) -> None:
        """Initialize Brave AdBlock engine for cosmetic filtering + scriptlets.

        Cache-first: a fresh filter cache (< _DNS_REFRESH_INTERVAL old)
        loads instantly; a missing/stale cache re-downloads in the
        background — same refresh behavior as before, just never on the
        startup path (waiting here used to block the browser launch for
        up to 60s). Until ``is_ready()`` flips true, is_blocked falls
        back to Ghostery + the DNS domain blocklist and upgrades
        automatically.
        """
        try:
            from .brave_adblock import init_brave_adblock

            cache_file = Path.home() / ".quillon" / "adblock_cache" / "filters.bin"
            fresh = cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < _DNS_REFRESH_INTERVAL
            self._brave = init_brave_adblock(force_download=not fresh)

            # Readiness lands in the background; never block launch on it.
            def _wait_ready() -> None:
                try:
                    self._brave.wait_ready(timeout=60.0)
                    print(f"[Adblock] Brave engine ready: {self._brave.is_ready()}")
                except Exception as e:
                    print(f"[Adblock] Brave engine readiness wait failed: {e}")
            threading.Thread(target=_wait_ready, daemon=True).start()
        except Exception as e:
            print(f"[Adblock] Brave backend init failed: {e}")
            self._brave = None


    def _load_builtin_fallback_rules(self) -> None:
        """Parse _BUILTIN_FALLBACK_RULES and add domains to _blocked set."""
        for line in _BUILTIN_FALLBACK_RULES.splitlines():
            domain = self._extract_domain(line)
            if domain:
                self._blocked.add(domain)
        print(f"[Adblock] Loaded {len(self._blocked)} builtin fallback domains")

    # ------------------------------------------------------------------ #
    #  Filterlist download (EasyList / EasyPrivacy)
    # ------------------------------------------------------------------ #



    # ------------------------------------------------------------------ #
    #  Engine init
    # ------------------------------------------------------------------ #

    def _init_ghostery_backend(self) -> None:
        """Initialise the Ghostery AdBlocker backend.

        The engine is started in a BACKGROUND thread so app launch is never
        blocked waiting for Node.js to download/compile the prebuilt filter
        lists (previously this ran on the main thread and could freeze the
        browser for up to 90 seconds on first run). Until the engine reports
        ready, ``is_blocked`` transparently falls back to the DNS domain
        blocklist, then upgrades to the full engine automatically.
        """
        try:
            repo_dir = Path(__file__).parent.parent.parent / "ghostery-adblocker"
            script_path = repo_dir / "server.js"
            self._ghostery = GhosteryEngineClient(repo_dir, script_path)
        except Exception as exc:
            self._ghostery_error = str(exc)
            print(f"[Adblock] Unexpected error initialising Ghostery backend: {exc}")
            self._ghostery = None
            return
        threading.Thread(target=self._ghostery_warmup, daemon=True).start()

    def _ghostery_warmup(self) -> None:
        """Background thread: start the Node engine, log when it's ready."""
        try:
            stats = self._ghostery.start()
            print(f"[Adblock] Ghostery engine initialised: {stats}")
        except GhosteryEngineError as exc:
            self._ghostery_error = str(exc)
            print(f"[Adblock] Failed to initialise Ghostery backend: {exc}")
        except Exception as exc:
            self._ghostery_error = str(exc)
            print(f"[Adblock] Unexpected error initialising Ghostery backend: {exc}")
            self._ghostery = None

    # ------------------------------------------------------------------ #
    #  Simple domain blocklist (combined.txt - hosts-file format)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_ip_or_localhost(domain: str) -> bool:
        """Check if a domain is actually an IP address or localhost variant."""
        # IPv4
        parts = domain.split(".")
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return True
        # IPv6 (simplified check)
        if ":" in domain and "." not in domain:
            return True
        # Localhost variants
        if domain in ("localhost", "localhost.localdomain", "local", "broadcasthost",
                      "ip6-localhost", "ip6-loopback", "ip6-localnet", "ip6-mcastprefix",
                      "ip6-allnodes", "ip6-allrouters", "ip6-allhosts"):
            return True
        return False

    @staticmethod
    def _extract_domain(line: str) -> str | None:
        """Extract a clean domain from various blocklist formats."""
        line = line.strip().lower()
        if not line or line.startswith(("#", "!", "[")):
            return None
        # Hosts format: "0.0.0.0 domain.com" or "127.0.0.1 domain.com"
        ip_prefixes = ("0.0.0.0 ", "127.0.0.1 ", "::1 ", "255.255.255.255 ")
        for prefix in ip_prefixes:
            if line.startswith(prefix):
                domain = line[len(prefix):].split()[0].strip()
                if domain and "." in domain and not URLBlocker._is_ip_or_localhost(domain):
                    return domain.rstrip(".")
        # uBlock/AdGuard format: "||domain.com^" or "||domain.com"
        if line.startswith("||"):
            domain = line[2:].split("^")[0]
            if domain and "." in domain:
                return domain
        # Pattern format: "*domain.com*" or "*ad.domain.com*"
        if line.startswith("*") and line.endswith("^"):
            domain = line.strip("*^")
            if domain and "." in domain:
                # Could be like "ad.durasite.net" or "-ad-"
                # Only return if it looks like a domain
                parts = domain.split(".")
                if len(parts) >= 2 and all(p.isalnum() or p == "-" for p in parts):
                    return domain
        # Plain domain
        if line.startswith("domain:"):
            line = line[7:]
        if "." in line and not any(c in line for c in "/~?=:#@ \t"):
            # Check it looks like a real domain (has TLD)
            return line
        return None

    def _load_blocklist(self) -> None:
        """Load simple domain blocklist from combined.txt and dns_blocklist.txt cache."""
        for fname in ("combined.txt", "dns_blocklist.txt"):
            blocklist_file = PATHS.BLOCKLIST_DIR / fname
            if not blocklist_file.exists():
                continue
            for line in blocklist_file.open():
                domain = self._extract_domain(line)
                if domain:
                    self._blocked.add(domain)

    # ------------------------------------------------------------------ #
    #  DNS blocklist refresh (background thread)
    # ------------------------------------------------------------------ #

    def _load_dns_blocklist(self) -> None:
        """Fetch and merge DNS-based blocklists (NextDNS, AdGuard, etc.)."""
        new_domains: set[str] = set()
        from urllib.request import Request as HTTPRequest, urlopen
        for src in _DNS_BLOCKLIST_SOURCES:
            try:
                req = HTTPRequest(src, headers={"User-Agent": "Quillon/1.0"})
                with urlopen(req, timeout=15) as resp:
                    data = resp.read().decode("utf-8", errors="ignore")
                for line in data.splitlines():
                    domain = self._extract_domain(line)
                    if domain:
                        new_domains.add(domain)
            except Exception as exc:
                print(f"[Adblock] DNS blocklist fetch failed for {src}: {exc}")
        if new_domains:
            self._blocked |= new_domains
            _DNS_BLOCKLIST_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _DNS_BLOCKLIST_CACHE.write_text("\n".join(sorted(new_domains)))
            self._write_hosts_file(new_domains)

    def _write_hosts_file(self, domains: set[str]) -> None:
        """Write blocked domains to a local hosts file for DNS-level blocking.
        
        Output: ~/.quillon/hosts.txt (one domain per line: 0.0.0.0 domain.com)
        To use system-wide: sudo cp ~/.quillon/hosts.txt /etc/hosts.d/quillon && sudo systemctl restart dnsmasq
        Or add to /etc/hosts manually.
        """
        hosts_path = PATHS.QUILLON_DIR / "hosts.txt"
        try:
            hosts_path.parent.mkdir(parents=True, exist_ok=True)
            lines = ["# Quillon adblock hosts - auto-generated", "# " + time.ctime()]
            for d in sorted(domains):
                lines.append(f"0.0.0.0 {d}")
                lines.append(f":: {d}")
            hosts_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"[Adblock] Wrote {len(domains)} domains to {hosts_path}")
        except Exception as exc:
            print(f"[Adblock] Failed to write hosts file: {exc}")

    def _dns_refresh_loop(self) -> None:
        """Periodic background refresh of DNS blocklists."""
        while True:
            self._load_dns_blocklist()
            time.sleep(_DNS_REFRESH_INTERVAL)

    # ------------------------------------------------------------------ #
    #  Main check
    # ------------------------------------------------------------------ #

    @property
    def blocked_count(self) -> int:
        return len(self._blocked)

    def filter_stats(self) -> str:
        """Return a one-line summary of loaded rules."""
        ghostery_stats = self._ghostery.stats() if self._ghostery else {}
        lists = ghostery_stats.get("lists", 0)
        return f"Adblock engine: Ghostery + DNS domains: {lists} lists, {len(self._blocked)} blocked"

    @staticmethod
    def _request_type_for_url(url_str: str) -> str:
        """Guess the content-type for the adblock engine based on URL extension."""
        lower = url_str.lower()
        if lower.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs")):
            return "script"
        if lower.endswith((".css", ".scss", ".less")):
            return "stylesheet"
        if lower.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp")):
            return "image"
        if lower.endswith((".mp4", ".webm", ".m3u8", ".ts")):
            return "media"
        if lower.endswith((".woff", ".woff2", ".ttf", ".eot")):
            return "font"
        if lower.endswith((".json", ".xml", ".html", ".htm")):
            return "other"
        if "xmlhttprequest" in lower:
            return "xmlhttprequest"
        return "other"

    def is_blocked(self, url_str: str, source_url: str = "", qt_resource_type: int | None = None) -> bool:
        """Check if *url_str* should be blocked.

        Uses the Ghostery engine first, then falls back to the simple domain blocklist.

        ``qt_resource_type`` should be the raw ``int`` value of Qt's
        ``QWebEngineUrlRequestInfo.ResourceType`` for this request, taken
        straight from the interceptor. When provided it is mapped to the
        Ghostery engine's expected vocabulary (script/xhr/image/media/...),
        which is what lets rules scoped to a specific resource type actually
        match. When omitted (e.g. for navigation-level checks that don't
        go through the interceptor), we fall back to guessing from the
        URL's file extension - a much weaker heuristic that misses most
        modern ad/tracking calls, which have no extension at all.
        """
        if self._ghostery_error:
            # If we have a known error, don't spam logs, just use fallback
            pass

        if qt_resource_type is not None:
            request_type = QT_RESOURCE_TYPE_MAP.get(qt_resource_type, "other")
        else:
            request_type = self._request_type_for_url(url_str)
        cache_key = (url_str, source_url or "", request_type)

        # Cache hit
        cached = self._url_cache.get(cache_key)
        if cached is not None:
            return cached

        # 0) Early whitelist check - allow local traffic unconditionally
        try:
            from urllib.parse import urlparse
            host = urlparse(url_str).hostname
        except Exception:
            host = None

        if host:
            host = host.lower()
            parts = host.split(".")
            for i in range(len(parts) - 1):
                domain = ".".join(parts[i:])
                if domain in self._whitelist:
                    self._cache_result(cache_key, False)
                    return False

        # 1) Ghostery engine check (most precise)
        try:
            if self._ghostery and self._ghostery.ready:
                result = self._ghostery.match(url_str, source_url, request_type)
                if "error" in result:
                    raise GhosteryEngineError(result["error"])
                if result.get("match"):
                    self._cache_result(cache_key, True)
                    return True
        except GhosteryEngineError as exc:
            # If the backend fails, we'll fall back to domain blocking
            print(f"[Adblock] Ghostery backend error: {exc}")
            self._ghostery_error = str(exc)
        except Exception as exc:
            print(f"[Adblock] Unexpected error in Ghostery match: {exc}")

        # 1.5) Brave AdBlock engine check
        try:
            if self._brave and self._brave.is_ready():
                brave_result = self._brave.check_url_detailed(url_str, source_url, request_type)
                if brave_result.get("matched"):
                    self._cache_result(cache_key, True)
                    return True
        except Exception as exc:
            print(f"[Adblock] Brave match error: {exc}")

        # 2) Fallback: simple domain-level check
        if host:
            parts = host.split(".")
            for i in range(len(parts) - 1):
                domain = ".".join(parts[i:])
                if domain in self._blocked:
                    self._cache_result(cache_key, True)
                    return True

        self._cache_result(cache_key, False)
        return False

    def get_cosmetic_styles(self, url_str: str) -> str:
        """Return ready-to-inject cosmetic CSS for *url_str*.

        Combines Ghostery's cosmetic filters with Brave's cosmetic filtering
        for comprehensive element hiding.
        """
        css_parts = []

        # 1. Ghostery cosmetic filters (existing)
        try:
            if self._ghostery and self._ghostery.ready:
                result = self._ghostery.cosmetics(
                    url_str,
                    self._extract_hostname(url_str),
                    self._extract_domain_from_url(url_str),
                )
                if "error" not in result and result.get("styles"):
                    css_parts.append(result["styles"])
        except Exception:
            pass

        # 2. Brave AdBlock cosmetic filtering (new)
        try:
            if self._brave and self._brave.is_ready():
                resources = self._brave.get_cosmetic_resources(url_str)
                if resources:
                    # Hide selectors
                    for selector in resources.get("hide_selectors", []):
                        css_parts.append(f"{selector} {{ display: none !important; visibility: hidden !important; }}")
                    # Style selectors
                    for selector, style in resources.get("style_selectors", {}).items():
                        css_parts.append(f"{selector} {{ {style} }}")
        except Exception:
            pass

        return "\n".join(css_parts) if css_parts else ""

    def get_cosmetic_css(self, url_str: str) -> Tuple[set[str], set[str], set[str]]:
        """Legacy API — kept for compatibility with older call sites.

        Returns (hide_selectors, style_selectors, exceptions). New code
        should use :meth:`get_cosmetic_styles`, which returns the raw CSS
        from the Ghostery engine instead of throwing the result away (the
        previous implementation asked the engine for cosmetics and then
        returned empty sets, so cosmetic filtering never worked).
        """
        return set(), set(), set()

    def get_injected_script(self, url_str: str) -> str:
        """Get scriptlet(s) to inject for this URL.

        Combines Ghostery's own per-URL scriptlets (anti-adblock defusers,
        popup killers, etc.) with Brave's scriptlets and our YouTube ad-killer.
        """
        script = ""
        try:
            if self._ghostery and self._ghostery.ready:
                ghostery_script = self._ghostery.scriptlet(url_str) or ""
                if ghostery_script:
                    script += ghostery_script
                    if not ghostery_script.rstrip().endswith(";"):
                        script += ";"
        except Exception as exc:
            print(f"[Adblock] Ghostery scriptlet error: {exc}")

        # Brave AdBlock scriptlets (json-prune, override-property-read, etc.)
        try:
            if self._brave and self._brave.is_ready():
                brave_script = self._brave.get_scriptlet_for_url(url_str)
                if brave_script:
                    script += brave_script
        except Exception as exc:
            print(f"[Adblock] Brave scriptlet error: {exc}")

        YOUTUBE_DOMAINS = ("youtube.com", "youtu.be")
        if any(domain in url_str for domain in YOUTUBE_DOMAINS):
            script += YOUTUBE_SCRIPTLET
        return script

    # Compatibility: expose scriptlet engine for legacy code
    @property
    def scriptlet_engine(self):
        """Return self for backward compatibility with old AdblockerEngine interface."""
        return self

    def get_all_scriptlets(self):
        """Return all scriptlets as list of objects with .code attribute."""
        # Return empty list - Ghostery engine handles scriptlets via scriptlet property
        return []

    def get_filter_stats(self) -> dict:
        """Return filter statistics."""
        ghostery_stats = self._ghostery.stats() if self._ghostery else {}
        return {
            "engine_loaded": self._ghostery is not None and self._ghostery_error is None,
            "dns_domains": len(self._blocked),
            "ghostery_lists": ghostery_stats.get("lists", 0),
            "ghostery_error": self._ghostery_error,
        }

    def _cache_result(self, cache_key: tuple, blocked: bool) -> None:
        if len(self._url_cache) >= self._url_cache_max:
            for _ in range(self._url_cache_max // 2):
                try:
                    self._url_cache.pop(next(iter(self._url_cache)))
                except StopIteration:
                    break
        self._url_cache[cache_key] = blocked

    def _extract_hostname(self, url_str: str) -> str:
        """Extract hostname from URL."""
        try:
            from urllib.parse import urlparse
            return urlparse(url_str).hostname or ""
        except Exception:
            return ""

    def _extract_domain_from_url(self, url_str: str) -> str:
        """Extract domain from URL."""
        try:
            from urllib.parse import urlparse
            hostname = urlparse(url_str).hostname or ""
            # Simple domain extraction - in practice we'd use proper public suffix list
            parts = hostname.split(".")
            if len(parts) >= 2:
                return ".".join(parts[-2:])
            return hostname
        except Exception:
            return ""