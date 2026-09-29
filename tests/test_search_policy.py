import importlib.util
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "quillon_search_policy_test",
    ROOT / "quillon/core/search/policy.py",
)
policy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = policy
spec.loader.exec_module(policy)


@dataclass
class FakeResult:
    url: str
    title: str = ""
    snippet: str = ""
    source: str = "searxng"


class SearchPolicyTests(unittest.TestCase):
    def test_normalized_host_strips_www(self):
        self.assertEqual(policy.normalized_host("https://www.YouTube.com/watch"), "youtube.com")
        self.assertEqual(policy.normalized_host("not a url"), "")

    def test_official_host_matches_subdomains(self):
        self.assertTrue(policy.is_official_host("https://m.youtube.com/watch?v=1", "https://www.youtube.com"))
        self.assertTrue(policy.is_official_host("https://youtube.com", "https://www.youtube.com"))
        self.assertTrue(policy.is_official_host("https://youtube.com", "https://m.youtube.com"))
        self.assertFalse(policy.is_official_host("https://notyoutube.com", "https://www.youtube.com"))

    def test_bare_tld_never_counts_as_official(self):
        self.assertFalse(policy.is_official_host("https://com", "https://www.youtube.com"))
        self.assertFalse(policy.is_official_host("not a url", "https://www.youtube.com"))

    def test_official_site_is_ranked_first(self):
        results = [
            FakeResult("https://youtube.fandom.com/wiki/YouTube", "YouTube - Fandom"),
            FakeResult("https://www.youtube.com/", "YouTube"),
            FakeResult("https://news.example.com/youtube", "YouTube news"),
        ]
        ranked, filtered = policy.rank_results(
            "youtube",
            results,
            official_url="https://www.youtube.com",
        )
        self.assertEqual(filtered, 0)
        self.assertEqual(ranked[0].url, "https://www.youtube.com/")

    def test_repository_links_filtered_for_ordinary_queries(self):
        results = [
            FakeResult("https://github.com/some/repo", "A repo", source="hackernews"),
            FakeResult("https://www.youtube.com/", "YouTube"),
        ]
        ranked, filtered = policy.rank_results(
            "youtube",
            results,
            official_url="https://www.youtube.com",
        )
        self.assertEqual(filtered, 1)
        self.assertNotIn("github.com", [r.url for r in ranked])

    def test_explicit_repository_queries_are_allowed(self):
        results = [FakeResult("https://github.com/some/repo", "repo")]
        ranked, filtered = policy.rank_results("github repo mirror", results)
        self.assertEqual(filtered, 0)
        self.assertEqual(len(ranked), 1)

    def test_hackernews_source_is_demoted(self):
        results = [
            FakeResult("https://example.com/a", "A", source="hackernews"),
            FakeResult("https://example.org/b", "B", source="searxng"),
        ]
        ranked, _ = policy.rank_results("topic", results)
        self.assertEqual(ranked[0].source, "searxng")


class AddressBarContractTests(unittest.TestCase):
    def test_typed_domain_navigates_over_https(self):
        template = (ROOT / "quillon/templates/quillon_combined.html").read_text(encoding="utf-8")
        self.assertIn("function directUrlForAddress", template)
        self.assertIn("return 'https://' + val;", template)
        self.assertIn("const direct = directUrlForAddress(val);", template)


if __name__ == "__main__":
    unittest.main()
