"""WebEngine components: SafePage, Interceptor, profile setup."""
# Removed WebChannel - causes Mojo IPC segfaults. Using runJavaScript() instead.

import json
from urllib.parse import urlparse
from pathlib import Path
from PyQt6.QtCore import QUrl, QObject, pyqtSlot
from PyQt6.QtNetwork import QSslConfiguration, QSslCertificate
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
    QWebEngineUrlRequestJob,
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
    QWebEngineScript,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

from .config import APP_CONFIG, SECURITY_CONFIG
from .blocker import URLBlocker
from .tampermonkey_scripts import inject_ghostery_scriptlet

# ─────────────────────────────────────────────────────────────
#  Auto-trust mitmproxy CA for HTTPS interception
# ─────────────────────────────────────────────────────────────

def _install_mitmproxy_ca(profile=None) -> None:
    """Install mitmproxy CA certificate into Qt's SSL configuration.
    
    Looks for the certificate in standard mitmproxy locations and BFSB data dir.
    This enables transparent HTTPS interception without browser warnings.
    
    If profile is provided, also adds CA to the profile's SSL configuration.
    """
    import os
    from pathlib import Path

    # Search locations for mitmproxy CA certificate
    search_paths = [
        Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem",  # Default
        Path.home() / ".bfsb" / "mitmproxy-ca-cert.pem",       # BFSB data dir
        Path("/usr/local/share/ca-certificates/mitmproxy.crt"), # System
        Path("/etc/ssl/certs/mitmproxy.pem"),                   # System
    ]

    # Also check MITMPROXY_CA env var
    if ca_env := os.environ.get("MITMPROXY_CA"):
        search_paths.insert(0, Path(ca_env))

    cert_path = None
    for p in search_paths:
        if p.exists():
            cert_path = p
            break

    if not cert_path:
        return  # No cert found, skip silently

    try:
        # Read certificate
        with open(cert_path, "rb") as f:
            cert_data = f.read()

        # Parse certificate (supports PEM and DER)
        certs = QSslCertificate.fromData(cert_data)
        if not certs:
            return

        cert = certs[0]

        # Add to default SSL configuration
        config = QSslConfiguration.defaultConfiguration()
        ca_certs = config.caCertificates()
        # Check if already added (by serial number)
        if not any(c.serialNumber() == cert.serialNumber() for c in ca_certs):
            ca_certs.append(cert)
            config.setCaCertificates(ca_certs)
            QSslConfiguration.setDefaultConfiguration(config)
            print(f"[BFSB] Installed mitmproxy CA from {cert_path}")

        # Also add to profile's SSL configuration if provided
        if profile is not None:
            profile_config = QSslConfiguration.defaultConfiguration()
            profile_ca_certs = profile_config.caCertificates()
            if not any(c.serialNumber() == cert.serialNumber() for c in profile_ca_certs):
                profile_ca_certs.append(cert)
                profile_config.setCaCertificates(profile_ca_certs)
                # Set on profile if method exists
                if hasattr(profile, 'setSslConfiguration'):
                    profile.setSslConfiguration(profile_config)
                    print(f"[BFSB] Installed mitmproxy CA on profile")

    except Exception as e:
        print(f"[BFSB] Failed to install mitmproxy CA: {e}")


def _ensure_mitmproxy_ca_in_nss() -> None:
    """Add mitmproxy CA to NSS database for QtWebEngine/Firefox/Chrome trust.
    
    This uses certutil to add the CA to the user's NSS database (~/.pki/nssdb).
    QtWebEngine on Linux uses the system NSS database for certificate validation.
    """
    import os
    import subprocess
    from pathlib import Path

    # Find mitmproxy CA
    search_paths = [
        Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem",
        Path.home() / ".bfsb" / "mitmproxy-ca-cert.pem",
        Path("/usr/local/share/ca-certificates/mitmproxy.crt"),
        Path("/etc/ssl/certs/mitmproxy.pem"),
    ]
    if ca_env := os.environ.get("MITMPROXY_CA"):
        search_paths.insert(0, Path(ca_env))

    cert_path = None
    for p in search_paths:
        if p.exists():
            cert_path = p
            break

    if not cert_path:
        return

    # Check if certutil is available
    if not _check_command("certutil"):
        return

    nss_db = Path.home() / ".pki" / "nssdb"
    nss_db.mkdir(parents=True, exist_ok=True)

    # Check if already added
    try:
        result = subprocess.run(
            ["certutil", "-L", "-d", f"sql:{nss_db}"],
            capture_output=True, text=True, timeout=5
        )
        if "mitmproxy" in result.stdout:
            return  # Already added
    except Exception:
        pass

    # Add to NSS database
    try:
        subprocess.run([
            "certutil", "-A",
            "-d", f"sql:{nss_db}",
            "-t", "C,,",  # Trust as CA
            "-n", "mitmproxy",
            "-i", str(cert_path)
        ], check=True, capture_output=True, timeout=10)
        print(f"[BFSB] Added mitmproxy CA to NSS database")
    except Exception as e:
        print(f"[BFSB] Failed to add CA to NSS: {e}")


def _check_command(cmd: str) -> bool:
    """Check if a command is available."""
    import subprocess
    try:
        subprocess.run([cmd, "--version"], capture_output=True, timeout=2)
        return True
    except Exception:
        return False


# Cache for the local Piped instance probe: (url, last_check_monotonic).
# A network probe on every navigation made YouTube navigation hang when the
# local Docker instance wasn't running; cache makes it at most 1 check/min.
_local_piped_cache = None

# Invidious instances currently on the official maintained list
# (docs.invidious.io/instances) as of the time this was written. Public
# instances rotate/die often - yewtu.be, the previous single hardcoded
# choice, is NOT on the current official list and is very likely dead,
# which is why YouTube redirects were silently failing and users ended up
# back on real, ad-serving youtube.com. Several instances are listed so a
# single dead mirror doesn't strand the user - see _dead_frontend_instances
# below for the runtime fallback that actually uses this redundancy.
INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://invidious.tiekoetter.com",
    "https://inv.thepixora.com",
    "https://yt.chocolatemoo53.com",
]

# Piped instances as fallback (best-effort; public Piped/Invidious mirrors
# go up and down constantly - see _dead_frontend_instances).
PIPED_INSTANCES = [
    "https://piped.kavin.rocks",
    "https://piped.projectsegfau.lt",
    "https://piped.rkevin.dev",
    "https://piped.mha.fi",
]

# Instances that failed to load THIS session. Populated by the load-failure
# handler in create_web_view(). Previously there was no such tracking: the
# "fallback" logic in build_url() looked like it rotated instances but every
# call site passed instance_idx=0, so it always retried the exact same
# (possibly dead) instance forever. This set is what makes fallback actually
# happen: a dead instance is skipped for the rest of the session instead of
# being retried on every single video.
_dead_frontend_instances: set[str] = set()



# ══════════════════════════════════════════════════════════════════
# BFSB:// SCHEME REGISTRATION
# ══════════════════════════════════════════════════════════════════
# `bfsb://about`, `bfsb://newtab` etc. are how the web page layer reaches
# the Qt main window. The scheme MUST be registered before any
# QWebEngineProfile is created, which means after QApplication but
# before get_server / create_web_view runs.

_bfsb_scheme_registered = False


def _register_bfsb_scheme() -> None:
    """Register the `bfsb://` URL scheme with QtWebEngine (process-wide).
    Idempotent — safe to call multiple times.
    """
    global _bfsb_scheme_registered
    if _bfsb_scheme_registered:
        return
    try:
        scheme = QWebEngineUrlScheme(b"bfsb")
        scheme.setFlags(
            QWebEngineUrlScheme.Flag.LocalScheme
            | QWebEngineUrlScheme.Flag.LocalAccessAllowed
            | QWebEngineUrlScheme.Flag.SecureScheme
            | QWebEngineUrlScheme.Flag.CorsEnabled
            | QWebEngineUrlScheme.Flag.FetchApiAllowed
            | QWebEngineUrlScheme.Flag.ContentSecurityPolicyIgnored
        )
        QWebEngineUrlScheme.registerScheme(scheme)
        _bfsb_scheme_registered = True
        print("[WebEngine] Registered URL scheme: bfsb://")
    except Exception as e:
        # Registering twice is fine — print any other error.
        print(f"[WebEngine] bfsb:// scheme registration issue: {e}")


# ══════════════════════════════════════════════════════════════════
# Inject cosmetic CSS via JavaScript (runs at DocumentCreation + on navigation)
# ══════════════════════════════════════════════════════════════════
COSMETIC_CSS_INJECTOR = r"""
(function() {
    'use strict';
    if (window.__bfsbCosmeticInjected) return;
    window.__bfsbCosmeticInjected = true;

    function injectCSS(css) {
        if (!css || !css.trim()) return;
        var style = document.createElement('style');
        style.textContent = css;
        style.setAttribute('data-bfsb-cosmetic', 'true');
        // Safe append - wait for head/body if not ready
        var root = document.head || document.documentElement;
        if (root) {
            root.appendChild(style);
        } else {
            var observer = new MutationObserver(function() {
                var r = document.head || document.documentElement;
                if (r) {
                    observer.disconnect();
                    r.appendChild(style);
                }
            });
            observer.observe(document, { childList: true, subtree: true });
        }
    }

    // Expose for Python to call
    window.__bfsbInjectCosmeticCSS = injectCSS;

    // Also expose a ready flag
    window.__bfsbCosmeticReady = true;
})();
"""


