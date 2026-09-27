import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _load(name, relpath):
    stubbed = ("bfsb", "bfsb.core", "bfsb.core.search")
    saved = {n: sys.modules.get(n) for n in stubbed}
    for n in stubbed:
        stub = types.ModuleType(n)
        stub.__path__ = []
        sys.modules[n] = stub
    try:
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


safety = _load("bfsb_safety_test", "bfsb/core/search/safety.py")


class BlockedQueryTests(unittest.TestCase):
    """What the BFSB search bar refuses before any provider is contacted."""

    def test_flagged_queries_are_classified(self):
        for value, expected in (
            ("pornhub", "pornhub"),
            ("pornhub.com", "pornhub.com"),
            ("https://www.pornhub.com/watch/x", "pornhub.com"),
            # Which exact host is reported depends on the published list;
            # what matters is that the subdomain is refused.
            ("xvideos", "xvideos"),
            ("onlyfans.com", "onlyfans.com"),
            ("free porn", "free porn"),  # longest match is reported
        ):
            with self.subTest(value=value):
                self.assertEqual(safety.classify(value), expected)

    def test_broad_coverage(self):
        """Cases that used to slip through the narrow list."""
        for value, expected in (
            ("fansly", "fansly"),
            ("fansly.com", "fansly.com"),
            ("onlyfans", "onlyfans"),
            ("free sex videos", "sex videos"),
            ("nude pics", "nude pics"),
            ("cam girl", "cam girl"),
            ("deep throat", "deep throat"),
            ("sugar daddy", "sugar daddy"),
            ("leaked nudes", "leaked nudes"),
            ("hentai", "hentai"),
            ("milf", "milf"),
            ("cougar", "cougar"),
            ("18+", "18+"),
            ("anal play", "anal"),
            ("sex position", "sex position"),
        ):
            with self.subTest(value=value):
                self.assertEqual(safety.classify(value), expected)

    def test_word_boundaries_allow_lookalikes(self):
        """Substring traps must not block ordinary words."""
        for value in (
            "essex county",
            "sussex police",
            "analysis of data",
            "class of 2026",
            "javascript array map",
            "hotmail login",
            "passport application",
            "assess the risk",
        ):
            with self.subTest(value=value):
                self.assertIsNone(safety.classify(value))

    def test_broad_terms_block_ordinary_phrases_by_design(self):
        """The list is deliberately aggressive; the user file is the escape hatch."""
        self.assertEqual(safety.classify("sex education documentary"), "sex")

    def test_user_file_can_reopen_a_broad_term(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "youtube_blocklist.txt"
            path.write_text("-sex\n", encoding="utf-8")
            original = safety.USER_TERMS_PATH
            safety.USER_TERMS_PATH = path
            safety.reset_cache()
            try:
                self.assertIsNone(safety.classify("sex education documentary"))
                self.assertEqual(safety.classify("free nudes"), "free nudes")
            finally:
                safety.USER_TERMS_PATH = original
                safety.reset_cache()
        self.assertEqual(safety.classify("sex education documentary"), "sex")

    def test_lookalike_hosts_pass(self):
        for value in (
            "notpornhub.com",
            "hub.docker.com",
            "youtube.com",
        ):
            with self.subTest(value=value):
                self.assertIsNone(safety.classify(value))

    def test_subdomain_of_a_blocked_site_is_blocked(self):
        # The published list contains the subdomain itself, so the host is
        # reported verbatim.
        self.assertIsNotNone(safety.classify("de.pornhub.com"))

    def test_clone_domains_are_still_blocked(self):
        # "pornhub" is a whole word here, so the term list catches the
        # clone even though the host is not the real domain.
        self.assertIsNone(safety.matched_domain("pornhub.example.org"))
        self.assertEqual(safety.classify("pornhub.example.org"), "pornhub")

    def test_ordinary_searches_pass(self):
        for value in (
            "python tutorial",
            "how to write a parser",
            "linux kernel news",
            "best github repositories",
        ):
            with self.subTest(value=value):
                self.assertIsNone(safety.classify(value))

    def test_disabled_by_env(self):
        import os

        previous = os.environ.get("BFSB_YOUTUBE_STRICT_FILTER")
        os.environ["BFSB_YOUTUBE_STRICT_FILTER"] = "0"
        try:
            self.assertFalse(safety.enabled())
        finally:
            if previous is None:
                os.environ.pop("BFSB_YOUTUBE_STRICT_FILTER", None)
            else:
                os.environ["BFSB_YOUTUBE_STRICT_FILTER"] = previous
        self.assertTrue(safety.enabled())


class TemplateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "bfsb/templates/bfsb_combined.html").read_text(encoding="utf-8")
        cls.server = (ROOT / "bfsb/core/server.py").read_text(encoding="utf-8")

    def test_block_notice_markup_exists(self):
        self.assertIn('id="blockOverlay"', self.template)
        self.assertIn('id="blockClose"', self.template)
        self.assertIn('id="blockError"', self.template)
        self.assertIn("/static/cheaky.png", self.template)

    def test_popup_is_dismissable_by_x_backdrop_and_escape(self):
        self.assertIn("close.addEventListener('click', closeBlockedPopup)", self.template)
        self.assertIn("if (event.target === overlay) closeBlockedPopup();", self.template)
        self.assertIn("event.key === 'Escape' && overlay && !overlay.hidden", self.template)

    def test_popup_appears_one_second_after_the_error(self):
        self.assertIn("blockPopupTimer = setTimeout(showBlockedPopup, 1000);", self.template)

    def test_toast_has_an_explicit_outro(self):
        self.assertIn(".toast.outro { animation: toastOutro", self.template)
        self.assertIn("@keyframes toastOutro", self.template)
        self.assertIn("function hideToast()", self.template)
        self.assertIn("toastTimer = setTimeout(hideToast, 3000);", self.template)
        # The old code dropped .show directly, which skipped the animation.
        self.assertNotIn("setTimeout(() => toast.classList.remove('show')", self.template)

    def test_block_notice_animates_out_and_auto_dismisses(self):
        self.assertIn(".block-error.outro { animation: blockErrorOutro", self.template)
        self.assertIn("@keyframes blockErrorOutro", self.template)
        self.assertIn("blockErrorTimer = setTimeout(hideBlockedNotice, 7000);", self.template)
        self.assertIn('id="blockErrorClose"', self.template)
        # Hiding must wait for the animation, not snap to hidden.
        self.assertIn("error.classList.add('outro');", self.template)
        self.assertIn("error.hidden = true;", self.template)
        # A stale outro timer must not hide a freshly shown banner.
        show_at = self.template.index("function showBlockedNotice(term) {")
        clear_at = self.template.index("clearTimeout(blockErrorOutroTimer); blockErrorOutroTimer = null;", show_at)
        self.assertLess(clear_at, show_at + 600)

    def test_blocklist_is_injected_from_the_server(self):
        self.assertIn("ADULT_TERMS_JSON", self.server)
        self.assertIn("ADULT_DOMAINS_JSON", self.server)
        self.assertIn("adultTerms: {{ ADULT_TERMS_JSON", self.template)
        self.assertIn("function classifyTyped", self.template)

    def test_server_refuses_before_calling_providers(self):
        blocked_at = self.server.index("blocked = self._blocked_search(query)")
        search_at = self.server.index("agg = await search_async(query, progress=progress)")
        self.assertLess(blocked_at, search_at)
        self.assertIn("await self._render_blocked_search(request, query, blocked)", self.server)

    def test_address_bar_blocks_before_navigating(self):
        block_at = self.template.index("const blocked = classifyTyped(val);")
        navigate_at = self.template.index("navigateFromAddressBar(direct);")
        self.assertLess(block_at, navigate_at)

    def test_image_asset_is_shipped(self):
        self.assertTrue((ROOT / "bfsb/templates/static/cheaky.png").is_file())


