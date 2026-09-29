"""Qt-layer regression tests that can fail for reasons a user would notice.

Every other test in this project asserts on strings in source files, which
is why the tab bugs below survived a green suite for two days.

Honest scope, revised after audit. This file is NOT "Qt layer coverage".
It is:
  * five facts about the installed PyQt6 build, which pin the API shape our
    code depends on. Those would not fail if our code regressed.
  * two guards that scan source text, repo-wide. They catch reintroduced
    call sites, not behaviour.
  * one behavioural test of the real switch_tab, added last, which is the
    only test here that can fail for the reason the user actually cares
    about: the tab does not move.

The offscreen platform is chosen by the runner, not by this file. Import
time must not mutate global env, because this module is imported into the
same process as the whole suite and a test file silently dictating
QT_QPA_PLATFORM for every other test is a side effect nobody can see.
"""

import os
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("quillon",)

try:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtWebEngineWidgets import QWebEngineView

    HAVE_QT = True
except Exception:  # pragma: no cover - environment without Qt
    HAVE_QT = False

_APP = None


def _app():
    """One QApplication per process; Qt refuses a second.

    argv[0] must be a real program name or Qt aborts during command-line
    initialisation on the offscreen platform.
    """
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication(["quillon-test"])
    return _APP


def _python_sources():
    for base in SOURCE_DIRS:
        for path in sorted((REPO_ROOT / base).rglob("*.py")):
            yield path


def _all_sources_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in _python_sources())


@unittest.skipUnless(HAVE_QT, "PyQt6 / QtWebEngine is unavailable")
class PyQtApiShapeTests(unittest.TestCase):
    """Facts about the installed build that our code relies on.

    These are API pins, not tests of our behaviour. If one fails, the
    helper below may be able to go back to the view-level form -- but
    nothing here would catch us failing to use the helper.
    """

    def setUp(self):
        _app()
        self.view = QWebEngineView()

    def tearDown(self):
        self.view.deleteLater()

    def test_calling_isloading_on_a_view_raises(self):
        """The bug itself, asserted in the failing direction.

        If a future Qt adds view.isLoading() this fails informatively:
        the bug class is gone, revisit the helper. This replaces a weaker
        hasattr-based canary that only flipped a boolean.
        """
        with self.assertRaises(AttributeError):
            self.view.isLoading()

    def test_page_has_isloading(self):
        self.assertTrue(hasattr(self.view.page(), "isLoading"))
        self.assertIsInstance(self.view.page().isLoading(), bool)


@unittest.skipUnless(HAVE_QT, "PyQt6 / QtWebEngine is unavailable")
class ViewIsLoadingHelperTests(unittest.TestCase):
    """Tests of our helper, on a real view where possible."""

    def setUp(self):
        _app()

    def test_helper_returns_a_real_bool_on_a_real_view(self):
        from quillon.ui.main_window import view_is_loading

        view = QWebEngineView()
        try:
            result = view_is_loading(view)
            self.assertIsInstance(result, bool)
        finally:
            view.deleteLater()

    def test_helper_raises_nothing_on_a_real_view(self):
        """Stated as its own test, not as assertIn(x, (True, False)).

        Every bool is in that tuple, so the original form asserted nothing.
        """
        from quillon.ui.main_window import view_is_loading

        view = QWebEngineView()
        try:
            view_is_loading(view)  # must not raise
        finally:
            view.deleteLater()

    def test_helper_survives_a_view_whose_page_raises(self):
        """The realistic teardown failure is a deleted C++ object raising
        RuntimeError, not page() returning None. The old test covered the
        case that cannot actually happen and missed the one that can."""
        from quillon.ui.main_window import view_is_loading

        class _DeletedCpp:
            def page(self):
                raise RuntimeError("wrapped C/C++ object has been deleted")

        self.assertIs(view_is_loading(_DeletedCpp()), False)

    def test_helper_survives_a_view_with_no_page(self):
        from quillon.ui.main_window import view_is_loading

        class _NoPage:
            def page(self):
                return None

        self.assertIs(view_is_loading(_NoPage()), False)