class RequestInterceptor(QWebEngineUrlRequestInterceptor):
    """Intercepts and blocks requests to blocked domains."""

    _init_called = False

    # Domains that are explicitly ALLOWED for third-party requests
    # (CDNs, fonts, APIs needed for site functionality)
    ALLOWED_THIRD_PARTY = frozenset({
        # Fonts
        'fonts.googleapis.com', 'fonts.gstatic.com',
        # CDNs
        'cdn.jsdelivr.net', 'cdnjs.cloudflare.com', 'unpkg.com',
        'code.jquery.com', 'ajax.googleapis.com',
        # Maps
        'maps.googleapis.com', 'maps.gstatic.com',
        # reCAPTCHA
        'www.google.com', 'www.gstatic.com', 'www.recaptcha.net',
        # YouTube embed
        'www.youtube.com', 'www.youtube-nocookie.com', 'i.ytimg.com',
        # GitHub
        'github.githubassets.com', 'avatars.githubusercontent.com',
        # Stripe
        'js.stripe.com', 'm.stripe.network',
        # Cloudflare
        'cdnjs.cloudflare.com', 'unpkg.com',
        # Microsoft
        'cdn.jsdelivr.net', 'ajax.aspnetcdn.com',
        # Bootstrap/FontAwesome
        'maxcdn.bootstrapcdn.com', 'use.fontawesome.com',
        # Algolia
        'cdn.jsdelivr.net',
    })

    def __init__(self, blocker: URLBlocker) -> None:
        super().__init__()
        self._blocker = blocker
        if not RequestInterceptor._init_called:
            RequestInterceptor._init_called = True

    # Hardcoded fast-path for the biggest ad/analytics/telemetry networks,
    # including YouTube's ad-serving and ad-metrics endpoints. Checked
    # first, on EVERY request, independent of self._blocker's list —
    # so ads still get blocked even if the (file-based) blocklist is
    # incomplete or fails to load.
    HARD_BLOCK_SUBSTRINGS = (
        # Core ad networks from _BUILTIN_FALLBACK_RULES in blocker.py
        # Google/YouTube ads + ad-metrics
        "doubleclick.net",
        "googlesyndication.com",
        "googleadservices.com",
        "google-analytics.com",
        "googletagmanager.com",
        "googletagservices.com",
        "adservice.google.",
        "pagead2.googlesyndication.com",
        "youtube.com/api/stats/ads",
        "youtube.com/api/stats/qoe",
        "youtube.com/api/stats/watchtime",
        "youtube.com/api/stats/playback",
        "youtube.com/pagead",
        "youtube.com/ptracking",
        "youtube.com/get_midroll",
        "youtube.com/pcs/activeview",
        "youtube.com/youtubei/v/log_event",
        "youtube.com/youtubei/v1/log_event",
        "youtube.com/youtubei/v1/player/ad_break",
        "youtube.com/youtubei/v1/updated_metadata",
        "googlevideo.com/videogoodput",
        "ad.doubleclick.net",
        "adnxs.com",
        "adsrvr.org",
        "criteo.com",
        "criteo.net",
        "outbrain.com",
        "taboola.com",
        "scorecardresearch.com",
        "quantserve.com",
        "moatads.com",
        "amazon-adsystem.com",
        "ads.youtube.com",
        "ad.youtube.com",
        "tracker.com",
        "play.google.com/log",
        # Additional tracking/advertising domains
        "liftoff.io",
        "advertising-api-eu.amazon.com",
        "udcm.yahoo.com",
        "log.fc.yahoo.com",
        "metrika.yandex.ru",
        "appmetrica.yandex.ru",
        "ironsource.mobi",
        "s.youtube.com",
        "redirector.googlevideo.com",
        "pangleglobal.com",
        "tagmanager.google.com",
        "app.posthog.com",
        "eu.posthog.com",
        "us.i.posthog.com",
        "rudderstack.com",
        "snowplowanalytics.com",
        "fingerprintjs.com",
        "bnc.lt",
        "graph.facebook.com",
        "tr.facebook.com",
        "graph.instagram.com",
        "i.instagram.com",
        "ads-api.x.com",
        "analytics.x.com",
        "d.reddit.com",
        "business-api.tiktok.com",
        "log.byteoversea.com",
        "widgets.pinterest.com",
        "pixel.quora.com",
        "qevents.quora.com",
        "api-adservices.apple.com",
        "books-analytics-events.apple.com",
        "xp.apple.com",
        "grs.hicloud.com",
        "data.mistat.india.xiaomi.com",
        "data.mistat.rus.xiaomi.com",
        "ngfts.lge.com",
        "browser.events.data.msn.com",
        "mads-eu.amazon.com",
        "privacyportal.onetrust.com",
        "consent.cookiebot.com",
        "consentcdn.cookiebot.com",
        "cookiebot.com",
        "consent.trustarc.com",
        "sdk.privacy-center.org",
        "cdn.privacy-mgmt.com",
        "app.usercentrics.eu",
        "cmp.osano.com",
        "clientstream.launchdarkly.com",
        "click.mailchimp.com",
        "widget.intercom.io",
        "js.driftt.com",
        "api.onesignal.com",
        "dai.google.com",
        "ssl.p.jwpcdn.com",
        # Egyptian/Middle Eastern ad networks
        "arabclicks.com",
        "admixer.net",
        "adriver.ru",
        "adx1.com",
        "adsmarket.com",
        "arabsocial.net",
        "bidvertiser.com",
        "egyptadnetwork.com",
        "googleads.g.doubleclick.net",
        "mediavine.com",
        "mgid.com",
        "onthe.io",
        "popads.net",
        "popcash.net",
        "propellerads.com",
        "pubmatic.com",
        "reklamstore.com",
        "rubiconproject.com",
        "smartadserver.com",
        "taboola.com",
        "teads.tv",
        "trafficfactory.biz",
        "undertone.com",
        "yieldlove.com",
        "/pagead/",
        # Ad serving paths
        "/ads/",
        "/ad.",
        "/ad?",
        "/ad&",
        "/advert/",
        "/banner/",
        "/promo/",
        "/sponsor/",
        "/track/",
        "/pixel/",
        "/tag/",
        "/metrics/",
        "/analytics/",
        "/count/",
        "/stat/",
        "/log/",
        "/fc=",
        "/rid=",
        "/cid=",
        "/adserver/",
        "/ad-engine/",
        "/ad-platform/",
        "/adnetwork/",
        "/adservice/",
        "/video-ads/",
        "/in-stream/",
        "/pre-roll/",
        "/post-roll/",
        "/mid-roll/",
        ".js?ad=",
        ".js?adid=",
        ".js?adtype=",
        ".php?ad=",
        ".php?adid=",
        ".php?adtype=",
        ".html?ad=",
        ".html?adid=",
        ".html?adtype=",
        "/ads.",
        "/banners.",
        "/promos.",
        "/marketing.",
        "/campaigns.",
        "/offers.",
        "/deals.",
        "/coupons.",
        "?utm_source=",
        "?utm_medium=",
        "?utm_campaign=",
        "?utm_term=",
        "?utm_content=",
        "?utm_id=",
        "&utm_source=",
        "&utm_medium=",
        "&utm_campaign=",
        "&utm_term=",
        "&utm_content=",
        "&utm_id=",
    )

    def interceptRequest(self, info) -> None:  # type: ignore[override]
        url = info.requestUrl().toString()
        parsed_url = urlparse(url)
        host = (parsed_url.hostname or "").lower()
        # Allow BFSB's local services unconditionally, but do not treat a
        # remote hostname containing "localhost" or a local port as local.
        if host in {"localhost", "127.0.0.1", "::1"} and (
            parsed_url.port in {None, 8888, 8889}
        ):
            return

        url_lower = url.lower()
        first_party = info.firstPartyUrl().toString()
        qt_resource_type = info.resourceType().value

        # 1) Hardcoded ad/telemetry networks - blocked unconditionally,
        #    regardless of resource type, before we even ask the blocklist.
        for needle in self.HARD_BLOCK_SUBSTRINGS:
            if needle in url_lower:
                info.block(True)
                return

        # 2) Additional ad blocking patterns for YouTube and other ad sources
        # These patterns catch ad URLs that might bypass the main blocklist
        ad_patterns = [
            # YouTube ad endpoints (COMPLETE from Poli Filter Rules + research)
            "/api/stats/ads",
            "/api/stats/qoe",
            "/api/stats/watchtime",
            "/api/stats/playback",
            "/pagead/",
            "/pagead/adview",
            "/ptracking",
            "/get_midroll",
            "/pcs/activeview",
            "/youtubei/v1/log_event",
            "/youtubei/v1/player/ad_break",
            "/youtubei/v1/updated_metadata",
            "/get_midroll_info",
            "/get_video_info",
            "/ad_companion",
            
            # Google ad networks
            "/ads/",
            "/ad.",
            "/ad?",
            "/ad&",
            "/advert/",
            "/banner/",
            "/promo/",
            "/sponsor/",
            
            # Social media ads
            "/ads/",
            "/promoted/",
            "/sponsored/",
            "/featured/",
            
            # Ad tracking/analytics
            "/track/",
            "/pixel/",
            "/tag/",
            "/metrics/",
            "/analytics/",
            "/count/",
            "/stat/",
            "/log/",
            
            # Ad frequency capping
            "/fc=",
            "/rid=",
            "/cid=",
            
            # Ad network APIs
            "/adserver/",
            "/ad-engine/",
            "/ad-platform/",
            "/adnetwork/",
            "/adservice/",
            
            # Ad serving patterns
            "/video-ads/",
            "/in-stream/",
            "/pre-roll/",
            "/post-roll/",
            "/mid-roll/",
            
            # Common ad file patterns
            ".js?ad=",
            ".js?adid=",
            ".js?adtype=",
            ".php?ad=",
            ".php?adid=",
            ".php?adtype=",
            ".html?ad=",
            ".html?adid=",
            ".html?adtype=",
            
            # Ad network subdomains
            "/ads.",
            "/banners.",
            "/promos.",
            "/marketing.",
            "/campaigns.",
            "/offers.",
            "/deals.",
            "/coupons.",
            
            # UTM tracking parameters
            "?utm_source=",
            "?utm_medium=",
            "?utm_campaign=",
            "?utm_term=",
            "?utm_content=",
            "?utm_id=",
            "&utm_source=",
            "&utm_medium=",
            "&utm_campaign=",
            "&utm_term=",
            "&utm_content=",
            "&utm_id=",
        ]

        for pattern in ad_patterns:
            if pattern in url_lower:
                info.block(True)
                return

        # 3) Full blocklist engine check on EVERY request, regardless of
        #    resource type - with the REAL Qt resource type passed through
        #    (see blocker.py's QT_RESOURCE_TYPE_MAP / is_blocked docstring).
        #    Previously no type was passed at all, so the engine guessed the
        #    type from the URL's file extension and got it wrong for almost
        #    every modern ad/tracking request (RTB pings, YouTube's pagead/
        #    ptracking calls, analytics beacons - none of which have a
        #    recognizable extension), silently defeating every EasyList/
        #    EasyPrivacy rule scoped to a specific resource type.
        if self._blocker.is_blocked(url, first_party, qt_resource_type):
            info.block(True)
            return


