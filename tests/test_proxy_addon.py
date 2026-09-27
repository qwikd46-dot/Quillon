import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path


class FakeLog:
    def info(self, message):
        pass

    def debug(self, message):
        pass


class FakeResponse:
    def __init__(self, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.stream = False

    @classmethod
    def make(cls, status_code, content=b"", headers=None):
        return cls(status_code, content, headers)

    def get_text(self):
        return self.content.decode("utf-8", errors="replace")

    def set_text(self, value):
        self.content = value.encode("utf-8")


class FakeHTTPFlow:
    def __init__(self, url, method="GET", headers=None, response=None, content=b""):
        from urllib.parse import urlparse
        parsed = urlparse(url)
        self.request = types.SimpleNamespace(
            pretty_host=parsed.hostname or "",
            pretty_url=url,
            method=method,
            headers=headers or {},
            content=content,
        )
        self.response = response
        self.metadata = {}


def load_addon():
    try:
        return importlib.import_module("bfsb.core.proxy_addon")
    except ModuleNotFoundError:
        package = types.ModuleType("mitmproxy")
        package.http = types.SimpleNamespace(Response=FakeResponse, HTTPFlow=FakeHTTPFlow)
        package.ctx = types.SimpleNamespace(log=FakeLog())
        sys.modules["mitmproxy"] = package
        root = Path(__file__).parents[1]

        def _load_sibling(name, relpath):
            spec = importlib.util.spec_from_file_location(name, root / relpath)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
            return mod

        path = root / "bfsb" / "core" / "proxy_addon.py"
        spec = importlib.util.spec_from_file_location("bfsb_proxy_addon_test", path)
        module = importlib.util.module_from_spec(spec)

        # proxy_addon imports the real filter; stub the package chain so the
        # sibling module loads without pulling in the whole bfsb package.
        # The stubs must stay in place until proxy_addon itself is executed.
        stubbed = ("bfsb", "bfsb.core", "bfsb.core.search")
        saved = {name: sys.modules.get(name) for name in stubbed}
        for name in stubbed:
            stub = types.ModuleType(name)
            stub.__path__ = []
            sys.modules[name] = stub
        try:
            safety = _load_sibling(
                "bfsb.core.search.safety", "bfsb/core/search/safety.py"
            )
            sys.modules["bfsb.core.search"].safety = safety
            filter_module = _load_sibling(
                "bfsb.core.youtube_filter", "bfsb/core/youtube_filter.py"
            )
            sys.modules["bfsb.core"].youtube_filter = filter_module
            spec.loader.exec_module(module)
        finally:
            for name, previous in saved.items():
                if previous is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous
        return module


module = load_addon()


class ProxyAddonTests(unittest.TestCase):
    def setUp(self):
        self.addon = module.BFSBAdblockAddon()

    def test_typed_fake_responses(self):
        cases = [
            ("https://youtube.com/pagead/ad.js", "application/javascript", b""),
            ("https://youtube.com/pixel.gif", "image/gif", module._EMPTY_GIF),
            ("https://youtube.com/pixel.png", "image/png", module._EMPTY_PNG),
            ("https://youtube.com/pixel.jpg", "image/jpeg", module._EMPTY_JPEG),
            ("https://youtube.com/pixel.webp", "image/webp", module._EMPTY_WEBP),
            ("https://redirector.googlevideo.com/videoplayback?adformat=42", "video/mp4", b""),
            ("https://redirector.googlevideo.com/segment.webm?adformat=43", "video/webm", b""),
            ("https://youtube.com/youtubei/v1/log_event", "application/json", b"{}"),
            ("https://www.youtube.com/api/stats/ads", "application/json", b"{}"),
        ]
        for url, content_type, body in cases:
            with self.subTest(url=url):
                flow = FakeHTTPFlow(url)
                response = self.addon._fake_response(flow, self.addon._request_kind(flow))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Content-Type"], content_type)
                self.assertEqual(int(response.headers["Content-Length"]), len(body))
                self.assertEqual(response.content, body)

    def test_head_has_no_body(self):
        flow = FakeHTTPFlow("https://youtube.com/pagead/ad.js", method="HEAD")
        response = self.addon._fake_response(flow, self.addon._request_kind(flow))
        self.assertEqual(response.content, b"")

    def test_query_rules_are_structured(self):
        rule = self.addon._match_rule("youtube.com", "https://youtube.com/videoplayback?adformat=42")
        self.assertEqual(rule, "query:adformat")
        self.assertIsNone(self.addon._match_rule("youtube.com", "https://youtube.com/videoplayback?range=1"))
        self.assertIsNone(self.addon._match_rule("youtube.com", "https://youtube.com/videoplayback?ctier=SH"))
        self.assertEqual(self.addon._match_rule("youtube.com", "https://youtube.com/videoplayback?ctier=SA"), "query:ctier")
        self.assertEqual(self.addon._match_rule("youtube.com", "https://youtube.com/videoplayback?ctier=SR"), "query:ctier")

    def test_player_request_adds_no_ad_context(self):
        body = json.dumps({
            "videoId": "abc",
            "playbackContext": {
                "contentPlaybackContext": {"html5Preference": "HTML5_PREF_WANTS"},
            },
        }).encode("utf-8")
        flow = FakeHTTPFlow(
            "https://www.youtube.com/youtubei/v1/player?prettyPrint=false",
            method="POST",
            content=body,
        )
        self.addon.request(flow)
        payload = json.loads(flow.request.content)
        self.assertTrue(payload["playbackContext"]["contentPlaybackContext"]["isInlinePlaybackNoAd"])
        self.assertEqual(payload["params"], "yAEB")
        self.assertEqual(flow.request.headers["Content-Length"], str(len(flow.request.content)))
        self.assertTrue(flow.metadata["bfsb_no_ad_context"])

    def test_shorts_request_is_not_rewritten(self):
        body = json.dumps({
            "videoId": "short",
            "params": "short-params",
            "playbackContext": {
                "contentPlaybackContext": {"html5Preference": "HTML5_PREF_WANTS"},
            },
        }).encode("utf-8")
        flow = FakeHTTPFlow(
            "https://www.youtube.com/youtubei/v1/reel/reel_watch_sequence",
            method="POST",
            content=body,
        )
        self.addon.request(flow)
        self.assertEqual(flow.request.content, body)
        self.assertNotIn("bfsb_no_ad_context", flow.metadata)
        self.assertTrue(flow.metadata["bfsb_shorts_passthrough"])

    def test_shorts_player_request_is_not_rewritten(self):
        body = json.dumps({
            "videoId": "short",
            "params": "CAUwAg%3D%3D",
            "context": {
                "client": {
                    "mainAppWebInfo": {"graftUrl": "https://www.youtube.com/shorts/short"},
                },
            },
            "playbackContext": {
                "contentPlaybackContext": {"html5Preference": "HTML5_PREF_WANTS"},
            },
        }).encode("utf-8")
        flow = FakeHTTPFlow(
            "https://www.youtube.com/youtubei/v1/player",
            method="POST",
            headers={"Referer": "https://www.youtube.com/shorts/short"},
            content=body,
        )
        self.addon.request(flow)
        self.assertEqual(flow.request.content, body)
        self.assertTrue(flow.metadata["bfsb_shorts_passthrough"])

    def test_shorts_player_response_preserves_streaming_url(self):
        flow = FakeHTTPFlow(
            "https://www.youtube.com/youtubei/v1/player",
            response=FakeResponse(
                200,
                b'{"streamingData":{"serverAbrStreamingUrl":"https://sabr.example"},"adPlacements":[]}',
                {"content-type": "application/json"},
            ),
        )
        flow.metadata["bfsb_shorts_passthrough"] = True
        self.addon.responseheaders(flow)
        self.addon.response(flow)
        payload = json.loads(flow.response.get_text())
        self.assertEqual(payload["streamingData"]["serverAbrStreamingUrl"], "https://sabr.example")

    def test_player_response_strips_backoff(self):
        flow = FakeHTTPFlow(
            "https://www.youtube.com/youtubei/v1/player",
            response=FakeResponse(
                200,
                b'{"streamingData":{"formats":[{"url":"x","backoffTimeMs":12000}],"serverAbrStreamingUrl":"https://sabr.example"},"adPlacements":[]}',
                {"content-type": "application/json"},
            ),
        )
        self.addon.responseheaders(flow)
        self.addon.response(flow)
        payload = json.loads(flow.response.get_text())
        self.assertEqual(payload, {"streamingData": {"formats": [{"url": "x"}]}})

    def test_player_response_removes_sabr_without_ad_metadata(self):
        flow = FakeHTTPFlow(
            "https://www.youtube.com/youtubei/v1/player",
            response=FakeResponse(
                200,
                b'{"streamingData":{"serverAbrStreamingUrl":"https://sabr.example","formats":[]}}',
                {"content-type": "application/json"},
            ),
        )
        self.addon.responseheaders(flow)
        self.addon.response(flow)
        self.assertEqual(json.loads(flow.response.get_text()), {"streamingData": {"formats": []}})

    def test_shorts_response_removes_ad_entries(self):
        flow = FakeHTTPFlow(
            "https://www.youtube.com/youtubei/v1/reel/reel_watch_sequence",
            response=FakeResponse(
                200,
                b'{"streamingData":{"serverAbrStreamingUrl":"https://sabr.example"},"items":[{"videoType":"REEL_VIDEO_TYPE_AD","videoId":"ad"},{"videoType":"REEL_VIDEO_TYPE_CONTENT","videoId":"short"}]}',
                {"content-type": "application/json"},
            ),
        )
        self.addon.responseheaders(flow)
        self.addon.response(flow)
        payload = json.loads(flow.response.get_text())
        self.assertEqual(payload, {
            "streamingData": {"serverAbrStreamingUrl": "https://sabr.example"},
            "items": [{"videoType": "REEL_VIDEO_TYPE_CONTENT", "videoId": "short"}],
        })

    def test_shorts_html_preserves_streaming_url(self):
        flow = FakeHTTPFlow(
            "https://www.youtube.com/shorts/short",
            response=FakeResponse(
                200,
                b'<script>var ytInitialPlayerResponse = {"streamingData":{"serverAbrStreamingUrl":"https://sabr.example"},"adPlacements":[]};</script>',
                {"content-type": "text/html"},
            ),
        )
        self.addon.responseheaders(flow)
        self.addon.response(flow)
        self.assertIn(b"serverAbrStreamingUrl", flow.response.content)
        self.assertNotIn(b"adPlacements", flow.response.content)

    def test_allowed_domain_is_not_faked(self):
        self.assertFalse(self.addon._should_block("www.youtube.com", "https://www.youtube.com/watch?v=abc"))
        self.assertFalse(self.addon._is_youtube_host("m.youtube.com"))

    def test_streaming_policy(self):
        passthrough = FakeHTTPFlow("https://youtube.com/stream", response=FakeResponse(200, b"chunk", {"content-type": "text/plain"}))
        self.addon.responseheaders(passthrough)
        self.assertTrue(passthrough.response.stream)
        rewrite = FakeHTTPFlow("https://youtube.com/youtubei/v1/player", response=FakeResponse(200, b"{}", {"content-type": "application/json"}))
        self.addon.responseheaders(rewrite)
        self.assertFalse(rewrite.response.stream)

    def test_json_rewrite(self):
        flow = FakeHTTPFlow("https://youtube.com/youtubei/v1/player", response=FakeResponse(200, b'{"adPlacements":[],"videoId":"x"}', {"content-type": "application/json"}))
        self.addon.response(flow)
        self.assertEqual(json.loads(flow.response.get_text()), {"videoId": "x"})

    def test_safe_log_url_drops_query(self):
        self.assertEqual(
            self.addon._safe_log_url("https://user:password@youtube.com/path?token=secret"),
            "https://youtube.com/path",
        )


class YouTubeStrictFilterTests(unittest.TestCase):
    def setUp(self):
        self.addon = module.BFSBAdblockAddon()

    @staticmethod
    def _reel(title):
        return {
            "reelItemRenderer": {
                "videoId": "abc123",
                "headline": {"simpleText": title},
            }
        }

    def test_search_query_with_blocked_term_is_blocked(self):
        flow = FakeHTTPFlow("https://www.youtube.com/results?search_query=hot+porn+video")
        self.addon.request(flow)
        self.assertTrue(flow.metadata.get("bfsb_fake"))
        self.assertEqual(flow.response.status_code, 200)

    def test_clean_search_query_is_not_blocked(self):
        flow = FakeHTTPFlow("https://www.youtube.com/results?search_query=python+tutorial")
        self.addon.request(flow)
        self.assertFalse(flow.metadata.get("bfsb_fake"))

    def test_json_search_body_with_blocked_term_is_blocked(self):
        body = json.dumps({"query": "onlyfans leak"}).encode("utf-8")
        flow = FakeHTTPFlow(
            "https://www.youtube.com/youtubei/v1/search?prettyPrint=false",
            method="POST",
            content=body,
        )
        self.addon.request(flow)
        self.assertTrue(flow.metadata.get("bfsb_fake"))

    def test_json_search_body_is_unstreamed_so_it_can_be_read(self):
        for url in (
            "https://www.youtube.com/youtubei/v1/search",
            "https://www.youtube.com/youtubei/v1/search?prettyPrint=false",
            "https://www.youtube.com/youtubei/v1/next",
            "https://www.youtube.com/results",
        ):
            with self.subTest(url=url):
                flow = FakeHTTPFlow(url, method="POST", content=b"{}")
                flow.request.stream = True
                self.addon.requestheaders(flow)
                self.assertFalse(flow.request.stream)

    def test_non_search_request_is_left_streaming(self):
        flow = FakeHTTPFlow("https://www.youtube.com/youtubei/v1/player", method="POST", content=b"{}")
        flow.request.stream = True
        self.addon.requestheaders(flow)
        self.assertTrue(flow.request.stream)

    def test_shorts_feed_items_are_removed_from_json(self):
        payload = json.dumps({"items": [
            {"videoType": "REEL_VIDEO_TYPE_CONTENT"},
            self._reel("cute dog"),
            self._reel("sexy bikini girl"),
        ]}).encode("utf-8")
        flow = FakeHTTPFlow(
            "https://www.youtube.com/youtubei/v1/reel/reel_watch_sequence",
            response=FakeResponse(200, payload, {"content-type": "application/json"}),
        )
        self.addon.responseheaders(flow)
        self.addon.response(flow)
        items = json.loads(flow.response.get_text())["items"]
        titles = [
            item["reelItemRenderer"]["headline"]["simpleText"]
            for item in items if "reelItemRenderer" in item
        ]
        self.assertEqual(titles, ["cute dog"])

    def test_shorts_feed_items_are_removed_from_html(self):
        data = json.dumps({"contents": [self._reel("topless webcam")]})
        body = f'<script>var ytInitialData = {data};</script>'.encode("utf-8")
        flow = FakeHTTPFlow(
            "https://www.youtube.com/shorts/",
            response=FakeResponse(200, body, {"content-type": "text/html"}),
        )
        self.addon.responseheaders(flow)
        self.addon.response(flow)
        self.assertNotIn(b"topless", flow.response.content)

    def test_filter_survives_adblock_being_disabled(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "adblock_state"
            state.write_text("0", encoding="utf-8")
            self.addon._state_path = str(state)
            self.addon._state_mtime = None

            payload = json.dumps({"items": [self._reel("sexy bikini girl")]}).encode("utf-8")
            flow = FakeHTTPFlow(
                "https://www.youtube.com/youtubei/v1/reel/reel_watch_sequence",
                response=FakeResponse(200, payload, {"content-type": "application/json"}),
            )
            self.addon.responseheaders(flow)
            self.addon.response(flow)
            self.assertNotIn(b"bikini", flow.response.content)

            search = FakeHTTPFlow("https://www.youtube.com/results?search_query=porn")
            self.addon.request(search)
            self.assertTrue(search.metadata.get("bfsb_fake"))

    def test_ads_are_kept_when_adblock_is_disabled(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "adblock_state"
            state.write_text("0", encoding="utf-8")
            self.addon._state_path = str(state)
            self.addon._state_mtime = None

            flow = FakeHTTPFlow(
                "https://www.youtube.com/youtubei/v1/player",
                response=FakeResponse(
                    200,
                    b'{"videoId":"x","adPlacements":[]}',
                    {"content-type": "application/json"},
                ),
            )
            self.addon.responseheaders(flow)
            self.addon.response(flow)
            self.assertIn("adPlacements", json.loads(flow.response.get_text()))

    def test_filter_can_be_disabled_by_env(self):
        import os

        previous = os.environ.get("BFSB_YOUTUBE_STRICT_FILTER")
        os.environ["BFSB_YOUTUBE_STRICT_FILTER"] = "0"
        try:
            payload = json.dumps({"items": [self._reel("sexy bikini girl")]}).encode("utf-8")
            flow = FakeHTTPFlow(
                "https://www.youtube.com/youtubei/v1/reel/reel_watch_sequence",
                response=FakeResponse(200, payload, {"content-type": "application/json"}),
            )
            self.addon.responseheaders(flow)
            self.addon.response(flow)
            self.assertIn(b"bikini", flow.response.content)

            search = FakeHTTPFlow("https://www.youtube.com/results?search_query=porn")
            self.addon.request(search)
            self.assertFalse(search.metadata.get("bfsb_fake"))
        finally:
            if previous is None:
                os.environ.pop("BFSB_YOUTUBE_STRICT_FILTER", None)
            else:
                os.environ["BFSB_YOUTUBE_STRICT_FILTER"] = previous


if __name__ == "__main__":
    unittest.main()
