import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


try:
    importlib.import_module("httpx")
except ModuleNotFoundError:
    httpx = types.ModuleType("httpx")
    httpx.AsyncClient = object
    httpx.Timeout = object
    sys.modules["httpx"] = httpx

ROOT = Path(__file__).parents[1]


def _load_sibling(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_stubbed = ("quillon", "quillon.core", "quillon.core.search")
_saved = {name: sys.modules.get(name) for name in _stubbed}
for _name in _stubbed:
    _stub = types.ModuleType(_name)
    _stub.__path__ = []
    sys.modules[_name] = _stub

try:
    _load_sibling("quillon.core.search.policy", "quillon/core/search/policy.py")
    _load_sibling("quillon.core.search.shortcuts", "quillon/core/search/shortcuts.py")
finally:
    for _name, _previous in _saved.items():
        if _previous is None:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _previous

spec = importlib.util.spec_from_file_location("quillon_aggregator_test", ROOT / "quillon/core/search/aggregator.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class _StubShortcuts:
    def lookup(self, query):
        from quillon.core.search.shortcuts import ShortcutHit

        if (query or "").strip().lower().startswith("youtube"):
            return ShortcutHit(key="youtube", url="https://www.youtube.com", query=query)
        return None


_ORIGINAL_SEARXNG = module.searxng_source
_ORIGINAL_HACKERNEWS = module.hackernews_source


class AggregatorProgressTests(unittest.TestCase):
    def setUp(self):
        self._original_shortcuts = module.ShortcutManager

    def tearDown(self):
        module.ShortcutManager = self._original_shortcuts
        module.searxng_source = _ORIGINAL_SEARXNG
        module.hackernews_source = _ORIGINAL_HACKERNEWS

    def test_parallel_sources_emit_lifecycle_events(self):
        async def source(query):
            return [module.Result("https://example.com", "Example", "", "test")]

        async def run():
            module.searxng_source = source
            module.hackernews_source = source
            events = []
            response = await module.aggregate_search("query", progress=events.append)
            return response, events

        response, events = asyncio.run(run())
        names = [event["event"] for event in events]
        self.assertEqual(names.count("engine_start"), 2)
        self.assertEqual(names.count("engine_done"), 2)
        self.assertEqual(len(response.results), 1)

    def test_result_urls_reject_script_schemes(self):
        self.assertIsNone(module._safe_result_url("javascript:alert(1)"))
        self.assertIsNone(module._safe_result_url("data:text/html,<script>alert(1)</script>"))
        self.assertIsNone(module._safe_result_url("https://:443/path"))
        self.assertIsNone(module._safe_result_url("https://example.com:bad/path"))
        self.assertEqual(module._safe_result_url("https://example.com/path"), "https://example.com/path")

    def test_official_shortcut_wins_and_repos_are_filtered(self):
        async def run():
            async def searxng(query):
                return [
                    module.Result("https://github.com/some/youtube-clone", "YouTube clone", "", "searxng"),
                    module.Result("https://mirror.example/youtube", "YouTube mirror", "", "searxng"),
                ]

            async def hackernews(query):
                return [
                    module.Result("https://news.ycombinator.com/item?id=1", "HN thread", "", "hackernews"),
                    module.Result("https://www.youtube.com/", "YouTube", "", "searxng"),
                ]

            module.searxng_source = searxng
            module.hackernews_source = hackernews
            module.ShortcutManager = _StubShortcuts
            return await module.aggregate_search("youtube")

        response = asyncio.run(run())
        urls = [r.url for r in response.results]
        self.assertEqual(urls[0], "https://www.youtube.com/")
        self.assertFalse(any("github.com" in u for u in urls), urls)
        self.assertEqual([r.rank for r in response.results], list(range(1, len(urls) + 1)))

    def test_official_shortcut_is_injected_when_missing(self):
        async def run():
            async def empty(query):
                return []

            module.searxng_source = empty
            module.hackernews_source = empty
            module.ShortcutManager = _StubShortcuts
            return await module.aggregate_search("youtube")

        response = asyncio.run(run())
        self.assertEqual(len(response.results), 1)
        self.assertEqual(response.results[0].url, "https://www.youtube.com")
        self.assertEqual(response.results[0].source, "shortcut")

    def test_explicit_github_query_keeps_repository_results(self):
        async def run():
            async def searxng(query):
                return [module.Result("https://github.com/psf/requests", "Requests", "", "searxng")]

            async def hackernews(query):
                return []

            module.searxng_source = searxng
            module.hackernews_source = hackernews
            module.ShortcutManager = _StubShortcuts
            return await module.aggregate_search("github python library")

        response = asyncio.run(run())
        self.assertEqual([r.url for r in response.results], ["https://github.com/psf/requests"])


if __name__ == "__main__":
    unittest.main()