class SafePage(QWebEnginePage):
    """WebEngine page with navigation blocking for dangerous content."""

    blocker: URLBlocker | None = None

    def __init__(self, profile: QWebEngineProfile, parent) -> None:
        super().__init__(profile, parent)

    def acceptNavigationRequest(
        self, url: QUrl, nav_type: int, is_main_frame: bool
    ) -> bool:
        url_str = url.toString()
        # Rewrite YouTube URLs to privacy-friendly frontends.
        # SafePage itself has no _rewrite_youtube_url (it lives on BFSBPage);
        # guard so a bare SafePage never crashes with AttributeError.
        rewriter = getattr(self, "_rewrite_youtube_url", None)
        if rewriter is not None:
            url_str = rewriter(url_str)
        url = QUrl(url_str)
        path = url.path().lower() if hasattr(url, "path") else url_str.lower()

        # Block dangerous file extensions
        for ext in SECURITY_CONFIG.BAD_EXTENSIONS:
            if path.endswith(ext):
                print(f"BLOCKED DOWNLOAD: {url_str}")
                return False

        # Block URLs on blocklist
        if SafePage.blocker and SafePage.blocker.is_blocked(url_str, qt_resource_type=0):  # 0 = MainFrame/document
            print(f"BLOCKED: {url_str}")
            return False

        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def javaScriptConsoleMessage(self, level, message, line, source) -> None:
        """Log JS console messages for debugging."""
        level_names = {0: "INFO", 1: "WARNING", 2: "ERROR"}
        level_name = level_names.get(level, "LOG")
        print(f"[JS Console {level_name}] {source}:{line} - {message}")


class BFSBPage(SafePage):
    """BFSB custom page that works with local HTTP server backend."""

    def __init__(self, profile: QWebEngineProfile, parent) -> None:
        super().__init__(profile, parent)
        self._bfsb_internal_nav = False  # Track bfsb:// navigations to suppress progress bar
        # Track the most recent "real" URL — used to restore the URL bar
        # when Chromium commits a rejected URL like `about:blank#blocked`.
        self._last_real_url: str = ""
        # Listen for URL changes (after navigation) to keep the tracker fresh.
        self.urlChanged.connect(self._track_real_url)

    def createWindow(self, window_type: 'QWebEnginePage.WebWindowType') -> 'QWebEnginePage':
        """Route JS popup requests (OAuth flows) to a real new window.

        Without this, window.open() returns ``None`` and Google/TikTok/etc.
        sign-in popups silently fail. We spawn a top-level
        ``QWebEngineView`` so the OAuth callback can complete and write
        cookies back to the shared profile (which is how the parent frame
        picks up the auth state). Google/TikTok also explicitly rely on
        the popup having its OWN window — child tabs inside the main
        view's window break the OAuth state machine.
        """
        from PyQt6.QtWidgets import QMainWindow

        popup_view = QWebEngineView()
        popup_page = BFSBPage(self.profile(), popup_view)
        popup_view.setPage(popup_page)
        # Use a normal top-level OS window so the OAuth flow's
        # window.open/close logic works as Chromium expects.
        try:
            popup_view.setAttribute(
                __import__('PyQt6.QtCore', fromlist=['Qt']).Qt.WidgetAttribute.WA_DeleteOnClose
            )
        except Exception:
            pass
        # Hold a reference in a list so Python doesn't GC it while open.
        BFSBPage._open_popups = getattr(BFSBPage, '_open_popups', [])
        BFSBPage._open_popups.append((popup_view, popup_page))
        # Clean up the hold when the popup is destroyed.
        try:
            popup_view.destroyed.connect(
                lambda *_: BFSBPage._open_popups.remove((popup_view, popup_page))
                if (popup_view, popup_page) in BFSBPage._open_popups else None
            )
        except Exception:
            pass
        popup_view.show()
        return popup_page

    def _schedule_blank_restore(self) -> None:
        """Restore the page after a rejected bfsb:// navigation.

        Chromium commits rejected navigations as ``about:blank#blocked``,
        which blanks the view. If no real navigation is in flight shortly
        after the action, put the last real URL back.
        """
        from PyQt6.QtCore import QTimer as _QTimer

        def _restore() -> None:
            try:
                view = self.view()
                if view is None or view.isLoading():
                    return  # a real navigation is already in flight
                current = view.url().toString()
                if current.startswith(("about:blank", "bfsb://")) and self._last_real_url:
                    view.setUrl(QUrl(self._last_real_url))
            except Exception:
                pass

        _QTimer.singleShot(250, _restore)

    def _track_real_url(self, url: QUrl) -> None:
        """Save the latest non-bfsb/non-blocked URL as the 'real' URL.

        Used to restore the view after Chromium commits
        `about:blank#blocked` for rejected navigation requests
        (e.g. `bfsb://about` clicks).
        """
        url_str = (url.toString() if hasattr(url, "toString") else str(url)) or ""
        if not url_str:
            return
        # Don't store rejected/bfsb/internal URLs as the "real" URL.
        if url_str.startswith(("bfsb://", "about:blank#blocked", "about:blank")):
            return
        # First run, when we load a normal URL, record it.
        self._last_real_url = url_str

    def acceptNavigationRequest(
        self, url: QUrl, nav_type: int, is_main_frame: bool
    ) -> bool:
        url_str = url.toString()
        # Rewrite YouTube URLs to privacy-friendly frontends
        url_str = self._rewrite_youtube_url(url_str)
        url = QUrl(url_str)
        path = url.path().lower() if hasattr(url, "path") else url_str.lower()

        # Handle BFSB internal URLs (bfsb:// scheme). Kept for
        # compatibility with any older links that still use the scheme,
        # but the home page About button uses a fragment-style trigger
        # which is detected in main_window._on_url_changed instead.
        if url_str.startswith("bfsb://"):
            self._bfsb_internal_nav = True
            url_lower = url_str.lower()
            print(f"[BFSBPage] Intercepted (legacy bfsb://): {url_str}")
            if hasattr(self, '_main_window') and self._main_window:
                main = self._main_window
                if url_lower.startswith("bfsb://goback"):
                    print("[BFSBPage] -> go_back()")
                    main.go_back()
                elif url_lower.startswith("bfsb://goforward"):
                    print("[BFSBPage] -> go_forward()")
                    main.go_forward()
                elif url_lower.startswith("bfsb://reload"):
                    print("[BFSBPage] -> reload()")
                    main.reload()
                elif url_lower.startswith("bfsb://navigate"):
                    from urllib.parse import urlparse, parse_qs
                    parsed = urlparse(url_str)
                    query = parse_qs(parsed.query)
                    if 'url' in query:
                        print(f"[BFSBPage] -> navigate({query['url'][0]})")
                        main.navigate(query['url'][0])
                elif url_lower.startswith("bfsb://switchtab"):
                    from urllib.parse import urlparse, parse_qs
                    parsed = urlparse(url_str)
                    query = parse_qs(parsed.query)
                    if 'index' in query:
                        print(f"[BFSBPage] -> switch_tab({query['index'][0]})")
                        main.switch_tab(int(query['index'][0]))
                elif url_lower.startswith("bfsb://closetab"):
                    from urllib.parse import urlparse, parse_qs
                    parsed = urlparse(url_str)
                    query = parse_qs(parsed.query)
                    if 'index' in query:
                        print(f"[BFSBPage] -> close_tab({query['index'][0]})")
                        main.close_tab(int(query['index'][0]))
                elif url_lower.startswith("bfsb://newtab"):
                    from urllib.parse import urlparse, parse_qs
                    parsed = urlparse(url_str)
                    query = parse_qs(parsed.query)
                    if 'url' in query:
                        print(f"[BFSBPage] -> new_tab({query['url'][0]})")
                        main.new_tab(query['url'][0])
                    else:
                        print("[BFSBPage] -> new_tab()")
                        main.new_tab()
                elif url_lower.startswith("bfsb://about"):
                    print("[BFSBPage] -> _show_about_dialog()")
                    main._show_about_dialog()
                elif url_lower.startswith("bfsb://preferences"):
                    print("[BFSBPage] -> _show_preferences_dialog()")
                    main._show_preferences_dialog()
            # Clear URL to prevent fallback navigation, then restore the
            # page the user was on: a rejected bfsb:// navigation would
            # otherwise commit about:blank#blocked and blank the screen.
            self._schedule_blank_restore()
            return False

        # Block dangerous file extensions
        for ext in SECURITY_CONFIG.BAD_EXTENSIONS:
            if path.endswith(ext):
                print(f"BLOCKED DOWNLOAD: {url_str}")
                return False

        # Block URLs on blocklist
        if BFSBPage.blocker and BFSBPage.blocker.is_blocked(url_str, qt_resource_type=0):  # 0 = MainFrame/document
            print(f"BLOCKED: {url_str}")
            return False

        # Allow all navigation to local server (our backend)
        if "localhost" in url_str or "127.0.0.1" in url_str or ":8888" in url_str or ":8889" in url_str:
            return super().acceptNavigationRequest(url, nav_type, is_main_frame)

        # Allow all other navigation (external links)
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def contextMenuEvent(self, event) -> None:
        """Override the default WebEngine context menu with BFSBMenu.

        Right-clicking on a page (background, image, link) would
        otherwise show Chromium's built-in menu ("Open in new tab",
        "Save image as", etc.). We replace it with the BFSB chrome
        menu so the user has the same options (New tab, Bookmarks,
        About BFSB, Quit) on the page as in the chrome.

        The page has a ``_main_window`` back-reference set by
        ``MainWindow._create_view`` (line 328). If it's not set
        (e.g. the user opened a popup from JS before the back-ref
        was wired) we fall through to the default Chromium menu.
        """
        try:
            main = getattr(self, '_main_window', None)
            if main is None or not hasattr(main, '_show_main_menu'):
                super().contextMenuEvent(event)
                return
            main._show_main_menu()
            event.accept()
        except Exception:
            # If anything goes wrong, hand the event back to Qt so
            # the user still gets the default menu — never silently
            # eat the right-click.
            super().contextMenuEvent(event)

    @staticmethod
    def _rewrite_youtube_url(url: str) -> str:
        """Rewrite YouTube URLs to Piped (privacy-friendly frontend)."""
        import re

        # Use the module-level lists (shared with the load-failure retry
        # logic in create_web_view), filtered to drop anything that's
        # failed to load already this session.
        invidious_live = [i for i in INVIDIOUS_INSTANCES if i not in _dead_frontend_instances]
        piped_live = [i for i in PIPED_INSTANCES if i not in _dead_frontend_instances]
        # If every known instance has died this session, fall back to
        # the full lists anyway - a possibly-broken frontend is still a
        # better outcome than silently landing on real, ad-serving
        # youtube.com, and instances do sometimes recover.
        if not invidious_live:
            invidious_live = list(INVIDIOUS_INSTANCES)
        if not piped_live:
            piped_live = list(PIPED_INSTANCES)

        # Check if we have a local instance running (Docker on port 8080).
        # Cached + very short timeout so a hung probe can never stall the UI
        # thread for more than 150ms once per minute.
        import time
        import urllib.request
        global _local_piped_cache
        now = time.monotonic()
        if _local_piped_cache is None or (now - _local_piped_cache[1]) > 60:
            try:
                urllib.request.urlopen(
                    "http://127.0.0.1:8080/api/v1/instance", timeout=0.15
                )
                _local_piped_cache = ("http://127.0.0.1:8080", now)
            except Exception:
                _local_piped_cache = (None, now)
        if _local_piped_cache[0]:
            piped_live.insert(0, _local_piped_cache[0])

        def build_url(path, instance_idx=0, use_invidious=True):
            instances = invidious_live if use_invidious else piped_live
            if instance_idx < len(instances):
                return f"{instances[instance_idx]}{path}"
            # Instance list exhausted: fall back to the first working
            # candidate of the OTHER set, rather than bouncing back to a
            # (possibly dead) preferred instance on every navigation.
            fallback = piped_live if use_invidious else invidious_live
            if fallback:
                return f"{fallback[0]}{path}"
            return url

        # youtube.com/watch?v=... or youtube.com/watch?...
        if "youtube.com/watch" in url:
            m = re.search(r"[?&]v=([^&]+)", url)
            if m:
                return build_url(f"/watch?v={m.group(1)}", use_invidious=True)

        # youtu.be/VIDEO_ID
        if "youtu.be/" in url:
            m = re.search(r"youtu\.be/([^?&]+)", url)
            if m:
                return build_url(f"/watch?v={m.group(1).split('?')[0]}", use_invidious=True)

        # youtube.com/shorts/VIDEO_ID
        if "youtube.com/shorts/" in url:
            m = re.search(r"youtube\.com/shorts/([^/?&]+)", url)
            if m:
                return build_url(f"/watch?v={m.group(1)}", use_invidious=True)

        # youtube.com/playlist?list=...
        if "youtube.com/playlist" in url:
            m = re.search(r"[?&]list=([^&]+)", url)
            if m:
                return build_url(f"/playlist?list={m.group(1)}", use_invidious=True)

        # youtube.com/channel/... or youtube.com/c/... or youtube.com/@...
        if "youtube.com/" in url and "youtube.com/watch" not in url and "youtube.com/shorts" not in url and "youtube.com/playlist" not in url:
            m = re.search(r"youtube\.com/(?:channel/|c/|@)([^/?&]+)", url)
            if m:
                return build_url(f"/channel/{m.group(1)}", use_invidious=True)

        # music.youtube.com
        if "music.youtube.com" in url:
            m = re.search(r"[?&]v=([^&]+)", url)
            if m:
                return build_url(f"/watch?v={m.group(1)}", use_invidious=True)

        # Catch-all for any other youtube.com URL (homepage, explore, etc.)
        if "youtube.com" in url and not any(x in url for x in ["youtube.com/watch", "youtube.com/shorts", "youtube.com/playlist", "youtube.com/channel", "youtube.com/c/", "youtube.com/@"]):
            return build_url("/", use_invidious=True)

        return url