class ViewLevelIsLoadingGuardTests(unittest.TestCase):
    """Source guards, scanned repo-wide.

    Scoped honestly: these catch a reintroduced call site, not a behaviour
    change. An earlier version read only main_window.py and so could not
    see webengine.py, which had a fourth instance of the bug.
    """

    def test_no_view_level_isloading_anywhere(self):
        offenders = [
            f"{path.relative_to(REPO_ROOT)}: {receiver}"
            for path in _python_sources()
            for receiver in re.findall(r"(\w+)\.isLoading\(\)", path.read_text(encoding="utf-8", errors="replace"))
            if receiver != "page"
        ]
        self.assertEqual(
            offenders, [],
            "QWebEngineView has no isLoading(); use view_is_loading(view)",
        )

    def test_the_only_direct_page_call_is_inside_the_helper(self):
        """Checks location, not just count.

        The previous version asserted count == 1 and its message claimed
        "inside the helper" while never looking. An exact count also fails
        on a legitimate second page call, so this allows more than one and
        only requires that none sit outside the helper body.
        """
        from quillon.ui.main_window import view_is_loading

        helper_source = Path(view_is_loading.__code__.co_filename).read_text(
            encoding="utf-8", errors="replace"
        )
        outside = [
            f"{path.relative_to(REPO_ROOT)}"
            for path in _python_sources()
            for receiver in re.findall(r"(\w+)\.isLoading\(\)", path.read_text(encoding="utf-8", errors="replace"))
            if receiver == "page" and path != Path(view_is_loading.__code__.co_filename)
        ]
        self.assertEqual(outside, [], "page.isLoading() should only be called in the helper")
        self.assertIn("page.isLoading()", helper_source)



