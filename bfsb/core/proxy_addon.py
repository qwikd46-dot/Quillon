"""BFSB mitmproxy addon for typed ad responses and streaming passthrough."""

from __future__ import annotations

import base64
import json
import os
import queue
import re
import sys
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from mitmproxy import ctx, http

# mitmdump loads this file as a standalone script via -s, so the repo root
# is NOT on sys.path and "from bfsb..." raises ModuleNotFoundError. The
# addon then dies during startup, nothing is listening on the proxy port,
# and the browser reports ERR_PROXY_CONNECTION_FAILED. Nothing said so
# because the proxy was started with stderr sent to /dev/null.
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from bfsb.core import youtube_filter


_EMPTY_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")
_EMPTY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_EMPTY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABD/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/EH//xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/EH//2Q=="
)
_EMPTY_WEBP = base64.b64decode("UklGRiIAAABXRUJQVlA4IBgAAAAwAQCdASoBAAEAAUAmJaQAA3AA/v89WAAAAA==")
_NO_AD_PARAMS = "yAEB"


class BFSBAdblockAddon:
    HARD_BLOCK_DOMAINS = frozenset({
        "doubleclick.net", "googlesyndication.com", "googleadservices.com",
        "google-analytics.com", "googletagmanager.com", "googletagservices.com",
        "adservice.google.com", "pagead2.googlesyndication.com",
        "tpc.googlesyndication.com", "securepubads.g.doubleclick.net",
        "pubads.g.doubleclick.net", "ad.doubleclick.net", "ads.youtube.com",
        "ad.youtube.com", "s.youtube.com", "adnxs.com", "adsrvr.org",
        "criteo.com", "criteo.net", "outbrain.com", "taboola.com",
        "scorecardresearch.com", "quantserve.com", "moatads.com",
        "amazon-adsystem.com", "adroll.com", "bidswitch.net", "casalemedia.com",
        "rubiconproject.com", "openx.net", "smartadserver.com", "zedo.com",
        "adform.net", "yieldmo.com", "sharethrough.com", "spotxchange.com",
        "teads.tv", "undertone.com", "w55c.net", "popads.net",
        "connect.facebook.net", "graph.facebook.com", "tr.facebook.com",
        "graph.instagram.com", "i.instagram.com", "analytics.x.com",
        "ads-api.x.com", "business-api.tiktok.com", "log.byteoversea.com",
        "pixel.quora.com", "qevents.quora.com", "rudderstack.com",
        "snowplowanalytics.com", "fingerprintjs.com", "bnc.lt",
        "tagmanager.google.com", "app.posthog.com", "eu.posthog.com",
        "us.i.posthog.com", "mixpanel.com", "segment.io", "segment.com",
        "amplitude.com", "heap.io", "fullstory.com", "hotjar.com",
        "mouseflow.com", "inspectlet.com", "crazyegg.com", "luckyorange.com",
        "fpjs.io", "clientstream.launchdarkly.com", "api.onesignal.com",
        "widget.intercom.io", "js.drifft.com", "click.mailchimp.com",
        "dai.google.com", "ssl.p.jwpcdn.com", "pangleglobal.com",
        "metrika.yandex.ru", "appmetrica.yandex.ru", "ironsource.mobi",
        "liftoff.io", "advertising-api-eu.amazon.com", "mads-eu.amazon.com",
        "udcm.yahoo.com", "log.fc.yahoo.com", "privacyportal.onetrust.com",
        "consent.cookiebot.com", "consentcdn.cookiebot.com", "cookiebot.com",
        "consent.trustarc.com", "sdk.privacy-center.org", "cdn.privacy-mgmt.com",
        "app.usercentrics.eu", "cmp.osano.com", "coinhive.com", "coin-hive.com",
        "jsecoin.com", "minero.cc", "webmine.cz", "xmrwebmine.com",
        "cryptoloot.pro", "deepminer.com", "authedmine.com",
    })
    HARD_BLOCK_PATTERNS = (
        "/api/stats/ads", "/api/stats/qoe", "/api/stats/att", "/pagead/",
        "/ptracking", "/pcs/activeview", "/get_midroll", "/get_midroll_info",
        "/youtubei/v1/log_event", "/youtubei/v1/log_interaction",
    )
    QUERY_BLOCK_KEYS = frozenset({"adformat", "ad_type", "afv", "ad_device"})
    QUERY_BLOCK_VALUES = {"ctier": frozenset({"SA", "SR", "L"})}
    ALLOWED_DOMAINS = frozenset({
        "fonts.googleapis.com", "fonts.gstatic.com", "cdn.jsdelivr.net",
        "cdnjs.cloudflare.com", "unpkg.com", "code.jquery.com",
        "ajax.googleapis.com", "maps.googleapis.com", "maps.gstatic.com",
        "www.google.com", "www.gstatic.com", "www.recaptcha.net",
        "www.youtube.com", "www.youtube-nocookie.com", "i.ytimg.com",
        "github.githubassets.com", "avatars.githubusercontent.com",
        "js.stripe.com", "m.stripe.network", "maxcdn.bootstrapcdn.com",
        "use.fontawesome.com", "ajax.aspnetcdn.com",
    })
    _AD_KEYS = (
        "adPlacements", "adSlots", "adSchedules", "adBreaks", "adSignalsInfo",
        "playerAds", "instreamAds", "bumperAds", "midrolls", "prerolls",
        "postrolls", "adTagUrl", "adTagUrlList", "adParameters", "adModule",
        "adSlotRenderer", "promotedSparklesWebRenderer", "infeedAdRenderer",
        "displayAdRenderer", "videoMastheadAdRenderer", "mealbarPromoRenderer",
        "bannerPromoRenderer", "adBreakHeartbeatParams", "backoffTimeMs",
        "backoffTime", "backoffMs",
    )
    _YT_JSON_VAR_RE = re.compile(
        r"(?:var\s+|window\.)?(ytInitialPlayerResponse|ytInitialData)\s*=\s*\{"
    )

    def __init__(self):
        self.blocked_count = 0
        self.allowed_count = 0
        self.cleaned_count = 0
        self._state_path = str(Path.home() / ".bfsb" / "adblock_state")
        self._state_mtime: Optional[float] = None
        self._state_enabled = True
        self._event_log_path = Path.home() / ".local" / "share" / "bfsb" / "proxy_events.log"
        self._event_log_queue: queue.Queue[str] = queue.Queue(maxsize=256)
        self._event_log_thread = threading.Thread(target=self._drain_event_log, daemon=True)
        self._event_log_thread.start()

    def _adblock_enabled(self) -> bool:
        try:
            mtime = os.stat(self._state_path).st_mtime
            if mtime != self._state_mtime:
                self._state_mtime = mtime
                with open(self._state_path) as handle:
                    self._state_enabled = handle.read().strip() != "0"
            return self._state_enabled
        except FileNotFoundError:
            return True
        except Exception:
            return True

    def _match_rule(self, host: str, url: str) -> Optional[str]:
        normalized_host = (host or "").lower().rstrip(".")
        parsed = urlparse(url or "")
        path = unquote(parsed.path or "").lower()
        query = parse_qs(parsed.query or "", keep_blank_values=True)
        if normalized_host in ("localhost", "127.0.0.1", "::1") or normalized_host.endswith(".local"):
            return None
        for pattern in self.HARD_BLOCK_PATTERNS:
            if pattern in path:
                return pattern
        for key in self.QUERY_BLOCK_KEYS:
            if key in query:
                return f"query:{key}"
        for key, blocked_values in self.QUERY_BLOCK_VALUES.items():
            if any(str(value).upper() in blocked_values for value in query.get(key, [])):
                return f"query:{key}"
        for blocked in self.HARD_BLOCK_DOMAINS:
            if normalized_host == blocked or normalized_host.endswith("." + blocked):
                return f"domain:{blocked}"
        for allowed in self.ALLOWED_DOMAINS:
            if normalized_host == allowed or normalized_host.endswith("." + allowed):
                return None
        return None

    def _should_block(self, host: str, url: str) -> bool:
        return self._match_rule(host, url) is not None

    def _request_kind(self, flow: http.HTTPFlow) -> Optional[str]:
        parsed = urlparse(flow.request.pretty_url or "")
        path = unquote(parsed.path or "").lower()
        headers = {str(key).lower(): str(value).lower() for key, value in flow.request.headers.items()}
        accept = headers.get("accept", "")
        if path.endswith(".js") or "javascript" in accept:
            return "javascript"
        if path.endswith(".gif"):
            return "gif"
        if path.endswith(".png"):
            return "png"
        if path.endswith((".jpg", ".jpeg")):
            return "jpeg"
        if path.endswith(".webp"):
            return "webp"
        if "image/" in accept:
            return "image"
        if path.endswith(".webm"):
            return "webm"
        if path.endswith((".mp4", ".m4s")) or "video/" in accept:
            return "video"
        if "adformat" in parse_qs(parsed.query, keep_blank_values=True):
            return "video"
        if path.startswith("/api/stats/") or "/youtubei/" in path or "json" in accept:
            return "json"
        return None

    @classmethod
    def _mark_inline_no_ad_context(cls, value) -> bool:
        if isinstance(value, dict):
            content_context = value.get("contentPlaybackContext")
            if isinstance(content_context, dict):
                content_context["isInlinePlaybackNoAd"] = True
                return True
            for nested in value.values():
                if cls._mark_inline_no_ad_context(nested):
                    return True
        elif isinstance(value, list):
            for nested in value:
                if cls._mark_inline_no_ad_context(nested):
                    return True
        return False

    def _rewrite_youtube_player_request(self, flow: http.HTTPFlow) -> bool:
        host = (flow.request.pretty_host or "").lower().rstrip(".")
        path = urlparse(flow.request.pretty_url or "").path.rstrip("/").lower()
        if not self._is_youtube_host(host):
            return False
        if path == "/youtubei/v1/reel/reel_watch_sequence":
            flow.metadata["bfsb_shorts_passthrough"] = True
            self._log_event(
                f"SHORTS_REQUEST_PASSTHROUGH url={self._safe_log_url(flow.request.pretty_url)}"
            )
            return False
        if path != "/youtubei/v1/player":
            return False
        if flow.request.method.upper() not in ("POST", "PUT"):
            return False
        try:
            raw = flow.request.content
        except Exception:
            raw = b""
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if not raw:
            return False
        try:
            data = json.loads(raw.decode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError):
            return False
        if not isinstance(data, dict):
            return False
        if self._is_shorts_player_request(flow, data):
            flow.metadata["bfsb_shorts_passthrough"] = True
            self._log_event(
                f"SHORTS_REQUEST_PASSTHROUGH url={self._safe_log_url(flow.request.pretty_url)}"
            )
            return False
        if not self._mark_inline_no_ad_context(data):
            return False
        params = data.get("params")
        if isinstance(params, str) and params:
            if not params.startswith(_NO_AD_PARAMS):
                data["params"] = _NO_AD_PARAMS + params
        else:
            data["params"] = _NO_AD_PARAMS
        body = json.dumps(data, separators=(",", ":")).encode("utf-8")
        try:
            flow.request.content = body
        except Exception:
            try:
                flow.request.raw_content = body
            except Exception:
                return False
        try:
            flow.request.headers["Content-Length"] = str(len(body))
        except Exception:
            pass
        flow.metadata["bfsb_no_ad_context"] = True
        self._log_event(f"NO_AD_CONTEXT url={self._safe_log_url(flow.request.pretty_url)}")
        return True

    def _fake_response(self, flow: http.HTTPFlow, kind: Optional[str]) -> http.Response:
        if flow.request.method == "HEAD":
            body = b""
        elif kind == "javascript":
            body = b""
        elif kind == "gif":
            body = _EMPTY_GIF
        elif kind == "png":
            body = _EMPTY_PNG
        elif kind == "jpeg":
            body = _EMPTY_JPEG
        elif kind == "webp":
            body = _EMPTY_WEBP
        elif kind == "image":
            body = _EMPTY_PNG
        elif kind in ("video", "webm"):
            body = b""
        elif kind == "json":
            body = b"{}"
        else:
            body = b""
        content_type = {
            "javascript": "application/javascript",
            "gif": "image/gif",
            "png": "image/png",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
            "image": "image/png",
            "video": "video/mp4",
            "webm": "video/webm",
            "json": "application/json",
        }.get(kind, "text/plain")
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
            "Connection": "close",
        }
        origin = flow.request.headers.get("Origin")
        if origin:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Vary"] = "Origin"
        else:
            headers["Access-Control-Allow-Origin"] = "*"
        return http.Response.make(200, body, headers)

    @staticmethod
    def _safe_log_url(url: str) -> str:
        parsed = urlparse(url or "")
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = f":{parsed.port}" if parsed.port else ""
        except ValueError:
            port = ""
        return f"{parsed.scheme}://{host}{port}{parsed.path}"

    @staticmethod
    def _is_youtube_host(host: str) -> bool:
        normalized = (host or "").lower().rstrip(".")
        return normalized in {"youtube.com", "www.youtube.com"}

    @classmethod
    def _is_shorts_player_request(cls, flow: http.HTTPFlow, data: dict) -> bool:
        referer = flow.request.headers.get("Referer", "")
        if "/shorts/" in unquote(urlparse(referer).path or "").lower():
            return True
        context = data.get("context")
        if isinstance(context, dict):
            client = context.get("client")
            if isinstance(client, dict):
                web_info = client.get("mainAppWebInfo")
                if isinstance(web_info, dict):
                    graft_url = str(web_info.get("graftUrl", ""))
                    if "/shorts/" in graft_url.lower():
                        return True
        params = unquote(str(data.get("params", "")))
        return bool(data.get("videoId")) and params.startswith("CA") and len(params) <= 64

    @classmethod
    def _is_shorts_sequence(cls, flow: http.HTTPFlow) -> bool:
        host = (flow.request.pretty_host or "").lower().rstrip(".")
        path = urlparse(flow.request.pretty_url or "").path.rstrip("/").lower()
        return cls._is_youtube_host(host) and path == "/youtubei/v1/reel/reel_watch_sequence"

    @classmethod
    def _is_shorts_flow(cls, flow: http.HTTPFlow) -> bool:
        return cls._is_shorts_sequence(flow) or bool(flow.metadata.get("bfsb_shorts_passthrough"))

    def _is_rewrite_flow(self, flow: http.HTTPFlow) -> bool:
        if not flow.response:
            return False
        host = (flow.request.pretty_host or "").lower()
        path = unquote(urlparse(flow.request.pretty_url or "").path or "").lower()
        content_type = (flow.response.headers.get("content-type") or "").lower()
        return self._is_youtube_host(host) and (path.startswith("/youtubei/") or "html" in content_type)

    def requestheaders(self, flow: http.HTTPFlow) -> None:
        if not flow.request or not flow.request.pretty_host:
            return
        if self._is_youtube_search_request(flow) and youtube_filter.enabled():
            # The query text lives in the POST body, which must be buffered
            # before the strict filter can read it.
            flow.request.stream = False
        if self._adblock_enabled() and self._match_rule(flow.request.pretty_host, flow.request.pretty_url):
            flow.request.stream = False

    @staticmethod
    def _is_youtube_search_request(flow: http.HTTPFlow) -> bool:
        host = (getattr(flow.request, "pretty_host", "") or "").lower().rstrip(".")
        if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            return False
        url = getattr(flow.request, "pretty_url", "") or ""
        path = unquote(urlparse(url).path or "").lower().rstrip("/")
        return path in {"/results", "/feed/search"} or path.startswith(
            ("/youtubei/v1/search", "/youtubei/v1/next")
        )

    @staticmethod
    def _youtube_search_path(flow: http.HTTPFlow) -> Optional[str]:
        """Return the YouTube search query in this request, if any."""
        url = getattr(flow.request, "pretty_url", "") or ""
        params = parse_qs(urlparse(url).query or "")
        for key in ("search_query", "q"):
            values = params.get(key)
            if values and values[0].strip():
                return values[0].strip()
        path = unquote(urlparse(url).path or "").lower().rstrip("/")
        if path.startswith("/youtubei/v1/search") or path.startswith("/youtubei/v1/next"):
            body = getattr(flow.request, "content", None)
            if body:
                try:
                    payload = json.loads(bytes(body).decode("utf-8", errors="replace"))
                except Exception:
                    return None
                query = payload.get("query") if isinstance(payload, dict) else None
                if isinstance(query, str) and query.strip():
                    return query.strip()
        return None

    def request(self, flow: http.HTTPFlow) -> None:
        if not flow.request or not flow.request.pretty_host:
            return
        # Content filtering is independent of the adblock toggle.
        query = self._youtube_search_path(flow) if youtube_filter.enabled() else None
        if query:
            hit = youtube_filter.matched_term(query)
            if hit:
                self.blocked_count += 1
                safe_url = self._safe_log_url(flow.request.pretty_url)
                self._log_event(f"FILTERED query term={hit} url={safe_url}")
                flow.response = self._fake_response(flow, self._request_kind(flow))
                try:
                    flow.metadata["bfsb_fake"] = True
                except Exception:
                    pass
                return
        if not self._adblock_enabled():
            self.allowed_count += 1
            return
        self._rewrite_youtube_player_request(flow)
        host = flow.request.pretty_host
        url = flow.request.pretty_url
        rule = self._match_rule(host, url)
        if rule is not None:
            self.blocked_count += 1
            safe_url = self._safe_log_url(url)
            self._log_event(f"BLOCKED rule={rule} url={safe_url}")
            flow.response = self._fake_response(flow, self._request_kind(flow))
            try:
                flow.metadata["bfsb_fake"] = True
            except Exception:
                pass
        else:
            self.allowed_count += 1

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        if not flow.response or flow.response.status_code == 101:
            return
        try:
            fake = bool(flow.metadata.get("bfsb_fake"))
        except Exception:
            fake = False
        if fake or self._is_rewrite_flow(flow):
            flow.response.stream = False
        else:
            flow.response.stream = True

    def response(self, flow: http.HTTPFlow) -> None:
        try:
            if not flow.response or flow.response.status_code != 200:
                return
            if getattr(flow.response, "stream", False):
                return
            try:
                if flow.metadata.get("bfsb_fake"):
                    return
            except Exception:
                pass
            if not self._is_rewrite_flow(flow):
                return
            strip_ads = self._adblock_enabled()
            content_type = (flow.response.headers.get("content-type") or "").lower()
            if "json" in content_type:
                self._rewrite_youtubei_json(flow, strip_ads=strip_ads)
            elif "html" in content_type:
                self._rewrite_html(flow, strip_ads=strip_ads)
        except Exception as exc:
            try:
                ctx.log.debug(f"[BFSB Proxy] response rewrite skipped: {type(exc).__name__}")
            except Exception:
                pass

    @classmethod
    def _remove_server_abr_url(cls, obj, depth: int = 0) -> int:
        if depth > 30 or not isinstance(obj, dict):
            return 0
        removed = 0
        streaming_data = obj.get("streamingData")
        if isinstance(streaming_data, dict) and "serverAbrStreamingUrl" in streaming_data:
            del streaming_data["serverAbrStreamingUrl"]
            removed += 1
        for value in obj.values():
            if isinstance(value, dict):
                removed += cls._remove_server_abr_url(value, depth + 1)
            elif isinstance(value, list):
                for item in value:
                    removed += cls._remove_server_abr_url(item, depth + 1)
        return removed

    @staticmethod
    def _is_ad_object(value) -> bool:
        if not isinstance(value, dict):
            return False
        if str(value.get("videoType", "")).upper() == "REEL_VIDEO_TYPE_AD":
            return True
        ad_params = value.get("adClientParams")
        return isinstance(ad_params, dict) and str(ad_params.get("isAd", "")).lower() == "true"

    @classmethod
    def _strip_ads(cls, obj, depth: int = 0) -> int:
        if depth > 30:
            return 0
        if isinstance(obj, list):
            removed = 0
            for index in range(len(obj) - 1, -1, -1):
                item = obj[index]
                if cls._is_ad_object(item):
                    del obj[index]
                    removed += 1
                else:
                    removed += cls._strip_ads(item, depth + 1)
            return removed
        if not isinstance(obj, dict):
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
            if isinstance(value, (dict, list)):
                removed += cls._strip_ads(value, depth + 1)
        return removed

    def _rewrite_youtubei_json(self, flow: http.HTTPFlow, strip_ads: bool = True) -> None:
        body = flow.response.get_text()
        if not body:
            return
        import json
        data = json.loads(body)
        removed = self._strip_ads(data) if strip_ads else 0
        if not self._is_shorts_flow(flow):
            removed += self._remove_server_abr_url(data)
        blocked_terms: list[str] = []
        if youtube_filter.enabled():
            dropped, blocked_terms = youtube_filter.filter_tree(data)
            removed += dropped
        if removed > 0:
            flow.response.set_text(json.dumps(data))
            self.cleaned_count += 1
            safe_url = self._safe_log_url(flow.request.pretty_url)
            detail = f" blocked_terms={','.join(blocked_terms)}" if blocked_terms else ""
            self._log_event(f"CLEANED {removed} keys url={safe_url}{detail}")

    @staticmethod
    def _extract_json_object(text: str, start: int) -> Optional[str]:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]
        return None

    def _rewrite_html(self, flow: http.HTTPFlow, strip_ads: bool = True) -> None:
        host = (flow.request.pretty_host or "").lower()
        if not self._is_youtube_host(host):
            return
        body = flow.response.get_text()
        if not body or "ytInitial" not in body:
            return
        import json
        matches = list(self._YT_JSON_VAR_RE.finditer(body))
        request_path = unquote(urlparse(flow.request.pretty_url or "").path or "").lower()
        preserve_shorts_stream = self._is_shorts_flow(flow) or "/shorts/" in request_path
        removed_total = 0
        blocked_terms: list[str] = []
        for match in reversed(matches):
            brace_pos = match.end() - 1
            raw = self._extract_json_object(body, brace_pos)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            removed = self._strip_ads(data) if strip_ads else 0
            if not preserve_shorts_stream:
                removed += self._remove_server_abr_url(data)
            if youtube_filter.enabled():
                dropped, hits = youtube_filter.filter_tree(data)
                removed += dropped
                blocked_terms.extend(t for t in hits if t not in blocked_terms)
            if removed > 0:
                body = body[:brace_pos] + json.dumps(data) + body[brace_pos + len(raw):]
                removed_total += removed
        if removed_total > 0:
            flow.response.set_text(body)
            self.cleaned_count += 1
            safe_url = self._safe_log_url(flow.request.pretty_url)
            detail = f" blocked_terms={','.join(blocked_terms)}" if blocked_terms else ""
            self._log_event(f"CLEANED HTML {removed_total} keys url={safe_url}{detail}")

    def _drain_event_log(self) -> None:
        while True:
            message = self._event_log_queue.get()
            try:
                self._event_log_path.parent.mkdir(parents=True, exist_ok=True)
                from datetime import datetime
                with open(self._event_log_path, "a") as handle:
                    handle.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
            except Exception:
                pass
            finally:
                self._event_log_queue.task_done()

    def _log_event(self, message: str) -> None:
        try:
            self._event_log_queue.put_nowait(message)
        except queue.Full:
            pass

    def done(self) -> None:
        try:
            ctx.log.info(
                f"[BFSB Proxy] Stats - Blocked: {self.blocked_count}, "
                f"Allowed: {self.allowed_count}, Cleaned: {self.cleaned_count}"
            )
        except Exception:
            pass


addons = [BFSBAdblockAddon()]