class BFSBSchemeHandler(QWebEngineUrlSchemeHandler):
    """Handles every bfsb:// request (fetch() or navigation).

    GUI actions (new tab, switch tab, close tab, back/forward/reload,
    navigate) are issued from the page as ``fetch('bfsb://…')``. The
    handler dispatches them to the main window and replies with a tiny
    body. Because fetch() never navigates the page, an action can no
    longer blank the current tab (the old ``location.href='bfsb://…'``
    flow committed ``about:blank#blocked`` and blacked the screen).
    """

    _main_window = None  # set by BFSBWindow after profile creation

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:
        url = job.requestUrl().toString()
        try:
            main = BFSBSchemeHandler._main_window
            if main is not None and url.startswith("bfsb://"):
                # navigate() dispatches every bfsb:// action form.
                main.navigate(url)
        except Exception as e:
            print(f"[BFSBSchemeHandler] action failed for {url}: {e}")
        finally:
            self._reply_ok(job)

    @staticmethod
    def _reply_ok(job: QWebEngineUrlRequestJob) -> None:
        try:
            from PyQt6.QtCore import QBuffer, QIODevice

            buf = QBuffer(job)  # parented to the job → lives until replied
            buf.setData(b"ok")
            buf.open(QIODevice.OpenModeFlag.ReadOnly)
            job.reply(b"text/plain", buf)
        except Exception:
            try:
                job.fail(QWebEngineUrlRequestJob.ErrorDeny)
            except Exception:
                pass


_bfsb_scheme_handler: BFSBSchemeHandler | None = None


def get_bfsb_scheme_handler() -> BFSBSchemeHandler:
    """Process-wide bfsb:// scheme handler (created once)."""
    global _bfsb_scheme_handler
    if _bfsb_scheme_handler is None:
        _bfsb_scheme_handler = BFSBSchemeHandler()
    return _bfsb_scheme_handler


def set_bfsb_action_target(window) -> None:
    """Point the bfsb:// scheme handler at the main window."""
    BFSBSchemeHandler._main_window = window


def create_web_profile() -> QWebEngineProfile:
    """Create and configure WebEngine profile."""
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtWebEngineCore import QWebEngineProfile
    from .config import SECURITY_CONFIG, APP_CONFIG
    app = QApplication.instance()
    _register_bfsb_scheme()  # idempotent
    
    # Ensure mitmproxy CA is in system NSS database for QtWebEngine trust
    _ensure_mitmproxy_ca_in_nss()

    profile = QWebEngineProfile(APP_CONFIG.WINDOW_TITLE, app)
    # bfsb:// action requests (fetch-based GUI actions) dispatch here.
    profile.installUrlSchemeHandler(b"bfsb", get_bfsb_scheme_handler())
    # Disk HTTP cache: re-downloading every image/script/font on every page
    # load makes heavy sites (WhatsApp Web, YouTube) crawl. Keep a bounded
    # on-disk cache instead of NoCache.
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
    # Force persisting cookies so OAuth flows (Google, TikTok, etc.) keep their
    # session/ID tokens between the popup window and main frame. Without this,
    # Google returns "no longer supported in this browser" / sign-in fails.
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    )
    # Use a real on-disk data path inside BFSB's project dir so cookies survive
    # restarts. Default location is %TEMP% which gets purged by some setups.
    cookie_storage = Path.home() / ".local" / "share" / "bfsb" / "cookie_storage"
    cookie_storage.mkdir(parents=True, exist_ok=True)
    if hasattr(QWebEngineProfile, 'setPersistentStoragePath'):
        profile.setPersistentStoragePath(str(cookie_storage))
    # Override the default User-Agent. QtWebEngine's default UA is
    # ``... QtWebEngine/<ver> Chrome/140 ...`` — Google and TikTok now refuse
    # OAuth logins from this fingerprint (they treat it as a less-secure
    # / scripted browser). Drop the QtWebEngine tag so auth providers accept
    # the request as a regular Chrome on Linux.
    #
    # The version below is a current Chrome stable build on Linux x86_64.
    CHROME_UA = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
    )
    if hasattr(profile, 'setHttpUserAgent'):
        profile.setHttpUserAgent(CHROME_UA)

    # Trust mitmproxy CA for HTTPS interception (auto-load from BFSB data dir)
    _install_mitmproxy_ca(profile)

    # NextDNS DoH configuration (malware, cryptojacking, tracking at DNS level)
    if SECURITY_CONFIG.DOH_ENABLED:
        doh_url = ""
        provider = SECURITY_CONFIG.DOH_PROVIDER.lower()
        if provider == "cloudflare-security":
            doh_url = "https://security.cloudflare-dns.com/dns-query"
        elif provider == "cloudflare-family":
            doh_url = "https://family.cloudflare-dns.com/dns-query"
        elif provider == "quad9":
            doh_url = "https://dns.quad9.net/dns-query"
        elif provider == "adguard":
            doh_url = "https://dns.adguard.com/dns-query"
        elif provider == "nextdns":
            doh_url = "https://dns.nextdns.io"
        elif provider == "custom" and SECURITY_CONFIG.CUSTOM_DOH_URL:
            doh_url = SECURITY_CONFIG.CUSTOM_DOH_URL

        if doh_url and hasattr(profile, 'setDnsOverHttpsEndpoint'):
            profile.setDnsOverHttpsEndpoint(doh_url)
            print(f"[WebEngine] DoH enabled ({provider}): {doh_url}")
        if hasattr(profile, 'setDnsOverHttpsMode'):
            # 0 = Off, 1 = Automatic, 2 = Secure
            profile.setDnsOverHttpsMode(2)  # Secure - require DoH

    # Disable service workers, webauthn, and other background features
    if hasattr(QWebEngineProfile, 'setSpellCheckEnabled'):
        profile.setSpellCheckEnabled(False)
    if hasattr(QWebEngineProfile, 'setHttpCacheMaximumSize'):
        profile.setHttpCacheMaximumSize(50 * 1024 * 1024)  # 50MB limit

    # Register Tampermonkey-style scriptlets via script manager
    # This replaces the old manual QWebEngineScript registration
    try:
        from .tampermonkey_scripts import setup_tampermonkey_scripts, log_diag
        setup_tampermonkey_scripts(profile)
        log_diag("[WebEngine] Tampermonkey-style scripts registered (profile-level)")
    except Exception as e:
        from .tampermonkey_scripts import log_diag
        log_diag(f"[WebEngine] FAILED to register tampermonkey scripts: {e}")

    return profile


