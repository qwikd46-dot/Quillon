"""The in-page notice that reports a vault which is not saving.

A v2 vault that cannot open used to fail to a single stderr line, so the
user browsed believing passwords and cookies were stored. These tests
cover the rendered notice, the slot it shares with the blocked-search
banner, and the decision to refresh it lazily rather than on a timer.
"""

import ast
import asyncio
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "quillon" / "templates"
TEMPLATE_PATH = TEMPLATES_DIR / "quillon_combined.html"
SERVER_PATH = REPO_ROOT / "quillon" / "core" / "server.py"

try:
    from aiohttp import ClientSession
    from quillon.core.server import QuillonHBServer
except (ImportError, ModuleNotFoundError):
    ClientSession = None
    QuillonHBServer = None

# A term safety.terms() is expected to flag, used to drive the blocked
# search render path.
BLOCKED_QUERY = "free porn"

NOTICE_TEXT = "Passwords and cookies are not being saved."

DEGRADED_TEXT = "Saving to a key file."


class _FakeVault:
    def __init__(self, available, error=None, storage_mode="keyring"):
        self.available = available
        self.last_error = error
        self.storage_mode = storage_mode


class _FakeWindow:
    """Stands in for MainWindow, which owns the two live vault objects."""

    def __init__(self, available, error=None, storage_mode="keyring"):
        self._password_vault = _FakeVault(available, error, storage_mode)
        self._cookie_vault = _FakeVault(available, error, storage_mode)

    @staticmethod
    def _safe_action_target(value: str) -> bool:
        value = (value or "").strip()
        return value.startswith(("http://", "https://")) or value == "about:blank"

    def new_tab(self, url=None, private=False):
        return None


def _function_body(source: str, name: str, until: str) -> str:
    """Slice one function out of the template source.

    A fixed character window breaks the moment the function grows, which
    is how two of these tests started failing for the wrong reason.
    """
    start = source.index(name)
    end = source.index(until, start)
    return source[start:end]


def _template_source() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@unittest.skipIf(QuillonHBServer is None, "project runtime dependencies are unavailable")
class VaultNoticeRenderTests(unittest.TestCase):
    """Renders real pages through a real server."""

    def _fetch(self, available, error, paths, storage_mode="keyring"):
        async def run():
            server = QuillonHBServer(TEMPLATES_DIR, port=0)
            server._main_window = _FakeWindow(available, error, storage_mode)
            server._run_on_qt = lambda fn, timeout=5.0: fn(server._main_window)
            await server.start()
            port = server._site._server.sockets[0].getsockname()[1]
            bodies = {}
            try:
                async with ClientSession() as session:
                    for path in paths:
                        async with session.get(
                            f"http://127.0.0.1:{port}{path}",
                            headers={"Origin": "http://127.0.0.1:8889"},
                        ) as response:
                            bodies[path] = await response.text()
            finally:
                await server.stop()
            return bodies

        return asyncio.run(run())

    def test_notice_shown_when_vault_unavailable(self):
        body = self._fetch(False, "no OS keyring is available", ["/"])["/"]
        self.assertIn('id="vaultError"', body)
        self.assertIn(NOTICE_TEXT, body)
        self.assertIn("vaultAvailable: false", body)

    def test_no_notice_when_vault_available(self):
        body = self._fetch(True, "", ["/"])["/"]
        self.assertNotIn('id="vaultError"', body)
        self.assertNotIn(NOTICE_TEXT, body)
        self.assertIn("vaultAvailable: true", body)

    def test_vault_error_is_surfaced_with_the_consequence(self):
        body = self._fetch(False, "no OS keyring is available", ["/"])["/"]
        # The consequence, not just an internal status word.
        self.assertIn(NOTICE_TEXT, body)
        # ...and the vault's own last_error, rather than a second error path.
        self.assertIn("no OS keyring is available", body)

    def test_notice_present_on_every_page_type(self):
        paths = ["/", "/preferences", "/about", f"/search?q={BLOCKED_QUERY.replace(' ', '+')}"]
        bodies = self._fetch(False, "vault is locked", paths)
        for path, body in bodies.items():
            with self.subTest(path=path):
                # A condition that disables password saving must be visible
                # wherever the user happens to be, not only on the home page.
                self.assertIn('id="vaultError"', body)
                self.assertIn(NOTICE_TEXT, body)

    def test_notice_absent_on_every_page_type_when_healthy(self):
        paths = ["/", "/preferences", "/about", f"/search?q={BLOCKED_QUERY.replace(' ', '+')}"]
        bodies = self._fetch(True, "", paths)
        for path, body in bodies.items():
            with self.subTest(path=path):
                self.assertNotIn('id="vaultError"', body)

    def test_degraded_notice_shown_when_vault_saves_to_a_file_key(self):
        body = self._fetch(True, "", ["/"], storage_mode="file-degraded")["/"]
        self.assertIn('id="vaultDegraded"', body)
        self.assertIn(DEGRADED_TEXT, body)
        self.assertIn("vaultDegraded: true", body)

    def test_degraded_is_not_shown_as_a_saving_failure(self):
        """A vault on a file key IS saving. It must never raise the red
        "not being saved" notice, because that would be a false alarm."""
        body = self._fetch(True, "", ["/"], storage_mode="file-degraded")["/"]
        self.assertNotIn('id="vaultError"', body)
        self.assertNotIn(NOTICE_TEXT, body)

    def test_degraded_absent_when_the_keyring_is_in_use(self):
        body = self._fetch(True, "", ["/"], storage_mode="keyring")["/"]
        self.assertNotIn('id="vaultDegraded"', body)

    def test_degraded_yields_to_an_unavailable_vault(self):
        """Broken beats merely noteworthy: only one may claim the slot."""
        body = self._fetch(False, "no keyring", ["/"], storage_mode="file-degraded")["/"]
        self.assertIn('id="vaultError"', body)
        self.assertNotIn('id="vaultDegraded"', body)

    def test_degraded_present_on_every_page_type(self):
        paths = ["/", "/preferences", "/about", f"/search?q={BLOCKED_QUERY.replace(' ', '+')}"]
        bodies = self._fetch(True, "", paths, storage_mode="file-degraded")
        for path, body in bodies.items():
            with self.subTest(path=path):
                self.assertIn('id="vaultDegraded"', body)


