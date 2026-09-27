import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _load_with_stubs(name, relpath, siblings):
    """Load a bfsb submodule standalone, stubbing the package chain.

    Importing through the real ``bfsb`` package pulls in the whole
    runtime (cryptography, PyQt6, ...), so the parent packages are
    replaced with empty stubs and the needed siblings are pre-loaded.
    """
    stubbed = ("bfsb", "bfsb.core", "bfsb.core.search")
    saved = {n: sys.modules.get(n) for n in stubbed}
    for n in stubbed:
        stub = types.ModuleType(n)
        stub.__path__ = []
        sys.modules[n] = stub
    try:
        loaded = {}
        for sibling_name, sibling_path in siblings:
            spec = importlib.util.spec_from_file_location(sibling_name, ROOT / sibling_path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[sibling_name] = mod
            spec.loader.exec_module(mod)
            loaded[sibling_name] = mod
        setattr(sys.modules["bfsb.core.search"], name.rsplit(".", 1)[-1], loaded.get(name))
        spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for n, previous in saved.items():
            if previous is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = previous


safety = _load_with_stubs(
    "bfsb.core.search.safety",
    "bfsb/core/search/safety.py",
    [],
)
yf = _load_with_stubs(
    "bfsb_youtube_filter_test",
    "bfsb/core/youtube_filter.py",
    [("bfsb.core.search.safety", "bfsb/core/search/safety.py")],
)


def reel(title, views="1.2M views", channel="Some Channel"):
    return {
        "reelItemRenderer": {
            "videoId": "abc123",
            "headline": {"simpleText": title},
            "shortBylineText": {"runs": [{"text": channel}]},
            "viewCountText": {"simpleText": views},
            "navigationEndpoint": {"commandMetadata": {"webCommandMetadata": {"url": "/shorts/abc123"}}},
        }
    }


class TermMatchingTests(unittest.TestCase):
    def test_explicit_terms_match(self):
        self.assertEqual(yf.matched_term("free porn videos"), "free porn")
        self.assertEqual(yf.matched_term("my ONLYFANS page"), "onlyfans")
        self.assertEqual(yf.matched_term("watch hentai"), "hentai")

    def test_suggestive_terms_match(self):
        self.assertEqual(yf.matched_term("beach bikini 2026"), "bikini")
        self.assertEqual(yf.matched_term("big tits compilation"), "big tits")
        self.assertEqual(yf.matched_term("she is topless"), "topless")

    def test_clean_text_does_not_match(self):
        for text in (
            "python tutorial for beginners",
            "how to cook biryani",
            "the dog enjoys the park",
            "wifi is this marriage healthy",
            "guitar lesson",
        ):
            with self.subTest(text=text):
                self.assertIsNone(yf.matched_term(text))

    def test_word_boundaries_limit_false_positives(self):
        self.assertIsNone(yf.matched_term("analyst of the market"))
        self.assertIsNone(yf.matched_term("cucumber farm video"))
        self.assertIsNotNone(yf.matched_term("anal play video"))

    def test_disabled_by_env(self):
        previous = os.environ.get("BFSB_YOUTUBE_STRICT_FILTER")
        os.environ["BFSB_YOUTUBE_STRICT_FILTER"] = "0"
        try:
            self.assertFalse(yf.enabled())
        finally:
            if previous is None:
                os.environ.pop("BFSB_YOUTUBE_STRICT_FILTER", None)
            else:
                os.environ["BFSB_YOUTUBE_STRICT_FILTER"] = previous
        self.assertTrue(yf.enabled())


class FeedFilterTests(unittest.TestCase):
    def test_flagged_feed_items_are_removed(self):
        data = {"contents": [
            reel("cute dog video"),
            reel("hot chick bikini"),
            reel("guitar solo"),
            reel("nude webcam show"),
        ]}
        removed, hits = yf.filter_tree(data)
        self.assertEqual(removed, 2)
        # Each dropped item contributes the most specific term it matched;
        # which term wins depends on the shipped dataset, so assert that
        # every removal was attributed rather than pinning the labels.
        self.assertEqual(len(hits), 2)
        titles = [item["reelItemRenderer"]["headline"]["simpleText"] for item in data["contents"]]
        self.assertEqual(titles, ["cute dog video", "guitar solo"])

    def test_item_identity_fields_are_ignored(self):
        data = {"contents": [reel("harmless clip", channel="Cool Channel")]}
        data["contents"][0]["reelItemRenderer"]["navigationEndpoint"]["commandMetadata"][
            "webCommandMetadata"
        ]["url"] = "/shorts/porn"
        removed, _ = yf.filter_tree(data)
        self.assertEqual(removed, 0)

    def test_nested_structures_are_walked(self):
        inner_items = [
            reel("guitar solo"),
            {"richItemRenderer": {"content": reel("onlyfans leak")}},
        ]
        section = {"itemSectionRenderer": {"contents": inner_items}}
        section_list = {"sectionListRenderer": {"contents": [section]}}
        tab_content = {"content": section_list}
        tab = {"tabRenderer": tab_content}
        data = {"contents": {"tabs": [tab]}}

        removed, hits = yf.filter_tree(data)

        self.assertEqual(removed, 1)
        self.assertIn("onlyfans", hits)
        self.assertEqual(len(section["itemSectionRenderer"]["contents"]), 1)
        # The enclosing containers must survive even though a descendant
        # matched, otherwise one flagged video deletes a whole shelf.
        self.assertEqual(len(data["contents"]["tabs"]), 1)
        self.assertIn("tabRenderer", data["contents"]["tabs"][0])

    def test_large_payload_is_filtered_promptly(self):
        import time

        def item(i, flagged=False):
            return {
                "reelItemRenderer": {
                    "videoId": f"id{i}",
                    "headline": {"simpleText": f"sexy bikini {i}" if flagged else f"clip number {i}"},
                    "shortBylineText": {"runs": [{"text": "a channel"}]},
                    "viewCountText": {"simpleText": f"{i}.2M views"},
                    "navigationEndpoint": {
                        "commandMetadata": {"webCommandMetadata": {"url": f"/shorts/id{i}"}}
                    },
                }
            }

        sections = [
            {"itemSectionRenderer": {"contents": [item(s * 60 + i, i % 15 == 0) for i in range(60)]}}
            for s in range(5)
        ]
        data = {"contents": {"tabs": [{"tabRenderer": {"content": {"sectionListRenderer": {"contents": sections}}}}]}}
        start = time.perf_counter()
        removed, _ = yf.filter_tree(data)
        elapsed = time.perf_counter() - start
        self.assertEqual(removed, 20)
        # Generous bound: catches a return to per-term regex scanning without
        # being flaky on a slow machine.
        self.assertLess(elapsed, 2.0, f"filter took {elapsed:.2f}s on 300 items")

    def test_non_item_dicts_are_left_alone(self):
        data = {"frameworkUpdates": {"entities": [{"name": "porn", "id": 1}]}}
        removed, _ = yf.filter_tree(data)
        self.assertEqual(removed, 0)
        self.assertEqual(len(data["frameworkUpdates"]["entities"]), 1)


class UserBlocklistTests(unittest.TestCase):
    def test_user_file_adds_and_removes_terms(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "youtube_blocklist.txt"
            path.write_text("# comment\nmyownterm\n-bikini\n", encoding="utf-8")
            original = safety.USER_TERMS_PATH
            safety.USER_TERMS_PATH = path
            safety.reset_cache()
            yf.reset_cache()
            try:
                self.assertIsNone(safety.matched_term("beach bikini 2026"))
                self.assertEqual(safety.matched_term("myownterm video"), "myownterm")
            finally:
                safety.USER_TERMS_PATH = original
                safety.reset_cache()
                yf.reset_cache()
        self.assertEqual(safety.matched_term("beach bikini 2026"), "bikini")


class DomainBlocklistTests(unittest.TestCase):
    def test_known_adult_domains_are_matched(self):
        for value in (
            "pornhub",
            "pornhub.com",
            "https://pornhub.com",
            "https://www.pornhub.com/watch/abc",
            "de.pornhub.com",
            "xvideos.com",
            "onlyfans.com/my",
        ):
            with self.subTest(value=value):
                self.assertIsNotNone(safety.classify(value))

    def test_ordinary_text_is_not_blocked(self):
        for value in (
            "python tutorial",
            "github.com/torvalds/linux",
            "youtube.com",
            "hub.docker.com",
            "how to write a parser",
        ):
            with self.subTest(value=value):
                self.assertIsNone(safety.classify(value))

    def test_explicit_words_are_blocked(self):
        self.assertEqual(safety.classify("pornography is a word"), "pornography")
        self.assertEqual(safety.classify("free pornhub clips"), "pornhub")

    def test_lookalike_hosts_are_not_blocked(self):
        self.assertIsNone(safety.matched_domain("notpornhub.com"))
        self.assertIsNone(safety.matched_domain("pornhub.example.org"))
        self.assertIsNone(safety.matched_domain("mypornhub"))


if __name__ == "__main__":
    unittest.main()


class DictValueItemTests(unittest.TestCase):
    """entityBatchUpdate.mutations[].payload.videoRenderer arrives as a
    dict value, and only list elements were ever tested, so YouTube's
    live-update path was structurally unfiltered."""

    def test_item_reached_as_a_dict_value_is_removed(self):
        from bfsb.core import youtube_filter

        payload = {
            "frameworkUpdates": {
                "entityBatchUpdate": {
                    "mutations": [
                        {
                            "payload": {
                                "videoRenderer": {
                                    "videoId": "abc",
                                    "title": {"runs": [{"text": "porn video free nudes"}]},
                                }
                            }
                        }
                    ]
                }
            }
        }
        removed, hits = youtube_filter.filter_tree(payload)
        self.assertEqual(removed, 1, "an item in mutations[].payload was not filtered")
        self.assertTrue(hits)

    def test_long_metadata_no_longer_protects_an_item(self):
        from bfsb.core import youtube_filter

        long_snippet = {"runs": [{"text": "word " * 400}]}
        items = [
            {
                "videoRenderer": {
                    "videoId": "long",
                    "title": {"runs": [{"text": "free nudes leaked onlyfans"}]},
                    "detailedMetadataSnippets": [long_snippet],
                }
            }
        ]
        removed, _ = youtube_filter.filter_tree(items)
        self.assertEqual(removed, 1, "extra metadata let the item through")

    def test_ordinary_items_survive(self):
        from bfsb.core import youtube_filter

        items = [{"videoRenderer": {"videoId": "ok", "title": {"runs": [{"text": "Rust tutorial 2026"}]}}}]
        removed, _ = youtube_filter.filter_tree(items)
        self.assertEqual(removed, 0)
        self.assertEqual(len(items), 1)
