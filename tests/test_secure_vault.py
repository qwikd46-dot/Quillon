import importlib.util
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
try:
    import cryptography  # noqa: F401
    HAVE_CRYPTOGRAPHY = True
except ImportError:
    HAVE_CRYPTOGRAPHY = False


def load_secure_vault():
    name = "bfsb_secure_vault_test"
    path = ROOT / "bfsb/core/secure_vault.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(HAVE_CRYPTOGRAPHY, "cryptography is unavailable")
class SecureVaultTests(unittest.TestCase):
    def setUp(self):
        self.vault = load_secure_vault()
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.key = self.vault.VaultKeyProvider(
            self.root / "vault.key", root_key=b"k" * self.vault.KEY_SIZE
        )
        self.store = self.vault.VaultStore(
            self.root / "vault.enc", self.key, "passwords"
        )

    def tearDown(self):
        self.directory.cleanup()

    def test_key_and_database_permissions_are_restrictive(self):
        self.store.put("origin\0user", b"secret")
        self.assertEqual(stat.S_IMODE((self.root / "vault.enc").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)

    def test_explicit_file_fallback_is_0600(self):
        class UnavailableKeyring:
            def get_password(self, service, account):
                raise RuntimeError("unavailable")

            def set_password(self, service, account, value):
                raise RuntimeError("unavailable")

        previous = os.environ.get("BFSB_VAULT_ALLOW_FILE_KEY")
        os.environ["BFSB_VAULT_ALLOW_FILE_KEY"] = "1"
        try:
            provider = self.vault.VaultKeyProvider(
                self.root / "fallback.key", keyring_module=UnavailableKeyring()
            )
            provider.get_root_key()
        finally:
            if previous is None:
                os.environ.pop("BFSB_VAULT_ALLOW_FILE_KEY", None)
            else:
                os.environ["BFSB_VAULT_ALLOW_FILE_KEY"] = previous
        self.assertEqual(stat.S_IMODE((self.root / "fallback.key").stat().st_mode), 0o600)

    def test_records_are_encrypted_and_authenticated(self):
        self.store.put("origin\0user", b"secret-value")
        self.assertEqual(self.store.get("origin\0user"), b"secret-value")
        raw = (self.root / "vault.enc").read_bytes()
        self.assertNotIn(b"secret-value", raw)
        self.assertNotIn(b"origin", raw)
        with sqlite3.connect(str(self.root / "vault.enc")) as conn:
            row = conn.execute("SELECT payload FROM vault_records").fetchone()
        stored = bytes(row[0])
        stored = stored[:-1] + bytes([stored[-1] ^ 1])
        with sqlite3.connect(str(self.root / "vault.enc")) as conn:
            conn.execute("UPDATE vault_records SET payload=?", (stored,))
        with self.assertRaises(self.vault.VaultIntegrityError):
            self.store.get("origin\0user")

    def test_wrong_root_key_cannot_open_existing_vault(self):
        self.store.put("origin\0user", b"secret-value")
        wrong = self.vault.VaultKeyProvider(
            self.root / "wrong.key", root_key=b"w" * self.vault.KEY_SIZE
        )
        with self.assertRaises(self.vault.VaultKeyError):
            self.vault.VaultStore(self.root / "vault.enc", wrong, "passwords")

    def test_keyring_is_preferred_over_file_storage(self):
        class FakeKeyring:
            def __init__(self):
                self.values = {}

            def get_password(self, service, account):
                return self.values.get((service, account))

            def set_password(self, service, account, value):
                self.values[(service, account)] = value

        keyring = FakeKeyring()
        provider = self.vault.VaultKeyProvider(
            self.root / "keyring-vault.key", keyring_module=keyring
        )
        first = provider.get_root_key()
        self.assertEqual(provider.storage_mode, "keyring")
        self.assertFalse((self.root / "keyring-vault.key").exists())
        second = self.vault.VaultKeyProvider(
            self.root / "keyring-vault.key", keyring_module=keyring
        ).get_root_key()
        self.assertEqual(first, second)

    def test_missing_keyring_falls_back_to_a_file_key_without_any_env_var(self):
        """A first run with no Secret Service must still persist.

        This used to fail closed behind BFSB_VAULT_ALLOW_FILE_KEY, which
        no launcher set, so a browser on such a machine silently never
        saved a password.
        """
        class UnavailableKeyring:
            def get_password(self, service, account):
                raise RuntimeError("unavailable")

            def set_password(self, service, account, value):
                raise RuntimeError("unavailable")

        previous = os.environ.pop("BFSB_VAULT_ALLOW_FILE_KEY", None)
        try:
            provider = self.vault.VaultKeyProvider(
                self.root / "auto.key", keyring_module=UnavailableKeyring()
            )
            key = provider.get_root_key()
        finally:
            if previous is not None:
                os.environ["BFSB_VAULT_ALLOW_FILE_KEY"] = previous
        self.assertEqual(len(key), self.vault.KEY_SIZE)
        self.assertTrue((self.root / "auto.key").exists())
        self.assertEqual(stat.S_IMODE((self.root / "auto.key").stat().st_mode), 0o600)
        self.assertEqual(provider.storage_mode, "file-degraded")
        self.assertTrue(provider.degraded)

    def test_degraded_file_key_still_stores_and_reads_records(self):
        class UnavailableKeyring:
            def get_password(self, service, account):
                raise RuntimeError("unavailable")

            def set_password(self, service, account, value):
                raise RuntimeError("unavailable")

        provider = self.vault.VaultKeyProvider(
            self.root / "degraded.key", keyring_module=UnavailableKeyring()
        )
        store = self.vault.VaultStore(
            self.root / "degraded.enc", provider, "passwords"
        )
        store.put("origin\0user", b"secret-value")
        self.assertEqual(store.get("origin\0user"), b"secret-value")

    def test_existing_vault_is_never_overwritten_with_a_new_key(self):
        """The guarantee that replaced the blanket refusal.

        A vault database exists but its key cannot be reached, so a fresh
        key would orphan the stored records. This must fail closed.
        """
        class UnavailableKeyring:
            def get_password(self, service, account):
                raise RuntimeError("unavailable")

            def set_password(self, service, account, value):
                raise RuntimeError("unavailable")

        db = self.root / "locked.enc"
        self.store.put("origin\0user", b"secret-value")
        db.write_bytes((self.root / "vault.enc").read_bytes())

        provider = self.vault.VaultKeyProvider(
            self.root / "absent.key",
            keyring_module=UnavailableKeyring(),
            vault_db=db,
        )
        with self.assertRaises(self.vault.VaultKeyError):
            provider.get_root_key()
        # No stray key minted over data that already exists.
        self.assertFalse((self.root / "absent.key").exists())

    def test_locked_keyring_with_stored_records_keeps_the_data_intact(self):
        class UnavailableKeyring:
            def get_password(self, service, account):
                raise RuntimeError("unavailable")

            def set_password(self, service, account, value):
                raise RuntimeError("unavailable")

        provider = self.vault.VaultKeyProvider(
            self.root / "gone.key",
            keyring_module=UnavailableKeyring(),
            vault_db=self.root / "vault.enc",
        )
        self.store.put("origin\0user", b"secret-value")
        with self.assertRaises(self.vault.VaultKeyError):
            provider.get_root_key()
        # The original key still opens the original records.
        self.assertEqual(self.store.get("origin\0user"), b"secret-value")

    def test_keyring_that_fails_read_but_accepts_writes_does_not_clobber_the_key(self):
        """A keyring can reject reads while still accepting writes.

        That is what a locked-then-unlocked or partially readable keyring
        looks like, and it is the one case the existing refusal missed:
        both locked-keyring tests use a keyring that fails writes too, so
        the later guard caught them. Minting a key here would write it
        over the real one and orphan every stored record permanently.
        """
        class ReadFailsWriteSucceeds:
            def __init__(self):
                self.writes = []

            def get_password(self, service, account):
                raise RuntimeError("keyring is locked")

            def set_password(self, service, account, value):
                self.writes.append(value)

        db = self.root / "half.enc"
        self.store.put("origin\0user", b"secret-value")
        db.write_bytes((self.root / "vault.enc").read_bytes())

        keyring = ReadFailsWriteSucceeds()
        provider = self.vault.VaultKeyProvider(
            self.root / "absent.key",
            keyring_module=keyring,
            vault_db=db,
        )
        with self.assertRaises(self.vault.VaultKeyError):
            provider.get_root_key()
        # The unreadable keyring must never be written to here.
        self.assertEqual(keyring.writes, [])
        self.assertFalse((self.root / "absent.key").exists())
        # And the real records still open with the real key.
        self.assertEqual(self.store.get("origin\0user"), b"secret-value")

    def test_unreadable_keyring_still_allows_a_first_run_key(self):
        """The guard must not block a vault that has no data to lose.

        Refusing here would strand a genuinely new install with no way to
        create its first key.
        """
        class ReadFailsWriteSucceeds:
            def get_password(self, service, account):
                raise RuntimeError("keyring is locked")

            def set_password(self, service, account, value):
                pass

        provider = self.vault.VaultKeyProvider(
            self.root / "fresh.key",
            keyring_module=ReadFailsWriteSucceeds(),
            vault_db=self.root / "brand-new.enc",
        )
        self.assertEqual(len(provider.get_root_key()), self.vault.KEY_SIZE)

    def test_corrupt_database_is_quarantined_but_never_silently_emptied(self):
        """A corrupt vault must not quietly become an empty one.

        Quarantining and returning made every password and cookie vanish
        while the store still reported itself healthy, so the in-page
        notice stayed hidden at exactly the moment it was needed.
        """
        store = self.store
        store.put("origin\0user", b"secret-value")
        (self.root / "vault.enc").write_bytes(b"this is not a database" * 64)

        with self.assertRaises(self.vault.VaultError) as caught:
            self.vault.VaultStore(
                self.root / "vault.enc",
                self.vault.VaultKeyProvider(
                    self.root / "vault.key", root_key=b"k" * self.vault.KEY_SIZE
                ),
                "passwords",
            )
        message = str(caught.exception)
        self.assertIn("corrupt", message)
        # The records are still on disk and the user is told where.
        self.assertIn(".corrupt-", message)
        quarantined = list(self.root.glob("vault.enc.corrupt-*"))
        self.assertEqual(len(quarantined), 1, "records must not be deleted")
        self.assertTrue(quarantined[0].exists())

    def test_failed_key_cleanup_keeps_reporting_degraded(self):
        """If the key file cannot be removed after promotion, the store
        must not claim the key lives only in the OS keyring."""
        import bfsb.core.secure_vault as module

        class WorkingKeyring:
            def __init__(self):
                self.values = {}

            def get_password(self, service, account):
                return self.values.get((service, account))

            def set_password(self, service, account, value):
                self.values[(service, account)] = value

        keyring = WorkingKeyring()
        key_path = self.root / "leftover.key"
        provider = module.VaultKeyProvider(key_path, keyring_module=keyring)
        # Produce the file, the way a first run without a keyring would.
        provider._create_key_file()
        self.assertTrue(key_path.exists())

        real_unlink = Path.unlink

        def refuse(self, *a, **k):
            if self.name == "leftover.key":
                raise OSError("read-only file system")
            return real_unlink(self, *a, **k)

        Path.unlink = refuse
        try:
            fresh = module.VaultKeyProvider(key_path, keyring_module=keyring)
            fresh.get_root_key()
        finally:
            Path.unlink = real_unlink

        self.assertTrue(key_path.exists(), "the unlink was supposed to be refused")
        self.assertEqual(
            fresh.storage_mode, "file-degraded",
            "a key left on disk must not be reported as keyring-only",
        )
        self.assertTrue(fresh.degraded)

    def test_storage_mode_is_stable_across_repeated_calls(self):
        """get_root_key used to relabel the mode as "provided" every time
        it was called with the key already cached, so a long-lived vault
        reported the wrong storage mode and never looked degraded."""
        class FakeKeyring:
            def __init__(self):
                self.values = {}

            def get_password(self, service, account):
                return self.values.get((service, account))

            def set_password(self, service, account, value):
                self.values[(service, account)] = value

        keyring = FakeKeyring()
        provider = self.vault.VaultKeyProvider(
            self.root / "stable.key", keyring_module=keyring
        )
        for _ in range(3):
            provider.get_root_key()
        self.assertEqual(provider.storage_mode, "keyring")
        self.assertFalse(provider.degraded)

        class UnavailableKeyring:
            def get_password(self, service, account):
                raise RuntimeError("unavailable")

            def set_password(self, service, account, value):
                raise RuntimeError("unavailable")

        degraded = self.vault.VaultKeyProvider(
            self.root / "stable-degraded.key", keyring_module=UnavailableKeyring()
        )
        for _ in range(3):
            degraded.get_root_key()
        self.assertEqual(degraded.storage_mode, "file-degraded")
        self.assertTrue(degraded.degraded)

        provided = self.vault.VaultKeyProvider(
            self.root / "stable-provided.key", root_key=b"p" * self.vault.KEY_SIZE
        )
        for _ in range(3):
            provided.get_root_key()
        self.assertEqual(provided.storage_mode, "provided")

    def test_zero_length_database_is_quarantined(self):
        self.store.put("origin\0user", b"secret-value")
        (self.root / "vault.enc").write_bytes(b"")
        replacement = self.vault.VaultStore(
            self.root / "vault.enc", self.key, "passwords"
        )
        self.assertEqual(replacement.keys(), [])
        self.assertEqual(len(list(self.root.glob("vault.enc.empty-*"))), 1)

    def test_locked_database_is_not_quarantined(self):
        self.store.put("origin\0user", b"secret-value")
        blocker = sqlite3.connect(str(self.root / "vault.enc"), timeout=1)
        blocker.execute("BEGIN EXCLUSIVE")
        try:
            with self.assertRaises(self.vault.VaultError):
                self.vault.VaultStore(
                    self.root / "vault.enc", self.key, "passwords"
                )
            self.assertTrue((self.root / "vault.enc").exists())
            self.assertEqual(list(self.root.glob("vault.enc.corrupt-*")), [])
        finally:
            blocker.rollback()
            blocker.close()

    def test_purpose_separation_binds_records(self):
        other = self.vault.VaultStore(
            self.root / "other-purpose.enc", self.key, "cookies"
        )
        self.store.put("same-key", b"password")
        other.put("same-key", b"cookie")
        self.assertEqual(self.store.get("same-key"), b"password")
        self.assertEqual(other.get("same-key"), b"cookie")

    def test_keys_delete_and_purge(self):
        self.store.put("a\0one", b"1")
        self.store.put("b\0two", b"2")
        self.assertEqual(self.store.keys(), ["a\0one", "b\0two"])
        self.store.delete("a\0one")
        self.assertIsNone(self.store.get("a\0one"))
        self.store.purge()
        self.assertEqual(self.store.keys(), [])


class FakeKeyring:
    """Minimal in-memory stand-in for the OS keyring."""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class ScopedKeyringAccountTests(unittest.TestCase):
    """The root key is scoped per installation, with a legacy migration.

    A single global account meant whichever install touched the keyring
    last silently replaced every other install's key, orphaning its
    records. The real keyring still holds the old global account, so the
    migration has to open that vault before it retires the entry.
    """

    def setUp(self):
        if not HAVE_CRYPTOGRAPHY:
            self.skipTest("cryptography is unavailable")
        self.vault = load_secure_vault()
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def _store_with(self, key, purpose="passwords"):
        return self.vault.VaultStore(
            self.root / f"{purpose}.enc",
            self.vault.VaultKeyProvider(
                self.root / "unused.key", root_key=key
            ),
            purpose,
        )

    def test_account_is_scoped_not_the_global_one(self):
        self.assertNotEqual(
            self.vault.KEYRING_ACCOUNT,
            "vault-root-v2",
            "the root key must not use the shared global account",
        )
        self.assertTrue(self.vault.KEYRING_ACCOUNT.startswith("vault-root-v2-"))

    def test_a_legacy_vault_still_opens_and_is_promoted(self):
        key = b"k" * self.vault.KEY_SIZE
        store = self._store_with(key)
        store.put("origin\0user", b"legacy-secret")

        keyring = FakeKeyring({("bfsb", "vault-root-v2"): key.hex()})
        provider = self.vault.VaultKeyProvider(
            self.root / "absent.key", keyring_module=keyring
        )
        # The real key is recovered, so the old records still open.
        self.assertEqual(provider.get_root_key(), key)

        # And it is moved onto the scoped account, with the old entry gone.
        self.assertEqual(
            keyring.values[("bfsb", self.vault.KEYRING_ACCOUNT)], key.hex()
        )
        self.assertNotIn(("bfsb", "vault-root-v2"), keyring.values)

    def test_promotion_never_happens_without_a_verified_write(self):
        """If the scoped write cannot be verified, the legacy entry stays.

        Deleting first and trusting the copy would be the one way to lose
        the key outright.
        """
        key = b"k" * self.vault.KEY_SIZE

        class FailsToStore(FakeKeyring):
            def set_password(self, service, account, value):
                # Pretend the write landed; the read-back check catches it.
                self.values[(service, account)] = "not-the-key"

        keyring = FailsToStore({("bfsb", "vault-root-v2"): key.hex()})
        provider = self.vault.VaultKeyProvider(
            self.root / "absent.key", keyring_module=keyring
        )
        self.assertEqual(provider.get_root_key(), key)
        # The legacy entry is the only good copy left, so it must survive.
        self.assertEqual(keyring.values[("bfsb", "vault-root-v2")], key.hex())
        # And the bad scoped write was rejected rather than trusted.
        self.assertNotEqual(
            keyring.values.get(("bfsb", self.vault.KEYRING_ACCOUNT)), key.hex()
        )

    def test_the_scoped_account_wins_when_both_exist(self):
        scoped, legacy = b"s" * self.vault.KEY_SIZE, b"l" * self.vault.KEY_SIZE
        keyring = FakeKeyring({
            ("bfsb", self.vault.KEYRING_ACCOUNT): scoped.hex(),
            ("bfsb", "vault-root-v2"): legacy.hex(),
        })
        provider = self.vault.VaultKeyProvider(
            self.root / "absent.key", keyring_module=keyring
        )
        self.assertEqual(provider.get_root_key(), scoped)
        # The stale global entry is left alone, not deleted.
        self.assertIn(("bfsb", "vault-root-v2"), keyring.values)

    def test_two_installations_never_share_a_key(self):
        """The whole point: two checkouts must not overwrite each other."""
        root_a, root_b = self.root / "install-a", self.root / "install-b"
        account_a = f"vault-root-v2-{self.vault._installation_id(root_a)}"
        account_b = f"vault-root-v2-{self.vault._installation_id(root_b)}"
        self.assertNotEqual(account_a, account_b)

        key_a, key_b = b"1" * self.vault.KEY_SIZE, b"2" * self.vault.KEY_SIZE
        keyring = FakeKeyring({
            ("bfsb", account_a): key_a.hex(),
            ("bfsb", account_b): key_b.hex(),
        })

        # A single global account would hold one of these and lose the
        # other. Scoped accounts keep both keys independently readable.
        self.assertEqual(
            bytes.fromhex(keyring.get_password("bfsb", account_a)), key_a
        )
        self.assertEqual(
            bytes.fromhex(keyring.get_password("bfsb", account_b)), key_b
        )
        self.assertEqual(len(keyring.values), 2)

    def test_different_roots_get_different_accounts(self):
        self.assertNotEqual(
            self.vault._installation_id(self.root / "one"),
            self.vault._installation_id(self.root / "two"),
        )
        # And stable for the same root, so the key does not drift.
        self.assertEqual(
            self.vault._installation_id(self.root / "one"),
            self.vault._installation_id(self.root / "one"),
        )

    def test_an_unreadable_legacy_read_does_not_orphan_the_vault(self):
        """Failing to read the scoped account is not an empty vault."""
        key = b"k" * self.vault.KEY_SIZE

        class ScopedFails(FakeKeyring):
            def get_password(self, service, account):
                if account == self.scoped_account:
                    raise RuntimeError("keyring is locked")
                return self.values.get((service, account))

        keyring = ScopedFails({("bfsb", "vault-root-v2"): key.hex()})
        keyring.scoped_account = self.vault.KEYRING_ACCOUNT
        provider = self.vault.VaultKeyProvider(
            self.root / "absent.key", keyring_module=keyring
        )
        # The legacy key is still usable, so nothing is lost.
        self.assertEqual(provider.get_root_key(), key)


if __name__ == "__main__":
    unittest.main()