class ShippedDataTests(unittest.TestCase):
    """The published-blocklist data set and how it is matched."""

    @classmethod
    def setUpClass(cls):
        cls.domains_path = ROOT / "bfsb/core/search/data/adult_domains.txt"
        cls.labels_path = ROOT / "bfsb/core/search/data/adult_labels.txt"

    def test_data_files_are_present_and_large(self):
        self.assertTrue(self.domains_path.is_file())
        self.assertTrue(self.labels_path.is_file())
        domains = [l for l in self.domains_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        labels = [l for l in self.labels_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertGreater(len(domains), 50_000)
        self.assertGreater(len(labels), 20_000)

    def test_provenance_is_documented(self):
        readme = (ROOT / "bfsb/core/search/data/SOURCES.md").read_text(encoding="utf-8")
        self.assertIn("StevenBlack", readme)
        self.assertIn("someonewhocares.org", readme)
        self.assertIn("english-words", readme)

    def test_stats_report_loaded_entries(self):
        safety.load_stats()
        stats = safety.load_stats()
        self.assertGreater(stats["shipped_domains"], 50_000)
        self.assertGreater(stats["shipped_labels"], 20_000)

    def test_site_names_from_the_dataset_are_blocked(self):
        for value in (
            "best xhamster clips",
            "cam4 stream",
            "redtube videos",
            "e621",
            "booru pictures",
            "chaturbate models",
            "onlyfans leaks",
            "motherless video",
        ):
            with self.subTest(value=value):
                self.assertIsNotNone(safety.classify(value))

    def test_typed_urls_are_blocked_by_exact_host(self):
        self.assertIsNotNone(safety.classify("https://xhamster.com/some/path"))
        self.assertIsNotNone(safety.classify("www.cam4.com"))

    def test_general_purpose_brands_are_not_blocked(self):
        """Sites that merely host adult material must stay reachable."""
        for value in (
            "github repo",
            "youtube download",
            "reddit thread",
            "facebook login",
            "best github repositories",
        ):
            with self.subTest(value=value):
                self.assertIsNone(safety.classify(value))

    def test_qualifier_plus_act_rule_catches_unlisted_combinations(self):
        self.assertIsNone(safety.matched_term("secret cam sessions"))
        self.assertIsNotNone(safety.classify("secret cam sessions"))

    def test_ordinary_qualifiers_still_pass(self):
        for value in (
            "free vpn for linux",
            "machine learning model",
            "mobile phone repair",
            "premium spotify price",
            "adult education course",
            "free movies online",
            "girl names 2026",
        ):
            with self.subTest(value=value):
                self.assertIsNone(safety.classify(value))


if __name__ == "__main__":
    unittest.main()


class GenericTokenRegressionTests(unittest.TestCase):
    """The shipped label set once contained "http", "https" and 53 bare
    numbers, derived from hosts like https-pornhub.com. Every URL-shaped
    search was reported as adult content, and any YouTube item whose text
    contained a link was deleted from the feed."""

    def test_url_shaped_queries_are_not_blocked(self):
        from bfsb.core.search import safety

        for query in (
            "https://github.com/torvalds/linux",
            "http://example.com",
            "check out https://news.ycombinator.com",
            "https://www.youtube.com/watch?v=abc",
        ):
            with self.subTest(query=query):
                self.assertIsNone(safety.matched_term(query), query)

    def test_generic_tokens_are_not_in_the_shipped_labels(self):
        from bfsb.core.search import safety

        for token in ("http", "https", "ftp", "www"):
            self.assertNotIn(token, safety._adult_labels, token)
        numeric = [label for label in safety._adult_labels if label.isdigit()]
        self.assertEqual(numeric, [], f"numeric labels block any year: {numeric[:8]}")

    def test_real_terms_are_still_blocked(self):
        from bfsb.core.search import safety

        for query in ("pornhub.com", "porn", "free porn", "sextape"):
            with self.subTest(query=query):
                self.assertIsNotNone(safety.matched_term(query), query)


class HomoglyphRegressionTests(unittest.TestCase):
    """The tokeniser was [a-z0-9]+ with no normalisation, so every
    non-ASCII character split a token and matched nothing."""

    def test_diacritic_fullwidth_and_cyrillic_forms_are_blocked(self):
        from bfsb.core.search import safety

        for query in ("pórn", "ｐｏｒｎ", "рorn", "pоrn", "ｓｅｘ", "pórnhub"):
            with self.subTest(query=query):
                self.assertIsNotNone(safety.matched_term(query), query)

    def test_folding_does_not_break_ordinary_queries(self):
        from bfsb.core.search import safety

        for query in ("https://github.com/torvalds/linux", "best github repositories"):
            with self.subTest(query=query):
                self.assertIsNone(safety.matched_term(query), query)
