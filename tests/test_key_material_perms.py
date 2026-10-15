"""The legacy key file must be created 0600 with no readable window.

``crypto.KeyManager`` derives the cookie/password key from a salt stored in
``~/.quillon/.hw-key``. It used to write that file with ``Path.write_text``
and ``chmod`` it afterwards, which leaves the salt world-readable for the
instant in between (the process umask decides the initial mode) and follows
a symlink if one is planted -- a write-through primitive. These tests pin
the hardened behaviour: ``O_EXCL|O_NOFOLLOW`` at 0600, and an existing file
re-tightened to 0600.
"""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from quillon.core import crypto
except (ImportError, ModuleNotFoundError):
    crypto = None


@unittest.skipIf(crypto is None, "cryptography is unavailable")
class KeyFilePermissionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.key_file = self.root / "nested" / ".hw-key"
        patcher = mock.patch.object(
            crypto, "PATHS", SimpleNamespace(KEY_FILE=self.key_file)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _mode(path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_new_key_file_is_0600_and_parent_0700(self):
        salt = crypto.KeyManager()._load_or_create_salt()
        self.assertEqual(len(salt), crypto.CRYPTO_CONFIG.SALT_LENGTH)
        self.assertEqual(self._mode(self.key_file), 0o600)
        self.assertEqual(self._mode(self.key_file.parent), 0o700)

    def test_existing_key_file_is_re_tightened(self):
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        self.key_file.write_text("ab" * 16)
        os.chmod(self.key_file, 0o644)
        crypto.KeyManager()._load_or_create_salt()
        self.assertEqual(self._mode(self.key_file), 0o600)

    def test_symlinked_key_file_is_refused_without_write_through(self):
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        target = self.root / "target"
        target.write_text("cd" * 16)
        self.key_file.symlink_to(target)
        with self.assertRaises(RuntimeError):
            crypto.KeyManager()._load_or_create_salt()
        self.assertEqual(target.read_text(), "cd" * 16)


if __name__ == "__main__":
    unittest.main()