def configure_web_settings(settings: QWebEngineSettings) -> None:
    """Apply security-focused WebEngine settings + rendering fixes."""
    # OAuth flows (Google sign-in, TikTok login, etc.) require window.open()
    # popups to actually open. Without this they silently do nothing because
    # JavascriptCanOpenWindows=False swallows the popup request.
    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
    # Enable localStorage - many sites (and adblock-tester.com) require it.
    # CookieVault isolates auth cookies; localStorage is sandboxed per-origin anyway.
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.AllowGeolocationOnInsecureOrigins, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.DnsPrefetchEnabled, False)

    # Enable JavaScript (required for our bridge)
    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
    # GPU-accelerated rendering. Forcing WebGL/2D-canvas off pushes ALL
    # rasterization onto the CPU, which is why heavy SPA sites (WhatsApp Web,
    # YouTube) felt sluggish. Both on; GL fallback flags are handled at app
    # startup for broken drivers.
    settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)

    # Disable features that cause Wayland/Mojo errors
    if hasattr(QWebEngineSettings.WebAttribute, 'ServiceWorkersEnabled'):
        settings.setAttribute(QWebEngineSettings.WebAttribute.ServiceWorkersEnabled, False)
    if hasattr(QWebEngineSettings.WebAttribute, 'WebRTCPublicInterfacesOnly'):
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebRTCPublicInterfacesOnly, True)
    if hasattr(QWebEngineSettings.WebAttribute, 'ScrollAnimatorEnabled'):
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, False)
    if hasattr(QWebEngineSettings.WebAttribute, 'PdfViewerEnabled'):
        settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)
    if hasattr(QWebEngineSettings.WebAttribute, 'FullScreenSupportEnabled'):
        settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, False)
    if hasattr(QWebEngineSettings.WebAttribute, 'OffscreenRenderingEnabled'):
        settings.setAttribute(QWebEngineSettings.WebAttribute.OffscreenRenderingEnabled, False)
    if hasattr(QWebEngineSettings.WebAttribute, 'Pepper3DEnabled'):
        settings.setAttribute(QWebEngineSettings.WebAttribute.Pepper3DEnabled, False)




# Stub common tracking/error libraries on every page so inline calls don't throw
# ReferenceError when blocklists block the underlying library. Without this,
# pages like adblock-tester.com break with errors like "bugsnag is not defined"
# because they have inline `<script>window.bugsnagClient=bugsnag("...")</script>`
# after the blocked script tag.
#
# The stubs are CONSPICUOUS no-ops. They don't actually track anything.
# Critically, we do NOT pre-define window.Sentry/bugsnag FULL globals - those
# would make eval-based adblocker tests think the script ran. Instead we only
# catch the "function is not defined" errors via window[name] = function(){}.
TRACKING_STUB_SCRIPTLET = r"""
(function() {
    'use strict';
    if (window.__bfsbTrackingStubs) return;
    window.__bfsbTrackingStubs = true;
    // When window.bugsnag() is called but bugsnag.min.js was blocked,
    // we still want it to return a no-op client rather than throw.
    // Importantly, we ONLY define these if they're not already defined.
    if (typeof window.bugsnag !== 'function') {
        window.bugsnag = function() {
            return {
                notify: function() {},
                leaveBreadcrumb: function() {},
                config: {},
                metaData: {},
                addCallback: function() { return this; },
                user: {}
            };
        };
    }
})();
"""


# Stealth-fingerprint-fix scriptlet.
#
# TikTok / Arkose Labs / DataDome / hCaptcha all check
# `navigator.userAgentData.brands` against the User-Agent string. Vanilla
# QtWebEngine reports `["Chromium", "Not=A?Brand"]` regardless of which
# UA you set on the QWebEngineProfile. That triggers immediate bot
# detection on TikTok's login flow (Google login still works because
# Google only checks the UA string).
#
# Inject a DocumentCreation scriptlet at the same priority as the
# tracking-stub that rewrites Navigator UAData, plugins, webdriver and
# languages before any tracking script runs.
STEALTH_OVERRIDE_SCRIPTLET = r"""
(function() {
    'use strict';
    if (window.__bfsbStealthApplied) return;
    Object.defineProperty(window, '__bfsbStealthApplied', {
        configurable: false, enumerable: false,
        writable: false, value: true,
    });

    var REAL_BRANDS = [
        {brand: 'Google Chrome', version: '143'},
        {brand: 'Chromium',      version: '143'},
        {brand: 'Not_A Brand',   version: '24'},
    ];
    var REAL_FULL_VERSION_LIST = [
        '143.0.7499.192',
        '143.0.7499.192',
        '24.0.0.0',
    ];
    var REAL_PLATFORM = 'Linux';
    var REAL_MOBILE = false;
    var REAL_ARCH = 'x86';
    var REAL_BITNESS = '64';

    // Override on the PROTOTYPES, not the instances. Chromium's
    // NavigatorUAData is a C++-backed object whose instance properties
    // cannot be redefineProperty'd; the prototypes are real JS classes.
    try {
        if (typeof NavigatorUAData === 'function' && NavigatorUAData.prototype) {
            var proto = NavigatorUAData.prototype;
            Object.defineProperty(proto, 'brands',   {configurable:true, get:function(){return REAL_BRANDS;}});
            Object.defineProperty(proto, 'mobile',   {configurable:true, get:function(){return REAL_MOBILE;}});
            Object.defineProperty(proto, 'platform', {configurable:true, get:function(){return REAL_PLATFORM;}});
            var orig = Object.getOwnPropertyDescriptor(proto, 'getHighEntropyValues');
            if (!orig || orig.configurable) {
                proto.getHighEntropyValues = function() {
                    return Promise.resolve({
                        architecture: REAL_ARCH,
                        bitness: REAL_BITNESS,
                        brands: REAL_BRANDS,
                        fullVersionList: REAL_FULL_VERSION_LIST,
                        mobile: REAL_MOBILE,
                        model: '',
                        platform: REAL_PLATFORM,
                        platformVersion: '',
                        uaFullVersion: REAL_FULL_VERSION_LIST[0],
                        wow64: false,
                    });
                };
            }
        }
    } catch (e) {}

    // Override navigator.webdriver, languages, language, plugins on the
    // Navigator prototype so all existing and future instances see the
    // new values without us having to redefine per-instance.
    try {
        var np = Navigator.prototype;
        Object.defineProperty(np, 'webdriver', {configurable:true, get:function(){return false;}});
        Object.defineProperty(np, 'languages', {configurable:true, get:function(){return ['en-US','en'];}});
        Object.defineProperty(np, 'language',  {configurable:true, get:function(){return 'en-US';}});
        var FAKE_PLUGINS = [
            {name: 'PDF Viewer',         filename: 'internal-pdf-viewer',
             description: 'Portable Document Format'},
            {name: 'Chrome PDF Viewer',  filename: 'internal-pdf-viewer',
             description: 'Portable Document Format'},
            {name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer',
             description: 'Portable Document Format'},
            {name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer',
             description: 'Portable Document Format'},
            {name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer',
             description: 'Portable Document Format'},
        ];
        Object.defineProperty(np, 'plugins', {configurable:true, get:function(){return FAKE_PLUGINS;}});
    } catch (e) {}

    // window.chrome runtime object — vanilla Chromium-tracing embeds
    // expose a thin `chrome.app.isInstalled` etc. shape; Arkose and
    // DataDome look for it.
    try {
        window.chrome = {
            app: {
                isInstalled: false,
                InstallState: {
                    DISABLED: 'disabled',
                    INSTALLED: 'installed',
                    NOT_INSTALLED: 'not_installed'
                },
                RunningState: {
                    CANNOT_RUN: 'cannot_run',
                    READY_TO_RUN: 'ready_to_run',
                    RUNNING: 'running'
                }
            },
            runtime: {
                OnInstalledReason: {
                    CHROME_UPDATE: 'chrome_update',
                    INSTALL: 'install',
                    SHARED_MODULE_UPDATE: 'shared_module_update',
                    UPDATE: 'update'
                },
                OnRestartRequiredReason: {
                    APP_UPDATE: 'app_update',
                    OS_UPDATE: 'os_update',
                    PERIODIC: 'periodic'
                },
                PlatformArch: {
                    ARM: 'arm', ARM64: 'arm64',
                    MIPS: 'mips', MIPS64: 'mips64',
                    X86_32: 'x86-32', X86_64: 'x86-64',
                    UNKNOWN: 'unknown'
                },
                PlatformOs: {
                    ANDROID: 'android', CROS: 'cros', FUCHSIA: 'fuchsia',
                    LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd',
                    UNKNOWN: 'unknown', WIN: 'win'
                },
                RequestUpdateCheckStatus: {
                    NO_UPDATE: 'no_update',
                    THROTTLED: 'throttled',
                    UPDATE_AVAILABLE: 'update_available'
                },
                connect: function() {},
                sendMessage: function() {},
            },
            csi: function() { return {}; },
            loadTimes: function() { return {}; },
        };
    } catch (e) {}
})();
"""