class VaultNoticeSlotTests(unittest.TestCase):
    """Static wiring checks. There is no JS engine here, so these assert
    the source says the right things rather than that they run."""

    def test_both_notices_share_one_fixed_slot(self):
        source = _template_source()
        self.assertIn('<div class="block-error" id="blockError"', source)
        # The vault notice reuses the blocked banner's class, so it is
        # positioned by the same rule and cannot sit below it.
        self.assertIn('class="block-error vault-error" id="vaultError"', source)
        # The degraded notice takes the same slot, not a second one.
        self.assertIn('class="block-error vault-degraded" id="vaultDegraded"', source)

    def test_degraded_uses_its_own_muted_styling(self):
        source = _template_source()
        # It must not be styled like the red alarm: a working vault that
        # looks broken is worse than no notice.
        self.assertIn(".block-error.vault-degraded", source)
        start = source.index(".block-error.vault-degraded {")
        block = source[start:start + 400]
        self.assertNotIn("120, 22, 40", block, "degraded must not reuse the red alarm colours")

    def test_degraded_notice_is_suppressed_by_the_block_notice(self):
        source = _template_source()
        # The shared slot is arbitrated in refreshVaultNotice, which both
        # notices read, rather than by a second offset.
        self.assertIn("isBlockNoticeVisible()", source)
        body = _function_body(source, "function refreshVaultNotice", "function initVaultNotice")
        self.assertIn("vaultDegraded", body)
        self.assertIn("isBlockNoticeVisible", body)

    def test_suppression_helper_takes_no_boolean(self):
        """Pins the reviewer-required fix.

        setVaultNoticeVisible used to take a boolean that was only ever
        passed false, leaving a dead `true` branch that would have shown
        the amber notice without consulting the once-per-profile marker.
        A boolean only ever false is a lie about the API.
        """
        source = _template_source()
        self.assertNotIn("setVaultNoticeVisible", source)
        self.assertIn("function suppressVaultNotices()", source)
        body = _function_body(source, "function suppressVaultNotices", "function isBlockNoticeVisible")
        self.assertNotIn("visible", body)
        # Only suppression lives here; showing belongs to refreshVaultNotice,
        # which is the one place that consults the marker.
        self.assertNotIn("vaultDegradedSeen", body)

    def test_show_blocked_notice_suppresses_via_the_parameterless_helper(self):
        source = _template_source()
        show_body = _function_body(source, "function showBlockedNotice", "function hideBlockedNotice")
        self.assertIn("suppressVaultNotices()", show_body)

    def test_degraded_marker_key_is_not_derived_from_the_unavailable_error(self):
        """The marker key must come from the degraded signal itself.

        Deriving it from vaultError coupled the amber notice to the red
        notice's error string: a future change setting vaultError on the
        degraded path would silently re-warn every user.
        """
        source = _template_source()
        body = _function_body(source, "function vaultDegradedReason", "function vaultDegradedSeen")
        self.assertIn("vaultStorageMode", body)
        self.assertNotIn("vaultError", body)

    def test_degraded_yields_to_the_unavailable_notice(self):
        source = _template_source()
        body = _function_body(source, "function refreshVaultNotice", "function initVaultNotice")
        self.assertIn("!wantUnavailable", body)

    def test_degraded_is_once_per_profile_not_every_load(self):
        """Wallpaper is the failure mode: a user trained to ignore the
        amber notice stops seeing the red one. The red notice must stay
        unconditional, the amber one must consult a stored marker."""
        source = _template_source()
        self.assertIn("VAULT_DEGRADED_SEEN_KEY", source)
        self.assertIn("localStorage", source)
        # Only the amber notice consults the marker.
        body = _function_body(source, "function refreshVaultNotice", "function initVaultNotice")
        self.assertIn("vaultDegradedSeen()", body)
        self.assertIn("!vaultNoticeDismissed", body)
        # ...and the red one never does.
        self.assertNotIn("vaultDegradedSeen", body.split("wantUnavailable = ")[0])

    def test_degraded_still_renders_server_side(self):
        """Suppression is client-side only, so the first paint is honest
        and cannot be faked away by state."""
        source = _template_source()
        self.assertIn("VAULT_DEGRADED|default(false)", source)
        body = _function_body(source, "function refreshVaultNotice", "function initVaultNotice")
        self.assertIn("page.vaultDegraded", body)

    def test_block_notice_suppresses_the_vault_notice(self):
        source = _template_source()
        show_body = _function_body(source, "function showBlockedNotice", "function hideBlockedNotice")
        self.assertIn(
            "suppressVaultNotices()", show_body,
            "the block notice must yield the shared slot, not stack on it",
        )

    def test_vault_notice_returns_after_the_block_notice_dismissed(self):
        source = _template_source()
        self.assertIn("refreshVaultNotice", source)
        hide_body = _function_body(source, "function hideBlockedNotice", "function initBlockedNotice")
        self.assertIn("refreshVaultNotice", hide_body)

    def test_vault_notice_is_not_auto_dismissed(self):
        source = _template_source()
        body = _function_body(source, "function initVaultNotice", "function closeBlockedPopup")
        # No toast timer: a vault that is down is still down in seven
        # seconds, and a dismissed banner that vanishes is worse than none.
        self.assertNotIn("setTimeout", body)

    def test_vault_notice_is_wired_into_init(self):
        source = _template_source()
        self.assertIn("initVaultNotice();", source)


class VaultStatusRefreshTests(unittest.TestCase):
    def test_no_background_refresh_task(self):
        """Pin the lazy decision.

        The only consumer of the vault status is a render, so it is read
        on demand behind a cache. A background refresh task would add a
        task to start, stop and reason about during shutdown for no gain.
        """
        tree = ast.parse(SERVER_PATH.read_text(encoding="utf-8"))
        schedulers = {
            "create_task", "ensure_future", "call_later", "call_at",
            "call_every", "start", "sleep",
        }
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            name = node.name.lower()
            if "vault" not in name:
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    func = inner.func
                    attr = getattr(func, "attr", None) or getattr(func, "id", None)
                    if attr in schedulers:
                        offenders.append(f"{node.name} -> {attr}")
        self.assertEqual(
            offenders, [],
            "vault status must stay lazy; do not reintroduce a refresh task",
        )

    def test_vault_status_is_cached_on_a_signature(self):
        source = SERVER_PATH.read_text(encoding="utf-8")
        self.assertIn("_VAULT_STATUS_TTL", source)
        # Same cheap change token the adblock state file already uses.
        self.assertIn("st_mtime", source)
        self.assertIn("st_size", source)


if __name__ == "__main__":
    unittest.main()