@unittest.skipUnless(HAVE_QT, "PyQt6 / QtWebEngine is unavailable")
class SwitchTabBehaviourTests(unittest.TestCase):
    """The only test here that can fail for the user's reason.

    Builds a real window offscreen with two tabs, switches, and asserts
    the stack index actually moved AND that no failure line was printed.
    The second assertion is the important one: switch_tab wraps its body in
    a bare `except Exception: print(...)`, so a failure after
    setCurrentIndex still leaves the tab switched while the chrome is
    stale. A test that only checked the index would pass on exactly the
    bug the user reported.
    """

    def setUp(self):
        _app()
        self._had_quillon_test = "QUILLON_TEST" in os.environ
        self._prev_quillon_test = os.environ.get("QUILLON_TEST")
        os.environ.setdefault("QUILLON_TEST", "1")

    def tearDown(self):
        # QUILLON_TEST is what disables the CORS guard at server.py:233 and
        # :235. Leaving it set for the rest of the process hands anyone who
        # writes a CORS test next a vacuous pass, so give it back exactly as
        # we found it.
        if self._had_quillon_test:
            os.environ["QUILLON_TEST"] = self._prev_quillon_test
        else:
            os.environ.pop("QUILLON_TEST", None)

    def _patch(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)

    def _build_window(self):
        """Build a real window against throwaway state.

        QuillonWindow constructs CookieVault and PasswordVault in __init__,
        and those reach the real ~/.quillon vault plus the real OS keyring.
        Left alone, simply running this suite migrates the user's vault
        key to a new account -- or, with no vault on disk, mints and
        stores a fresh one. A test must not edit live credentials, so
        every path and keyring the window can reach is redirected here and
        restored on cleanup.
        """
        import dataclasses
        import tempfile

        from quillon.core import config
        from quillon.core import cookies as cookies_mod
        from quillon.core import passwords as passwords_mod
        from quillon.core import secure_vault
        from quillon.core.blocker import URLBlocker
        from quillon.ui.main_window import QuillonWindow

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        sandboxed = dataclasses.replace(
            config.PATHS,
            QUILLON_DIR=root,
            COOKIE_DB=root / "cookies.enc",
            PASSWORD_DB=root / "passwords.enc",
            VAULT_DB=root / "vault.enc",
            VAULT_KEY=root / "vault.key",
        )
        for module in (config, cookies_mod, passwords_mod):
            self._patch(module, "PATHS", sandboxed)

        class FakeKeyring:
            def __init__(self):
                self.values = {}

            def get_password(self, service, account):
                return self.values.get((service, account))

            def set_password(self, service, account, value):
                self.values[(service, account)] = value

            def delete_password(self, service, account):
                self.values.pop((service, account), None)

        # The providers import VaultKeyProvider by name, so each module
        # that holds one needs its own patch.
        real_provider = secure_vault.VaultKeyProvider

        def sandboxed_provider(*args, **kwargs):
            kwargs.setdefault("keyring_module", FakeKeyring())
            return real_provider(*args, **kwargs)

        for module in (cookies_mod, passwords_mod):
            self._patch(module, "VaultKeyProvider", sandboxed_provider)

        return QuillonWindow(URLBlocker())

    def test_opening_a_tab_by_url_starts_the_progress_bar(self):
        """A clicked result link opens a tab by URL, and that is the path
        that showed no progress at all.

        The bar used to depend on loadStarted winning a race: setUrl fires
        it, but the view only becomes current a few lines later, and the
        handler only started the bar for the already-current view. When
        that lost, nothing started it. The window now starts it
        explicitly, so this asserts the call is not left to signal order.
        """
        from PyQt6.QtCore import QEventLoop, QTimer

        window = self._build_window()
        calls = []
        for name in ("start_progress", "set_progress", "complete_progress"):
            original = getattr(window._chrome, name)

            def recorder(n=original, label=name):
                def inner(*a, **k):
                    calls.append(label)
                    return n(*a, **k)
                return inner

            setattr(window._chrome, name, recorder())
        try:
            window.new_tab("https://example.com")
            # Synchronously, before any event loop turn: this is the part
            # that used to be lost.
            self.assertIn(
                "start_progress", calls,
                "opening a tab by URL must start the progress bar immediately",
            )
        finally:
            window.close()
            window.deleteLater()

    def test_progress_starts_after_the_chrome_is_shown_for_a_bookmark_press(self):
        """A bookmark card navigates the current view out of a Quillon page.

        The chrome is hidden on Quillon pages, so loadStarted fires while the
        bar is still inside a hidden widget and the bar is painted where
        nobody can see it. It must be started again once the chrome is
        actually shown for a site.
        """
        from PyQt6.QtCore import QEventLoop, QTimer, QUrl

        window = self._build_window()
        window.new_tab()  # the home page
        loop = QEventLoop(); QTimer.singleShot(1500, loop.quit); loop.exec()
        self.assertFalse(
            getattr(window, "_chrome_shown_for_site", False),
            "the chrome should be hidden while on a Quillon page",
        )

        order = []
        original = window._chrome.start_progress

        def spy(*a, **k):
            order.append(bool(getattr(window, "_chrome_shown_for_site", False)))
            return original(*a, **k)

        window._chrome.start_progress = spy
        try:
            window._get_current_view().setUrl(QUrl("https://example.com/"))
            loop = QEventLoop(); QTimer.singleShot(1500, loop.quit); loop.exec()
        finally:
            window.close()
            window.deleteLater()

        self.assertTrue(order, "the progress bar was never started")
        self.assertTrue(
            order[-1],
            "the last start_progress happened while the chrome was still "
            "hidden, so the user saw no bar at all",
        )

    def test_opening_a_quillon_page_does_not_start_the_progress_bar(self):
        """The bar is for real sites; Quillon pages have no native chrome."""
        from PyQt6.QtCore import QEventLoop, QTimer

        window = self._build_window()
        calls = []
        for name in ("start_progress", "set_progress"):
            original = getattr(window._chrome, name)

            def recorder(n=original, label=name):
                def inner(*a, **k):
                    calls.append(label)
                    return n(*a, **k)
                return inner

            setattr(window._chrome, name, recorder())
        try:
            window.new_tab("http://127.0.0.1:8889/search?q=test")
            self.assertNotIn("start_progress", calls)
        finally:
            window.close()
            window.deleteLater()

    def test_switch_tab_moves_the_stack_and_does_not_log_a_failure(self):
        import io
        from contextlib import redirect_stdout

        window = self._build_window()
        try:
            window.new_tab()
            window.new_tab()
            self.assertGreaterEqual(len(window._views), 2, "need two tabs to switch between")

            before = window._stack.currentIndex()
            target = 1 if before == 0 else 0

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                window.switch_tab(target)

            after = window._stack.currentIndex()
            printed = buffer.getvalue()

            self.assertEqual(
                after, target,
                "switch_tab did not move the stack",
            )
            self.assertNotIn(
                "switch_tab failed", printed,
                f"switch_tab raised and swallowed: {printed.strip()!r}",
            )
        finally:
            window.deleteLater()
            window.close()


if __name__ == "__main__":
    unittest.main()