# Early cosmetic-blocking scriptlet. Injected at DocumentCreation on every
# page (and subframe), BEFORE any content is parsed or rendered — this is the
# same technique uBlock Origin uses. It watches the DOM and hides any element
# matching the selectors Python pushed into window.__bfsbCosmeticCSS (set via
# runJavaScript from loadStarted, which fires before the document body is
# parsed) plus a small built-in list of generic ad/tracker containers. The
# result: ad slots never become visible, instead of appearing and being
# hidden after loadFinished.
EARLY_COSMETIC_SCRIPTLET = r"""
(function() {
    'use strict';
    if (window.__bfsbEarlyCosmetic) return;
    window.__bfsbEarlyCosmetic = true;

    var GENERIC_HIDE = [
        'iframe[src*="doubleclick.net"]',
        'iframe[src*="googlesyndication.com"]',
        'iframe[src*="googleadservices.com"]',
        'iframe[src*="adservice.google."]',
        'iframe[src*="adnxs.com"]',
        'iframe[src*="criteo."]',
        'iframe[src*="taboola."]',
        'iframe[src*="outbrain."]',
        'ins.adsbygoogle',
        'iframe[id^="google_ads_iframe"]',
        'div[id^="google_ads"]',
        'div[id^="div-gpt-ad"]',
        '[id^="aswift_"]',
        '[id*="-ad-container"]',
        '[class*="advert-"], [class*="ad-banner"], [class*="ad-slot"]'
    ].join(',');

    var style = document.createElement('style');
    style.setAttribute('data-bfsb-early-cosmetic', 'true');
    var applyScheduled = false;
    function apply() {
        applyScheduled = false;
        var extra = '';
        try { extra = window.__bfsbCosmeticCSS || ''; } catch (e) {}
        var css = GENERIC_HIDE + (extra ? ',' + extra : '');
        if (style.textContent !== css) {
            style.textContent = css + ' { display: none !important; visibility: hidden !important; }';
        }
        var root = document.head || document.documentElement;
        if (root && style.parentNode !== root) root.appendChild(style);
    }
    function scheduleApply() {
        if (!applyScheduled) {
            applyScheduled = true;
            setTimeout(apply, 200); // Throttle: max once per 200ms
        }
    }
    apply(); // Initial apply
    var mo = new MutationObserver(scheduleApply);
    function start() {
        mo.observe(document.documentElement || document, { childList: true, subtree: true });
    }
    if (document.documentElement) start();
    else document.addEventListener('readystatechange', function once() {
        document.removeEventListener('readystatechange', once); start();
    });
})();
"""

