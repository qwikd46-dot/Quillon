"""BFSB Proxy Addon for mitmproxy - Blocks ads/trackers at proxy level."""

from mitmproxy import http, ctx
from typing import Optional
import os
import re


class BFSBAdblockAddon:
    """mitmproxy addon that blocks ad/tracking requests."""

    def __init__(self):
        self.blocked_count = 0
        self.allowed_count = 0
        self.cleaned_count = 0

        # The app flips ~/.bfsb/adblock_state; re-read per request
        # (mtime-cached) so toggling never restarts mitmdump.
        from pathlib import Path
        self._state_path = str(Path.home() / ".bfsb" / "adblock_state")
        self._state_mtime: Optional[float] = None
        self._state_enabled = True

    def _adblock_enabled(self) -> bool:
        try:
            mtime = os.stat(self._state_path).st_mtime
            if mtime != self._state_mtime:
                self._state_mtime = mtime
                with open(self._state_path) as f:
                    self._state_enabled = f.read().strip() != "0"
            return self._state_enabled
        except FileNotFoundError:
            return True
        except Exception:
            return True


        # Hardcoded ad/tracking domains and patterns
        self.HARD_BLOCK_DOMAINS = frozenset({
            # Google/YouTube ads
            "doubleclick.net",
            "googlesyndication.com",
            "googleadservices.com",
            "google-analytics.com",
            "googletagmanager.com",
            "googletagservices.com",
            "adservice.google.com",
            "pagead2.googlesyndication.com",
            "tpc.googlesyndication.com",
            "securepubads.g.doubleclick.net",
            "pubads.g.doubleclick.net",
            "ad.doubleclick.net",
            "ads.youtube.com",
            "ad.youtube.com",
            "s.youtube.com",
            # NOTE: googlevideo.com, redirector.googlevideo.com, ytimg.com and
            # yt3.ggpht.com are deliberately NOT here - they carry video
            # content, thumbnails and avatars; blocking them breaks playback.
            # In-stream ad segments are killed by the adformat/ctier/ad_type
            # URL patterns below and by the player-response rewriting.

            # Major ad networks
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
            "adroll.com",
            "bidswitch.net",
            "casalemedia.com",
            "rubiconproject.com",
            "openx.net",
            "smartadserver.com",
            "zedo.com",
            "adform.net",
            "yieldmo.com",
            "sharethrough.com",
            "spotxchange.com",
            "teads.tv",
            "undertone.com",
            "w55c.net",
            "popads.net",

            # Tracking/analytics
            "connect.facebook.net",
            "graph.facebook.com",
            "tr.facebook.com",
            "graph.instagram.com",
            "i.instagram.com",
            "analytics.x.com",
            "ads-api.x.com",
            "business-api.tiktok.com",
            "log.byteoversea.com",
            "pixel.quora.com",
            "qevents.quora.com",
            "rudderstack.com",
            "snowplowanalytics.com",
            "fingerprintjs.com",
            "bnc.lt",
            "tagmanager.google.com",
            "app.posthog.com",
            "eu.posthog.com",
            "us.i.posthog.com",
            "mixpanel.com",
            "segment.io",
            "segment.com",
            "amplitude.com",
            "heap.io",
            "fullstory.com",
            "hotjar.com",
            "mouseflow.com",
            "inspectlet.com",
            "crazyegg.com",
            "luckyorange.com",

            # Fingerprinting/bot detection
            "fpjs.io",
            "clientstream.launchdarkly.com",
            "api.onesignal.com",
            "widget.intercom.io",
            "js.driftt.com",
            "click.mailchimp.com",
            "dai.google.com",
            "ssl.p.jwpcdn.com",
            "pangleglobal.com",
            "metrika.yandex.ru",
            "appmetrica.yandex.ru",
            "ironsource.mobi",
            "liftoff.io",
            "advertising-api-eu.amazon.com",
            "mads-eu.amazon.com",
            "udcm.yahoo.com",
            "log.fc.yahoo.com",
            "privacyportal.onetrust.com",
            "consent.cookiebot.com",
            "consentcdn.cookiebot.com",
            "cookiebot.com",
            "consent.trustarc.com",
            "sdk.privacy-center.org",
            "cdn.privacy-mgmt.com",
            "app.usercentrics.eu",
            "cmp.osano.com",

            # Cryptojacking/malware domains (common)
            "coinhive.com",
            "coin-hive.com",
            "jsecoin.com",
            "minero.cc",
            "webmine.cz",
            "xmrwebmine.com",
            "cryptoloot.pro",
            "deepminer.com",
            "authedmine.com",
        })

        self.HARD_BLOCK_PATTERNS = (
            # YouTube ad/telemetry endpoints (SAFE set - never blocks the
            # player/feed APIs themselves; those are REWRITTEN in response())
            "/api/stats/ads",
            "/api/stats/qoe",
            "/api/stats/att",
            "/pagead/",
            "/ptracking",
            "/pcs/activeview",
            "/get_midroll",
            "/get_midroll_info",
            "/youtubei/v1/log_event",
            "/youtubei/v1/log_interaction",
            # In-stream ad video segments: the player requests them from the
            # same googlevideo.com hosts as content, but ad segment URLs carry
            # parameters real content URLs never have. Blocking by parameter
            # kills pre-roll/mid-roll streams without touching content.
            "adformat=",
            "ctier=",
            "ad_type=",
            "afv=1",
            "ad_device=",
        )

        # Allowed domains (CDNs, fonts, essential services)
        self.ALLOWED_DOMAINS = frozenset({
            "fonts.googleapis.com",
            "fonts.gstatic.com",
            "cdn.jsdelivr.net",
            "cdnjs.cloudflare.com",
            "unpkg.com",
            "code.jquery.com",
            "ajax.googleapis.com",
            "maps.googleapis.com",
            "maps.gstatic.com",
            "www.google.com",
            "www.gstatic.com",
            "www.recaptcha.net",
            "www.youtube.com",
            "www.youtube-nocookie.com",
            "i.ytimg.com",
            "github.githubassets.com",
            "avatars.githubusercontent.com",
            "js.stripe.com",
            "m.stripe.network",
            "cdn.jsdelivr.net",
            "maxcdn.bootstrapcdn.com",
            "use.fontawesome.com",
            "ajax.aspnetcdn.com",
        })

    def _should_block(self, host: str, url: str) -> bool:
        """Check if a request should be blocked."""
        host = host.lower()
        url_lower = url.lower()

        # Allow local traffic
        if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
            return False

        # Check hardcoded patterns in URL FIRST - the whitelist below contains
        # www.youtube.com, so ad endpoints on YouTube hosts must be matched
        # before the whitelist or they would always be allowed.
        for pattern in self.HARD_BLOCK_PATTERNS:
            if pattern in url_lower:
                return True

        # Check allowed domains (and subdomains)
        for allowed in self.ALLOWED_DOMAINS:
            if host == allowed or host.endswith("." + allowed):
                return False

        # Check hardcoded blocked domains (and subdomains)
        for blocked in self.HARD_BLOCK_DOMAINS:
            if host == blocked or host.endswith("." + blocked):
                return True

        return False

    def request(self, flow: http.HTTPFlow) -> None:
        """Intercept and potentially block requests."""
        if not flow.request or not flow.request.pretty_host:
            return

        if not self._adblock_enabled():
            self.allowed_count += 1
            return

        host = flow.request.pretty_host
        url = flow.request.pretty_url

        if self._should_block(host, url):
            self.blocked_count += 1
            ctx.log.info(f"[BFSB Proxy] BLOCKED: {url}")
            # Return empty 204 response
            flow.response = http.Response.make(
                204,  # No Content
                b"",
                {"Content-Type": "text/plain"}
            )
        else:
            self.allowed_count += 1

    # YouTube API ad keys stripped from player/feed responses at the
    # network layer - undetectable by page-side anti-adblock checks.
    # *Renderer keys: feed/promo renderer dicts - the recursive strip removes
    # them at any depth. Wrong keys here break YouTube, so keep this minimal.
    _AD_KEYS = (
        "adPlacements", "adSlots", "adSchedules", "adBreaks", "adSignalsInfo",
        "playerAds", "instreamAds", "bumperAds", "midrolls", "prerolls",
        "postrolls", "adTagUrl", "adTagUrlList", "adParameters", "adModule",
        "adSlotRenderer", "promotedSparklesWebRenderer", "infeedAdRenderer",
        "displayAdRenderer", "videoMastheadAdRenderer", "mealbarPromoRenderer",
        "bannerPromoRenderer",
    )

    @classmethod
    def _strip_ads(cls, obj, depth: int = 0) -> int:
        """Recursively remove ad keys from a parsed JSON structure.
        Returns number of keys removed. adSignalsInfo is reset to {} instead
        of deleted - the player expects the key to exist."""
        if depth > 30 or not isinstance(obj, dict):
            return 0
        removed = 0
        for key in cls._AD_KEYS:
            if key == "adSignalsInfo":
                continue
            if key in obj:
                del obj[key]
                removed += 1
        if "adSignalsInfo" in obj:
            obj["adSignalsInfo"] = {}
            removed += 1
        for value in obj.values():
            if isinstance(value, dict):
                removed += cls._strip_ads(value, depth + 1)
            elif isinstance(value, list):
                for item in value:
                    removed += cls._strip_ads(item, depth + 1)
        return removed

    def response(self, flow: http.HTTPFlow) -> None:
        """Rewrite YouTube player/feed responses: strip ad placements from
        the JSON body BEFORE the page ever sees it. Works for fetch, XHR
        and the initial inline player data - nothing for page JS to detect.
        Handles both HTTPS and plain HTTP flows (mitmproxy fires this hook
        for both once the connection is intercepted)."""
        try:
            if not flow.response or flow.response.status_code != 200:
                return
            if not self._adblock_enabled():
                return
            url = flow.request.pretty_url or ""
            content_type = (flow.response.headers.get("content-type") or "").lower()
            if "/youtubei/v1/" in url and "json" in content_type:
                self._rewrite_youtubei_json(flow, url)
            elif "html" in content_type:
                self._rewrite_html(flow, url)
        except Exception as e:
            try:
                ctx.log.debug(f"[BFSB Proxy] response rewrite skipped: {e}")
            except Exception:
                pass

    def _rewrite_youtubei_json(self, flow: http.HTTPFlow, url: str) -> None:
        """Strip ad keys from /youtubei/v1/* JSON bodies (SPA navigation)."""
        body = flow.response.get_text()
        if not body:
            return
        import json as _json
        data = _json.loads(body)
        removed = self._strip_ads(data)
        if removed > 0:
            flow.response.set_text(_json.dumps(data))
            self.cleaned_count += 1
            ctx.log.info(f"[BFSB Proxy] CLEANED {removed} ad keys from {url[:100]}")
            self._log_event(f"CLEANED {removed} ad keys from {url[:120]}")

    # Matches "var ytInitialPlayerResponse = {" / "window.ytInitialData = {"
    # - the embedded player/feed data on hard page loads, which never passes
    # through /youtubei/v1/ and was previously never cleaned.
    _YT_JSON_VAR_RE = re.compile(
        r"(?:var\s+|window\.)?(ytInitialPlayerResponse|ytInitialData)\s*=\s*\{"
    )

    @staticmethod
    def _extract_json_object(text: str, start: int) -> Optional[str]:
        """Extract a balanced {...} object from text starting at `start`
        (the position of the opening brace), respecting string literals and
        escapes. Returns the JSON substring or None if unbalanced."""
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start : i + 1]
        return None

    def _rewrite_html(self, flow: http.HTTPFlow, url: str) -> None:
        """Strip ad placements from ytInitialPlayerResponse/ytInitialData
        embedded in YouTube HTML pages. This kills in-video ads on hard page
        loads (new tab / typed URL) where the player data is inlined in the
        HTML and never travels through /youtubei/v1/. The page never sees the
        ad data at all, so there is nothing for anti-adblock checks to detect."""
        host = (flow.request.pretty_host or "").lower()
        if not (
            host == "youtube.com"
            or host.endswith(".youtube.com")
            or host.endswith(".youtube-nocookie.com")
        ):
            return
        html = flow.response.get_text()
        if not html or "ytInitial" not in html:
            return
        import json as _json

        # Collect matches first, then apply replacements in reverse order so
        # offsets stay valid while the string is rebuilt.
        matches = list(self._YT_JSON_VAR_RE.finditer(html))
        removed_total = 0
        for m in reversed(matches):
            brace_pos = m.end() - 1
            raw = self._extract_json_object(html, brace_pos)
            if not raw:
                continue
            try:
                data = _json.loads(raw)
            except Exception:
                continue
            removed = self._strip_ads(data)
            if removed > 0:
                html = (
                    html[:brace_pos]
                    + _json.dumps(data)
                    + html[brace_pos + len(raw) :]
                )
                removed_total += removed
        if removed_total > 0:
            flow.response.set_text(html)
            self.cleaned_count += 1
            ctx.log.info(f"[BFSB Proxy] CLEANED HTML {removed_total} ad keys from {url[:100]}")
            self._log_event(f"CLEANED HTML {removed_total} ad keys from {url[:120]}")

    @staticmethod
    def _log_event(msg: str) -> None:
        """Append proxy events to a file - survives desktop-entry launches."""
        try:
            from pathlib import Path
            log = Path.home() / ".local" / "share" / "bfsb" / "proxy_events.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            from datetime import datetime
            with open(log, "a") as f:
                f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
        except Exception:
            pass

    def done(self) -> None:
        ctx.log.info(
            f"[BFSB Proxy] Stats - Blocked: {self.blocked_count}, "
            f"Allowed: {self.allowed_count}, Cleaned: {self.cleaned_count}"
        )


addons = [BFSBAdblockAddon()]