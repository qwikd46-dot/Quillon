"""Cookies must not sit on disk in the clear.

Two things are under test:

* the default really is to not persist cookies at all, which is the
  control that does not depend on Chromium doing anything in particular;
* the os_crypt key handed to a profile is well formed, stable, and never
  written to disk in the clear.

The plaintext detector is itself tested against a real SQLite cookie
table, because a detector that never fires would make the rest of this
file pass vacuously.
"""

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_module():
    name = "bfsb_chromium_crypt_test"
    path = ROOT / "bfsb/core/chromium_crypt.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def make_cookie_db(path: Path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "create table cookies (host_key text, name text, value text, "
        "encrypted_value blob)"
    )
    conn.executemany(
        "insert into cookies values (?,?,?,?)", rows
    )
    conn.commit()
    conn.close()


class PlaintextDetectorTests(unittest.TestCase):
    """The detector has to fire, or every assertion built on it is theatre."""

    def setUp(self):
        self.mod = load_module()
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_detects_a_plaintext_value(self):
        db = self.root / "Cookies"
        make_cookie_db(db, [
            (".example.com", "sid", "PLAINTEXT_VALUE", b""),
            (".example.com", "ok", "", b"v10-encrypted-blob"),
        ])
        found = self.mod.cookies_stored_in_plaintext(self.root)
        self.assertEqual(found, ["sid"])

    def test_ignores_fully_encrypted_rows(self):
        db = self.root / "Cookies"
        make_cookie_db(db, [
            (".example.com", "a", "", b"v10-blob"),
            (".example.com", "b", "", b"v10-blob"),
        ])
        self.assertEqual(self.mod.cookies_stored_in_plaintext(self.root), [])

    def test_missing_database_is_not_an_error(self):
        self.assertEqual(self.mod.cookies_stored_in_plaintext(self.root), [])


class KeyWrappingTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_a_wrapped_key_round_trips(self):
        key = self.mod.generate_master_key()
        self.assertEqual(len(key), 16)
        self.assertEqual(self.mod.unwrap_master_key(
            self.mod.wrap_master_key(key)), key)

    def test_two_keys_do_not_wrap_to_the_same_blob(self):
        a, b = self.mod.generate_master_key(), self.mod.generate_master_key()
        self.assertNotEqual(self.mod.wrap_master_key(a),
                            self.mod.wrap_master_key(b))

    def test_wrapped_blob_looks_like_chromium_v10(self):
        import base64
        blob = base64.b64decode(
            self.mod.wrap_master_key(self.mod.generate_master_key()))
        self.assertTrue(blob.startswith(b"v10"))
        # 16-byte plaintext + full PKCS#7 block.
        self.assertEqual(len(blob), 3 + 32)

    def test_wrong_length_key_is_refused(self):
        with self.assertRaises(self.mod.ChromiumCryptError):
            self.mod.wrap_master_key(b"too short")

    def test_junk_does_not_unwrap(self):
        for junk in ("", "not-base64!!", "AAAA"):
            self.assertIsNone(self.mod.unwrap_master_key(junk))

    def test_the_master_key_is_never_written_to_disk_in_the_clear(self):
        key = self.mod.generate_master_key()
        self.mod.write_local_state(self.root, key)
        raw = (self.root / "Local State").read_text(encoding="utf-8")
        self.assertNotIn(key.hex(), raw)
        self.assertNotIn(base64_of(key), raw)

    def test_local_state_is_written_0600(self):
        self.mod.write_local_state(self.root, self.mod.generate_master_key())
        mode = (self.root / "Local State").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600, f"Local State is {oct(mode)}")

    def test_an_existing_key_is_never_overwritten(self):
        """Replacing it would orphan every cookie written under it."""
        first = self.mod.generate_master_key()
        self.mod.write_local_state(self.root, first)
        before = (self.root / "Local State").read_text(encoding="utf-8")

        second = self.mod.generate_master_key()
        self.mod.write_local_state(self.root, second)
        after = (self.root / "Local State").read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_merging_preserves_chromium_profile_state(self):
        (self.root / "Local State").write_text(
            json.dumps({"profile": {"exited_cleanly": False}}),
            encoding="utf-8")
        self.mod.write_local_state(self.root, self.mod.generate_master_key())
        data = json.loads((self.root / "Local State").read_text())
        self.assertFalse(data["profile"]["exited_cleanly"])
        self.assertTrue(self.mod.has_os_crypt_key(self.root))

    def test_has_os_crypt_key_is_false_without_one(self):
        self.assertFalse(self.mod.has_os_crypt_key(self.root))

    def test_the_report_names_plaintext_cookies(self):
        """A key being present must never make the report say OK."""
        self.mod.write_local_state(self.root, self.mod.generate_master_key())
        make_cookie_db(self.root / "Cookies", [
            (".x.com", "sid", "PLAINTEXT", b""),
        ])
        state = self.mod.report(self.root)
        self.assertTrue(state["has_key"])
        self.assertEqual(state["plaintext_cookies"], ["sid"])
        self.assertIn("PLAINTEXT", self.mod.format_report(self.root))

    def test_the_report_is_clean_with_no_cookies(self):
        self.mod.write_local_state(self.root, self.mod.generate_master_key())
        state = self.mod.report(self.root)
        self.assertEqual(state["plaintext_cookies"], [])
        self.assertIn("no plaintext cookie values", self.mod.format_report(self.root))


def base64_of(raw: bytes) -> str:
    import base64
    return base64.b64encode(raw).decode()


HAVE_QT = False
try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Qt aborts unless QWebEngineWidgets is imported before the
    # QApplication exists. Import order here is load-bearing.
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtWebEngineCore import QWebEngineProfile

    HAVE_QT = True
except Exception:  # pragma: no cover - environment without Qt
    pass


_APP = None


def _app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication(sys.argv)
    return _APP


@unittest.skipUnless(HAVE_QT, "PyQt6 is unavailable")
class ProfilePolicyTests(unittest.TestCase):
    """The default is the control that does not depend on Chromium.

    Even if the os_crypt key is ignored by this Chromium build, cookies
    are not written at all unless someone opts in.
    """

    def setUp(self):
        _app()
        self._prev = os.environ.pop("BFSB_PERSIST_COOKIES", None)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("BFSB_PERSIST_COOKIES", None)
        else:
            os.environ["BFSB_PERSIST_COOKIES"] = self._prev

    def test_cookies_are_not_persisted_by_default(self):
        from bfsb.core.webengine import create_web_profile

        profile = create_web_profile(private=False)
        self.assertEqual(
            profile.persistentCookiesPolicy(),
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies,
            "cookies are being persisted to disk without being asked for",
        )

    def test_persistence_is_opt_in(self):
        os.environ["BFSB_PERSIST_COOKIES"] = "1"
        from bfsb.core.webengine import create_web_profile

        profile = create_web_profile(private=False)
        self.assertEqual(
            profile.persistentCookiesPolicy(),
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies,
        )

    def test_private_profiles_never_persist_even_when_asked(self):
        os.environ["BFSB_PERSIST_COOKIES"] = "1"
        from bfsb.core.webengine import create_web_profile

        profile = create_web_profile(private=True)
        self.assertEqual(
            profile.persistentCookiesPolicy(),
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies,
            "an incognito profile must not persist cookies under any setting",
        )


if __name__ == "__main__":
    unittest.main()