# ─────────────────────────────────────────────────────────────────
# NUCLEAR ADBLOCKER — Maximum aggression, zero tolerance
# ─────────────────────────────────────────────────────────────────
NUCLEAR_ADBLOCK_SCRIPTLET = r"""
(function() {
    'use strict';
    if (window.__bfsbNuclearAdblock) return;
    window.__bfsbNuclearAdblock = true;

    // ═══════════════════════════════════════════════════════════════
    // 1. AGGRESSIVE ELEMENT REMOVAL — Remove anything ad-like
    // ═══════════════════════════════════════════════════════════════
    var NUCLEAR_SELECTORS = [
        // Iframes from ad networks
        'iframe[src*="doubleclick"]',
        'iframe[src*="googlesyndication"]',
        'iframe[src*="googleadservices"]',
        'iframe[src*="adservice.google"]',
        'iframe[src*="adnxs"]',
        'iframe[src*="criteo"]',
        'iframe[src*="taboola"]',
        'iframe[src*="outbrain"]',
        'iframe[src*="rubiconproject"]',
        'iframe[src*="casalemedia"]',
        'iframe[src*="adform"]',
        'iframe[src*="yieldmo"]',
        'iframe[src*="sharethrough"]',
        'iframe[src*="spotxchange"]',
        'iframe[src*="teads"]',
        'iframe[src*="undertone"]',
        'iframe[src*="smartadserver"]',
        'iframe[src*="zedo"]',
        'iframe[src*="adform"]',
        'iframe[src*="popads"]',
        'iframe[src*="adroll"]',
        'iframe[src*="bidswitch"]',
        'iframe[src*="openx"]',
        'iframe[src*="w55c"]',
        'iframe[src*="amazon-adsystem"]',
        'iframe[src*="pangleglobal"]',
        'iframe[src*="ironsource"]',
        'iframe[src*="mads"]',
        'iframe[src*="advertising-api"]',

        // Google ads
        'ins.adsbygoogle',
        'iframe[id^="google_ads_iframe"]',
        'div[id^="google_ads"]',
        'div[id^="div-gpt-ad"]',
        '[id^="aswift_"]',
        '[id*="-ad-container"]',

        // Generic ad classes/IDs
        '[class*="advert"]', '[class*="ad-banner"]', '[class*="ad-slot"]',
        '[class*="sponsor"]', '[class*="promoted"]', '[class*="ad-"]',
        '[id*="advert"]', '[id*="ad-banner"]', '[id*="ad-slot"]',
        '[id*="sponsor"]', '[id*="promoted"]', '[id*="ad-"]',
        '[data-ad]', '[data-sponsor]', '[data-ad-slot]',
        'iframe[src*="ads"]', 'iframe[src*="banner"]',
        'iframe[src*="sponsor"]', 'iframe[src*="promoted"]',

        // Video ads
        '.video-ad', '.ad-overlay', '.ad-slot', '.video-ad-container',
        '.video-ads', '.player-ad', '.video-page-ad', '.watch-page-ad',
        '.ad-overlay-container', '.ad-player', '.videoAdUi',
        '.ytp-ad-module', '.ytp-ad-player-overlay',

        // Sponsored content
        '.sponsored-video', '.promoted-video', '.promoted-content',
        '.sponsor-box', '.ad-box', '.ad-banner',
        '.sidebar-sponsor', '.related-sponsor',
        '.recommended-sponsored', '.related-promoted',
        '.sponsored-content', '.native-ad', '.paid-post',

        // Header/footer/banner
        '.header-ad', '.footer-ad', '.top-banner-ad', '.bottom-banner-ad',
        '.leaderboard-ad', '.skyscraper-ad', '.rectangle-ad',
        '.billboard-ad', '.interstitial-ad', '.prestitial-ad',

        // Social media ads
        '[data-testid*="ad"]', '[data-testid*="sponsor"]',
        '[data-pagelet*="ad"]', '[data-pagelet*="sponsor"]',

        // Amazon/Yahoo/TikTok/etc specific
        '[id*="amzn"]', '[class*="amzn"]',
        '[id*="yahoo"]', '[class*="yahoo"]',
        '[id*="tiktok"]', '[class*="tiktok"]',

        // Consent/cookie banners (optional - can break sites)
        // '.cookie-banner', '.consent-banner', '.gdpr-banner',
    ].join(',');

    function nuclearCleanup() {
        try {
            var removed = 0;
            var elements = document.querySelectorAll(NUCLEAR_SELECTORS);
            for (var i = 0; i < elements.length; i++) {
                var el = elements[i];
                if (el.parentNode) {
                    // Only remove iframes from known ad networks
                    var isAdIframe = el.tagName === 'IFRAME' && el.src && (
                        el.src.includes('doubleclick.net') ||
                        el.src.includes('googlesyndication.com') ||
                        el.src.includes('googleadservices.com') ||
                        el.src.includes('adnxs.com') ||
                        el.src.includes('criteo.com') ||
                        el.src.includes('taboola.com') ||
                        el.src.includes('outbrain.com') ||
                        el.src.includes('adform.net') ||
                        el.src.includes('yieldmo.com') ||
                        el.src.includes('sharethrough.com') ||
                        el.src.includes('spotxchange.com') ||
                        el.src.includes('teads.tv') ||
                        el.src.includes('undertone.com') ||
                        el.src.includes('smartadserver.com') ||
                        el.src.includes('zedo.com') ||
                        el.src.includes('popads.net') ||
                        el.src.includes('adroll.com') ||
                        el.src.includes('bidswitch.net') ||
                        el.src.includes('openx.net') ||
                        el.src.includes('amazon-adsystem.com') ||
                        el.src.includes('pangleglobal.com') ||
                        el.src.includes('ironsource.mobi') ||
                        el.src.includes('advertising-api') ||
                        el.src.includes('mads-eu.amazon.com') ||
                        el.src.includes('udc.yahoo.com') ||
                        el.src.includes('metrika.yandex.ru') ||
                        el.src.includes('appmetrica.yandex.ru') ||
                        el.src.includes('redirector.googlevideo.com') ||
                        el.src.includes('pangleglobal.com') ||
                        el.src.includes('tagmanager.google.com') ||
                        el.src.includes('posthog.com') ||
                        el.src.includes('rudderstack.com') ||
                        el.src.includes('snowplowanalytics.com') ||
                        el.src.includes('fingerprintjs.com') ||
                        el.src.includes('bnc.lt') ||
                        el.src.includes('graph.facebook.com') ||
                        el.src.includes('tr.facebook.com') ||
                        el.src.includes('graph.instagram.com') ||
                        el.src.includes('i.instagram.com') ||
                        el.src.includes('analytics.x.com') ||
                        el.src.includes('business-api.tiktok.com') ||
                        el.src.includes('log.byteoversea.com') ||
                        el.src.includes('widgets.pinterest.com') ||
                        el.src.includes('pixel.quora.com') ||
                        el.src.includes('qevents.quora.com') ||
                        el.src.includes('xp.apple.com') ||
                        el.src.includes('ngfts.lge.com') ||
                        el.src.includes('mads-eu.amazon.com') ||
                        el.src.includes('privacyportal.onetrust.com') ||
                        el.src.includes('consent.cookiebot.com') ||
                        el.src.includes('cookiebot.com') ||
                        el.src.includes('consent.trustarc.com') ||
                        el.src.includes('sdk.privacy-center.org') ||
                        el.src.includes('cdn.privacy-mgmt.com') ||
                        el.src.includes('app.usercentrics.eu') ||
                        el.src.includes('cmp.osano.com') ||
                        el.src.includes('clientstream.launchdarkly.com') ||
                        el.src.includes('click.mailchimp.com') ||
                        el.src.includes('api.onesignal.com') ||
                        el.src.includes('dai.google.com') ||
                        el.src.includes('ssl.p.jwpcdn.com') ||
                        el.src.includes('s.youtube.com') ||
                        el.src.includes('pagead2.googlesyndication.com') ||
                        el.src.includes('googleads.g.doubleclick.net') ||
                        el.src.includes('securepubads.g.doubleclick.net') ||
                        el.src.includes('pubads.g.doubleclick.net') ||
                        el.src.includes('googletagservices.com') ||
                        el.src.includes('googletagmanager.com') ||
                        el.src.includes('google-analytics.com') ||
                        el.src.includes('connect.facebook.net')
                    );
                    
                    // Only remove if it's an ad iframe OR a clear ad container
                    if (isAdIframe || 
                        el.classList.contains('video-ad') ||
                        el.classList.contains('ad-overlay') ||
                        el.classList.contains('ad-slot') ||
                        el.classList.contains('video-ad-container') ||
                        el.classList.contains('video-ads') ||
                        el.classList.contains('ytp-ad-module') ||
                        el.classList.contains('ytp-ad-player-overlay') ||
                        el.classList.contains('videoAdUi') ||
                        el.classList.contains('adShowing')) {
                        el.parentNode?.removeChild(el);
                        removed++;
                    }
                }
            }
            return removed;
        } catch (e) { return 0; }
    }

    // ═══════════════════════════════════════════════════════════════
    // 2. NETWORK REQUEST BLOCKING — Intercept fetch/XHR/WebSocket
    // ═══════════════════════════════════════════════════════════════
    var AD_DOMAINS = [
        // Clear ad delivery networks
        'doubleclick.net', 'googlesyndication.com', 'googleadservices.com',
        'adservice.google.com', 'adnxs.com', 'criteo.com', 'criteo.net',
        'taboola.com', 'outbrain.com', 'rubiconproject.com', 'casalemedia.com',
        'adform.net', 'yieldmo.com', 'sharethrough.com', 'spotxchange.com',
        'teads.tv', 'undertone.com', 'smartadserver.com', 'zedo.com',
        'popads.net', 'adroll.com', 'bidswitch.net',
        'openx.net', 'w55c.net', 'amazon-adsystem.com', 'pangleglobal.com',
        'ironsource.mobi', 'mads-eu.amazon.com', 'advertising-api-eu.amazon.com',
        'udc.yahoo.com', 'udcm.yahoo.com', 'log.fc.yahoo.com',
        'metrika.yandex.ru', 'appmetrica.yandex.ru',
        'redirector.googlevideo.com', 'pangleglobal.com',
        // YouTube/Google ad specific
        's.youtube.com', 'pagead2.googlesyndication.com', 'googleads.g.doubleclick.net',
        'securepubads.g.doubleclick.net', 'pubads.g.doubleclick.net',
        'googletagservices.com', 'googletagmanager.com',
        // Facebook ad delivery
        'connect.facebook.net', 'staticxx.facebook.com',
    ];

    function isAdDomain(url) {
        try {
            var hostname = new URL(url).hostname;
            return AD_DOMAINS.some(function(domain) {
                return hostname === domain || hostname.endsWith('.' + domain);
            });
        } catch (e) { return false; }
    }

    // Hook fetch
    var originalFetch = window.fetch;
    window.fetch = function(input, init) {
        try {
            var url = typeof input === 'string' ? input : (input && input.url) || '';
            if (isAdDomain(url)) {
                return Promise.resolve(new Response('', {status: 204, statusText: 'Nuclear Blocked'}));
            }
        } catch(e) {}
        return originalFetch.apply(this, arguments);
    };

    // Hook XHR
    var originalXHROpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {
        this._bfsbUrl = url || '';
        return originalXHROpen.apply(this, arguments);
    };
    var originalXHRSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function(data) {
        try {
            if (this._bfsbUrl && isAdDomain(this._bfsbUrl)) {
                this.abort();
                return;
            }
        } catch(e) {}
        return originalXHRSend.apply(this, arguments);
    };

    // Hook WebSocket
    var originalWebSocket = window.WebSocket;
    window.WebSocket = function(url, protocols) {
        if (isAdDomain(url)) {
            // Return a dummy WebSocket that immediately closes
            var dummy = {
                close: function() {},
                send: function() {},
                addEventListener: function() {},
                removeEventListener: function() {},
                readyState: 3, // CLOSED
                CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3
            };
            setTimeout(function() {
                if (dummy.onclose) dummy.onclose({code: 1000, reason: 'Nuclear Blocked'});
            }, 0);
            return dummy;
        }
        return new originalWebSocket(url, protocols);
    };

// ═══════════════════════════════════════════════════════════════
    // 3. PERIODIC CLEANUP — Lightweight, only where needed
    // ═══════════════════════════════════════════════════════════════
    function scheduleCleanup() {
        nuclearCleanup();
    }

    // Only run on actual YouTube pages - skip privacy frontends
    var hostname = window.location.hostname;
    var isYouTube = (hostname === 'www.youtube.com' || hostname === 'youtube.com' || 
                     hostname === 'youtu.be' || hostname === 'm.youtube.com' || 
                     hostname === 'music.youtube.com');
    var isFrontend = (hostname === 'yewtu.be' || hostname === 'www.yewtu.be' ||
                      hostname.includes('piped.') || hostname.includes('invidious'));

    if (isFrontend) {
        // On frontends: network blocking + cosmetic is enough
        // Skip heavy cleanup to save CPU/memory
        console.log('[BFSB Nuclear] Skipping heavy cleanup on frontend: ' + hostname);
    } else if (isYouTube) {
        // Initial cleanup
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function() {
                setTimeout(scheduleCleanup, 100);
                setTimeout(scheduleCleanup, 500);
                setTimeout(scheduleCleanup, 1000);
            });
        } else {
            setTimeout(scheduleCleanup, 100);
            setTimeout(scheduleCleanup, 500);
            setTimeout(scheduleCleanup, 1000);
        }

        // Periodic safety-net cleanup for the YouTube SPA. This used to
        // clearInterval() itself after 2 minutes (a later "save CPU"
        // change) which meant NO cleanup ran at all for the rest of a
        // video/session once you passed the 2-minute mark - exactly why
        // ads that showed up later stopped getting removed. Keep it
        // running for the page's whole life; 10s is cheap enough that
        // there's no real reason to ever turn it off. The MutationObserver
        // below is what actually gives near-instant removal - this interval
        // is just a fallback net.
        setInterval(scheduleCleanup, 10000);
        document.addEventListener('visibilitychange', scheduleCleanup);
    } else {
        // Other sites: one-time cleanup only
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function() {
                setTimeout(scheduleCleanup, 500);
            });
        } else {
            setTimeout(scheduleCleanup, 100);
        }
    }

    // ═══════════════════════════════════════════════════════════════
    // 4. MUTATION OBSERVER — instant removal, runs for the page's life
    // ═══════════════════════════════════════════════════════════════
    if (isYouTube) {
        var cleanupDebounce = null;
        var mo = new MutationObserver(function(mutations) {
            var shouldClean = false;
            for (var i = 0; i < mutations.length; i++) {
                var m = mutations[i];
                if (m.type === 'childList' && m.addedNodes.length > 0) {
                    for (var j = 0; j < m.addedNodes.length; j++) {
                        var node = m.addedNodes[j];
                        if (node.nodeType === 1) { // Element node
                            shouldClean = true;
                            break;
                        }
                    }
                }
                if (shouldClean) break;
            }
            if (shouldClean) {
                // Debounce so a burst of DOM mutations only triggers one
                // cleanup pass, not one per mutation.
                if (cleanupDebounce) clearTimeout(cleanupDebounce);
                cleanupDebounce = setTimeout(scheduleCleanup, 50);
            }
        });
        mo.observe(document.documentElement || document.body, {
            childList: true, subtree: true, attributes: false, characterData: false
        });
        // Deliberately NOT disconnecting this on a timer anymore.
        // Disconnecting after 30s (a previous "save resources" change)
        // meant any ad injected into the DOM after that point - a
        // mid-roll ad, a recommendation-feed ad on scroll, anything on a
        // long watch session - was never caught again. YouTube's ad
        // injection isn't a one-time event that happens in the first
        // 30 seconds, so the observer needs to keep watching for as
        // long as the page is open.
    }

    // ═══════════════════════════════════════════════════════════════
    // 5. BLOCK INLINE SCRIPTS FROM AD DOMAINS
    // ═══════════════════════════════════════════════════════════════
    var originalCreateElement = document.createElement;
    document.createElement = function(tag) {
        var el = originalCreateElement.call(this, tag);
        if (tag === 'script') {
            var originalSetSrc = Object.getOwnPropertyDescriptor(HTMLScriptElement.prototype, 'src')?.set;
            if (originalSetSrc) {
                Object.defineProperty(el, 'src', {
                    set: function(value) {
                        if (isAdDomain(value)) {
                            return; // Don't set src for ad scripts
                        }
                        originalSetSrc.call(this, value);
                    },
                    get: function() { return el.getAttribute('src'); },
                    configurable: true
                });
            }
        }
        return el;
    };

    console.log('[BFSB Nuclear] Adblocker active - ' + AD_DOMAINS.length + ' domains blocked, YouTube mode: ' + isYouTube);
})();
"""

# Register NUCLEAR scriptlet on profile level (runs on ALL pages)


def create_web_view(profile: QWebEngineProfile, blocker: URLBlocker, page_class=None, window=None) -> QWebEngineView:
    """Create a configured WebEngine view with runJavaScript for UI sync."""
    from PyQt6.QtGui import QColor
    from PyQt6.QtCore import QTimer

    view = QWebEngineView()
    if page_class is None:
        page_class = SafePage
    page = page_class(profile, view)
    view.setPage(page)
    if page_class == SafePage:
        SafePage.blocker = blocker
    elif page_class == BFSBPage:
        BFSBPage.blocker = blocker
    # URL request interceptor is set once on the profile in _init_profile
    configure_web_settings(profile.settings())

    # Set background color to match theme (prevents white flash / black screen)
    page.setBackgroundColor(QColor("#0a0a12"))  # C.BG_0 equivalent

    # CSS injection function (injected from Python on load, not at DocumentCreation)
    COSMETIC_INJECT_JS = r"""
    (function() {
        if (window.__bfsbInjectCosmeticCSS) return;
        window.__bfsbCosmeticInjected = true;
        window.__bfsbInjectCosmeticCSS = function(css) {
            if (!css || !css.trim()) return;
            var style = document.createElement('style');
            style.textContent = css;
            style.setAttribute('data-bfsb-cosmetic', 'true');
            var root = document.head || document.documentElement;
            if (root) {
                root.appendChild(style);
            } else {
                var observer = new MutationObserver(function() {
                    var r = document.head || document.documentElement;
                    if (r) {
                        observer.disconnect();
                        r.appendChild(style);
                    }
                });
                observer.observe(document, { childList: true, subtree: true });
            }
        };
        window.__bfsbCosmeticReady = true;
    })();
    """


    # Function to push cosmetic CSS for current URL
    def _inject_cosmetic_css(url_str: str):
        try:
            from .adblock_state import is_adblock_enabled
            if not is_adblock_enabled():
                return
            css = ""
            get_styles = getattr(blocker, "get_cosmetic_styles", None)
            if get_styles is not None:
                # Ghostery returns ready-to-inject CSS (comma-joined selectors
                # + display:none blocks). This is the path that actually makes
                # cosmetic filtering work — the old code asked the engine for
                # cosmetics and then threw the result away.
                css = get_styles(url_str) or ""
            if not css:
                hide_sel, _style_sel, _ = blocker.get_cosmetic_css(url_str)
                css = "\n".join(
                    f"{sel} {{ display: none !important; visibility: hidden !important; }}"
                    for sel in hide_sel
                )
            if css:
                # First ensure the injection function exists, then call it
                page.runJavaScript(COSMETIC_INJECT_JS)
                page.runJavaScript(f"window.__bfsbInjectCosmeticCSS({json.dumps(css)});")

            # Always inject aggressive YouTube ad-slot hiding (runs immediately)
            yt_cosmetic_css = """
                /* Hide YouTube ad slots immediately */
                ytd-ad-slot-renderer, ytd-promoted-sparkles-web-renderer,
                ytd-display-ad-renderer, ytd-infeed-ad-renderer,
                ytd-rich-item-renderer:has(ytd-ad-slot-renderer),
                #masthead-ad, #player-ads, .video-ads, .ad-slot,
                ytd-mech-shelf-renderer, ytd-merch-shelf-renderer,
                .ytd-compact-promoted-video-renderer,
                ytd-promoted-video-renderer,
                [data-ad-slot], [data-ad-client], [data-ad-name],
                ins.adsbygoogle, .adsbygoogle,
                .ytp-ad-module, .ytp-ad-player-overlay,
                .ytp-ad-overlay-container, .ytp-ad-image-overlay,
                .ytp-ad-text-overlay, .ytp-ad-skip-button-container,
                .ytp-ad-preview-container, .ytp-ad-bumper-progress {
                    display: none !important;
                    visibility: hidden !important;
                    opacity: 0 !important;
                    height: 0 !important;
                    width: 0 !important;
                    pointer-events: none !important;
                }
            """
            page.runJavaScript(COSMETIC_INJECT_JS)
            page.runJavaScript(f"window.__bfsbInjectCosmeticCSS({json.dumps(yt_cosmetic_css)});")

            if get_styles is not None and blocker._ghostery and not blocker._ghostery.ready:
                # Ghostery not ready yet - retry in 500ms
                QTimer.singleShot(500, lambda: _inject_cosmetic_css(url_str))
        except Exception as e:
            print(f"[WebEngine] cosmetic CSS inject failed: {e}")

    # Ghostery also ships per-URL JS "scriptlets" (anti-adblock defusers,
    # popup closers, ...). Inject them on every navigation/SPA URL change.
    def _inject_scriptlets(url_str: str):
        try:
            from .adblock_state import is_adblock_enabled
            if not is_adblock_enabled():
                return
            script = blocker.get_injected_script(url_str)
            if script:
                page.runJavaScript(script)
        except Exception as e:
            print(f"[WebEngine] scriptlet inject failed: {e}")

    view._inject_cosmetic_css = _inject_cosmetic_css

    def _retry_dead_frontend(failed_url: str) -> bool:
        """If failed_url is one of our Invidious/Piped redirect targets,
        mark that instance dead for the rest of the session and retry the
        same page on the next available instance.

        Without this, a single dead mirror (this app previously hardcoded
        exactly one Invidious instance, yewtu.be, which is not even on the
        currently-maintained public instance list) just fails silently -
        which is how users end up stuck on a broken page or, worse,
        manually navigating back to real, ad-serving youtube.com where no
        amount of network-level ad blocking can help, because YouTube now
        stitches many ads directly into the video stream server-side.
        Returns True if a retry was dispatched.
        """
        from urllib.parse import urlparse
        try:
            parsed = urlparse(failed_url)
            host = parsed.netloc
        except Exception:
            return False
        if not host:
            return False

        matched = None
        use_invidious = False
        for inst in INVIDIOUS_INSTANCES:
            if host in inst:
                matched, use_invidious = inst, True
                break
        if matched is None:
            for inst in PIPED_INSTANCES:
                if host in inst:
                    matched, use_invidious = inst, False
                    break
        if matched is None:
            return False  # Not a frontend redirect target; nothing to do.

        _dead_frontend_instances.add(matched)
        print(f"[WebEngine] Marking dead frontend instance: {matched}")

        remaining = [
            i for i in (INVIDIOUS_INSTANCES if use_invidious else PIPED_INSTANCES)
            if i not in _dead_frontend_instances
        ]
        if not remaining:
            remaining = [
                i for i in (PIPED_INSTANCES if use_invidious else INVIDIOUS_INSTANCES)
                if i not in _dead_frontend_instances
            ]
        if not remaining:
            return False  # Every known mirror is dead; nothing left to try.

        path_and_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        next_url = f"{remaining[0]}{path_and_query}"
        print(f"[WebEngine] Retrying on next instance: {next_url}")
        from PyQt6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(50, lambda: view.load(QUrl(next_url)))
        return True

    # Diagnostic: log load progress and errors
    def _on_load_started():
        print(f"[WebEngine] Load started: {view.url().toString()}")

    def _record_visit():
        """Best-effort history capture for real web visits.

        Local backend URLs are skipped here (the server logs searches
        itself); everything else http(s) is recorded with the page title.
        """
        try:
            from urllib.parse import urlparse

            url_str = view.url().toString()
            if not url_str.startswith(("http://", "https://")):
                return
            if (urlparse(url_str).hostname or "").lower() in {
                "127.0.0.1", "::1", "localhost", "0.0.0.0",
            }:
                return
            from bfsb.core.storage.history import HistoryStore

            title = ""
            try:
                title = view.page().title() or ""
            except Exception:
                pass
            HistoryStore().record(url_str, title)
        except Exception:
            pass

    def _on_load_finished(ok: bool):
        status = "OK" if ok else "FAILED"
        current_url = view.url().toString()
        print(f"[WebEngine] Load finished ({status}): {current_url}")
        if ok:
            _inject_cosmetic_css(current_url)
            _record_visit()
        else:
            print(f"[WebEngine] Page error - URL: {current_url}")
            _retry_dead_frontend(current_url)

    def _on_load_progress(progress: int):
        if progress % 25 == 0:  # Log every 25%
            print(f"[WebEngine] Load progress: {progress}%")

    view.loadStarted.connect(_on_load_started)
    view.loadFinished.connect(_on_load_finished)
    view.loadProgress.connect(_on_load_progress)

    # Also log URL changes (YouTube SPA navigations now handled by scriptlet, not redirect)
    def _on_url_changed(url):
        url_str = url.toString()
        print(f"[WebEngine] URL changed: {url_str}")
        _inject_cosmetic_css(url_str)
        _inject_scriptlets(url_str)

    view.urlChanged.connect(_on_url_changed)

    # UI sync via runJavaScript (no WebChannel - avoids Mojo IPC segfaults)
    # Python gets state from QWebEngineView directly and pushes to JS
    if window is not None:
        def _sync_ui():
            try:
                # Get state from Python (synchronous, no callbacks needed)
                current_url = view.url().toString()

                # Skip internal bfsb:// URLs and data: URLs (for home page)
                if current_url.startswith("bfsb://") or current_url.startswith("data:"):
                    display_url = ""
                else:
                    display_url = current_url

                can_go_back = view.history().canGoBack()
                can_go_forward = view.history().canGoForward()
                back_disabled = "true" if not can_go_back else "false"
                forward_disabled = "true" if not can_go_forward else "false"

                # Push state to JS - update nav buttons
                view.page().runJavaScript(f"""
                    (function() {{
                        var backBtn = document.getElementById('btn-back');
                        var forwardBtn = document.getElementById('btn-forward');
                        if (backBtn) backBtn.disabled = {back_disabled};
                        if (forwardBtn) forwardBtn.disabled = {forward_disabled};
                    }})();
                """)

                # Push URL to URL bar (only if not focused)
                # Use json.dumps for safe string interpolation
                display_url_js = json.dumps(display_url)
                view.page().runJavaScript(f"""
                    (function() {{
                        var urlInput = document.getElementById('urlInput');
                        if (urlInput && document.activeElement !== urlInput) {{
                            urlInput.value = {display_url_js};
                        }}
                    }})();
                """)
            except Exception as e:
                print(f"[WebEngine] UI sync failed: {e}")

        # Sync UI periodically
        sync_timer = QTimer()
        sync_timer.timeout.connect(_sync_ui)
        sync_timer.start(500)

        # Initial sync after load
        def _on_load_finished_for_sync(ok: bool):
            if ok:
                QTimer.singleShot(100, _sync_ui)

        view.loadFinished.connect(_on_load_finished_for_sync)

    # Diagnostic: render process monitoring
    def _on_render_process_terminated(status, exit_code):
        print(f"[WebEngine] Render process terminated: status={status}, exit_code={exit_code}")

    page.renderProcessTerminated.connect(_on_render_process_terminated)

    return view