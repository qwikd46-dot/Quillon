"""Versioned authenticated vault for local browser secrets."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


VAULT_VERSION = 2
NONCE_SIZE = 12
KEY_SIZE = 32
KEYRING_SERVICE = "bfsb"


def _installation_id(root: Optional[Path] = None) -> str:
    """Stable id for this checkout.

    The root key is scoped to it so two installs on one machine cannot
    overwrite each other's key -- a single global account meant whichever
    one ran second silently clobbered the first's vault.
    """
    if root is None:
        root = Path(__file__).resolve().parents[2]
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]


KEYRING_ACCOUNT = f"vault-root-v2-{_installation_id()}"

# Pre-scoped vaults, read once and then promoted to KEYRING_ACCOUNT. The
# first entry is the original global account that every install shared.
LEGACY_KEYRING_ACCOUNTS = ("vault-root-v2",)


class VaultError(Exception):
    """Base vault failure."""


class VaultKeyError(VaultError):
    """Raised when the vault key is unavailable or malformed."""


class VaultIntegrityError(VaultError):
    """Raised when authenticated vault data fails verification."""


class VaultKeyProvider:
    """Loads a root key from the OS keyring, falling back to a key file.

    The keyring is always preferred and is used automatically whenever it
    works. A file key is the fallback for machines with no Secret Service,
    and it no longer needs an environment variable to be allowed: refusing
    to persist instead left a browser whose passwords silently never
    saved, which is the failure the in-page vault notice reports.

    The fallback is only ever taken when no key material can be found at
    all. If a vault database already exists and its key is unreachable --
    keyring present but locked, key file deleted -- this fails closed
    rather than minting a new key, because a fresh key would orphan the
    stored records and look to the user like data loss.
    """

    def __init__(
        self,
        path: Path,
        root_key: Optional[bytes] = None,
        keyring_module: object = None,
        vault_db: Optional[Path] = None,
    ) -> None:
        self.path = path.expanduser()
        self._root_key = self._validate(root_key) if root_key is not None else None
        self._keyring = keyring_module
        self._vault_db = vault_db.expanduser() if vault_db is not None else None
        self._storage_mode: Optional[str] = None
        self._keyring_unreadable: Optional[str] = None
        self._keyring_legacy_account: Optional[str] = None
        if self._keyring is None:
            try:
                import keyring as keyring_module
                self._keyring = keyring_module
            except Exception:
                self._keyring = None

    @staticmethod
    def _validate(key: bytes) -> bytes:
        if len(key) != KEY_SIZE:
            raise VaultKeyError("vault root key must be 32 bytes")
        return bytes(key)

    @property
    def storage_mode(self) -> Optional[str]:
        return self._storage_mode

    @property
    def degraded(self) -> bool:
        """True when the key sits in a file because no keyring was usable.

        Persistence still works, so callers that only care whether
        saving is possible should ignore this. It exists so the UI can
        say the key is on disk rather than in the OS keyring.
        """
        return self._storage_mode == "file-degraded"

    def _vault_database_exists(self) -> bool:
        """True when a non-empty vault database is already on disk."""
        if self._vault_db is None:
            return False
        try:
            return self._vault_db.is_file() and self._vault_db.stat().st_size > 0
        except OSError:
            return False

    def _read_key_file(self) -> bytes:
        if self.path.is_symlink():
            raise VaultKeyError("vault key file must not be a symlink")
        try:
            value = bytes.fromhex(self.path.read_text(encoding="ascii").strip())
        except (OSError, ValueError) as error:
            raise VaultKeyError("vault key file is unreadable") from error
        return self._validate(value)

    def _create_key_file(self) -> bytes:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        key = secrets.token_bytes(KEY_SIZE)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.path, flags, 0o600)
        except FileExistsError:
            return self._read_key_file()
        try:
            os.write(fd, key.hex().encode("ascii") + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        self.path.chmod(0o600)
        return key

    def _keyring_read(self) -> Optional[bytes]:
        if self._keyring is None:
            return None
        # Read the scoped account first, then any pre-scoped one so an
        # existing vault keeps opening. A raise is remembered: an
        # unreadable keyring must never look like an empty one, or the
        # caller mints a fresh key that orphans the stored records.
        unreadable = None
        for account in (KEYRING_ACCOUNT, *LEGACY_KEYRING_ACCOUNTS):
            try:
                value = self._keyring.get_password(KEYRING_SERVICE, account)
            except Exception as error:
                unreadable = str(error) or "keyring read failed"
                continue
            if not value:
                continue
            try:
                key = self._validate(bytes.fromhex(value))
            except (TypeError, ValueError, VaultKeyError):
                continue
            self._keyring_unreadable = unreadable
            self._keyring_legacy_account = (
                None if account == KEYRING_ACCOUNT else account
            )
            return key
        self._keyring_unreadable = unreadable
        self._keyring_legacy_account = None
        return None

    def _keyring_promote_legacy(self, key: bytes) -> None:
        """Copy a pre-scoped key into the scoped account, then drop it.

        Best effort: if this fails the vault still opens from the legacy
        account on the next run, so it must never raise.
        """
        account = self._keyring_legacy_account
        if account is None or self._keyring is None:
            return
        if self._keyring_write(key):
            try:
                self._keyring.delete_password(KEYRING_SERVICE, account)
            except Exception as error:
                print(
                    f"[bfsb] old vault key {account} could not be removed from "
                    f"the keyring ({error}); the copy under "
                    f"{KEYRING_ACCOUNT} is authoritative"
                )
        self._keyring_legacy_account = None

    def _keyring_write(self, key: bytes) -> bool:
        if self._keyring is None:
            return False
        try:
            self._keyring.set_password(
                KEYRING_SERVICE, KEYRING_ACCOUNT, key.hex()
            )
            stored = self._keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
            return bool(stored) and hmac.compare_digest(
                str(stored).encode("ascii"), key.hex().encode("ascii")
            )
        except Exception:
            return False

    def get_root_key(self) -> bytes:
        if self._root_key is not None:
            # Only claim "provided" if nothing better is already known.
            # Overwriting it here used to relabel a keyring or file key as
            # "provided" on every call after the first, which made
            # storage_mode (and degraded) wrong for any long-lived vault.
            if self._storage_mode is None:
                self._storage_mode = "provided"
            return self._root_key
        environment_key = (
            os.environ.get("BFSB_VAULT_KEY")
            if os.environ.get("BFSB_VAULT_ALLOW_ENV_KEY") == "1"
            else None
        )
        if environment_key:
            try:
                self._root_key = self._validate(bytes.fromhex(environment_key))
            except (ValueError, VaultKeyError) as error:
                raise VaultKeyError("BFSB_VAULT_KEY must be 32-byte hex") from error
            self._storage_mode = "environment"
            return self._root_key
        keyring_key = self._keyring_read()
        if keyring_key is not None:
            self._keyring_promote_legacy(keyring_key)
            self._root_key = keyring_key
            self._storage_mode = "keyring"
            return self._root_key
        if self.path.exists():
            file_key = self._read_key_file()
            promoted = self._keyring_write(file_key)
            if promoted:
                try:
                    self.path.unlink()
                except OSError as error:
                    # The key is in the keyring, but a copy is still on
                    # disk. Reporting "keyring" here would make degraded
                    # False and hide that copy, which is the one thing the
                    # file fallback exists to avoid.
                    self._root_key = file_key
                    self._storage_mode = "file-degraded"
                    print(
                        f"[bfsb] vault key file {self.path} could not be removed "
                        f"({error}); it stays on disk alongside the keyring copy"
                    )
                    return self._root_key
            self._root_key = file_key
            self._storage_mode = "keyring" if promoted else "file"
            return self._root_key
        # The keyring answered but could not be read. Minting a key here
        # would overwrite the real one if the write happens to succeed --
        # a keyring that errors on read can still accept writes, which is
        # what a locked-then-unlocked or partially readable keyring looks
        # like. Records already on disk would be orphaned for good, so
        # refuse instead. Only safe when there is nothing to lose.
        if self._keyring_unreadable and self._vault_database_exists():
            raise VaultKeyError(
                "an existing vault cannot be opened: the OS keyring could not "
                f"be read ({self._keyring_unreadable}). Unlock the keyring and "
                f"retry, or restore {self.path}"
            )
        new_key = secrets.token_bytes(KEY_SIZE)
        if self._keyring_write(new_key):
            self._root_key = new_key
            self._storage_mode = "keyring"
            return self._root_key
        # No keyring to hold a new key. If a vault already exists, its key
        # is somewhere we cannot reach right now, so refuse rather than
        # overwrite the only way back into the stored records.
        if self._vault_database_exists():
            raise VaultKeyError(
                "an existing vault cannot be opened: no OS keyring is available "
                "and no vault key file was found. Unlock the keyring, or "
                "restore ~/.bfsb/vault.key"
            )
        # Nothing to lose: this is a first run with no key anywhere. Persist
        # with a 0600 key file instead of silently not saving anything.
        # BFSB_VAULT_ALLOW_FILE_KEY is no longer required for this and is
        # accepted only so an existing setting does not become an error.
        self._root_key = self._create_key_file()
        self._storage_mode = "file-degraded"
        return self._root_key

    def derive(self, purpose: str, info: bytes) -> bytes:
        if not purpose:
            raise VaultKeyError("vault purpose is required")
        purpose_bytes = purpose.encode("utf-8")
        framed = (
            len(purpose_bytes).to_bytes(4, "big") + purpose_bytes + info
        )
        return HKDF(
            algorithm=hashes.SHA256(),
            length=KEY_SIZE,
            salt=None,
            info=b"bfsb-vault-v2/" + framed,
        ).derive(self.get_root_key())

    def key_check(self) -> bytes:
        check_key = self.derive("meta", b"key-check")
        return hmac.new(check_key, b"bfsb-vault-key-check-v2", hashlib.sha256).digest()


class VaultStore:
    """SQLite-backed AEAD store with encrypted record keys."""

    def __init__(
        self,
        db_path: Path,
        key_provider: VaultKeyProvider,
        purpose: str,
    ) -> None:
        self.db_path = db_path.expanduser()
        self.key_provider = key_provider
        self.purpose = purpose
        if not purpose:
            raise VaultKeyError("vault purpose is required")
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db_path.parent.chmod(0o700)
        expected_key_check = self.key_provider.key_check()
        created = self._ensure_db_file()
        self._initialize_schema(created, expected_key_check)

    def _ensure_db_file(self) -> bool:
        if self.db_path.is_symlink():
            raise VaultKeyError("vault database must not be a symlink")
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.db_path, flags, 0o600)
        except FileExistsError:
            if not self.db_path.is_file() or self.db_path.is_symlink():
                raise VaultKeyError("vault database path is not a regular file")
            self.db_path.chmod(0o600)
            return False
        except OSError as error:
            raise VaultError("vault database cannot be created") from error
        os.close(fd)
        self.db_path.chmod(0o600)
        return True

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=1)
        except sqlite3.Error as error:
            raise VaultError("vault database cannot be opened") from error
        try:
            conn.execute("PRAGMA secure_delete=ON")
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA trusted_schema=OFF")
            conn.execute("PRAGMA busy_timeout=1000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _create_schema(self) -> None:
        with self._connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS vault_records ("
                "purpose TEXT NOT NULL, lookup BLOB NOT NULL, "
                "key_version INTEGER NOT NULL, updated_at REAL NOT NULL, "
                "payload BLOB NOT NULL, PRIMARY KEY(purpose, lookup))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS vault_meta ("
                "name TEXT PRIMARY KEY, value BLOB NOT NULL)"
            )

    def _initialize_schema(self, created: bool, expected_key_check: bytes) -> None:
        if not created and self.db_path.stat().st_size == 0:
            quarantine = self.db_path.with_name(
                f"{self.db_path.name}.empty-{int(time.time())}"
            )
            try:
                os.replace(self.db_path, quarantine)
            except OSError as error:
                raise VaultError("empty vault database cannot be quarantined") from error
            self._ensure_db_file()
        for attempt in range(2):
            try:
                self._create_schema()
                self._check_or_store_key(expected_key_check)
                return
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower() or attempt == 1:
                    raise VaultError("vault database is busy") from error
                time.sleep(0.1)
            except sqlite3.DatabaseError as error:
                message = str(error).lower()
                corrupt = any(
                    marker in message
                    for marker in ("not a database", "malformed", "file is encrypted")
                )
                if not corrupt:
                    raise VaultError("vault database cannot be opened") from error
                quarantine = self.db_path.with_name(
                    f"{self.db_path.name}.corrupt-{int(time.time())}"
                )
                try:
                    os.replace(self.db_path, quarantine)
                except OSError as move_error:
                    raise VaultError("vault database is corrupt") from move_error
                self._ensure_db_file()
                self._create_schema()
                self._check_or_store_key(expected_key_check)
                # The records are still on disk in the quarantine file, but
                # this store is now empty. Returning quietly made every
                # saved password and cookie vanish from the UI while the
                # vault still reported itself healthy, so the in-page
                # notice stayed hidden at exactly the moment the user
                # needed it. Refuse instead, and name the file so the data
                # is recoverable.
                raise VaultError(
                    "vault database was corrupt; your records were kept in "
                    f"{quarantine.name} and are not loaded. Restore that file "
                    f"to {self.db_path.name} to recover them."
                )
        raise VaultError("vault database is busy")

    def _check_or_store_key(self, expected: bytes) -> None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT value FROM vault_meta WHERE name='key-check'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO vault_meta(name, value) VALUES ('key-check', ?)",
                    (expected,),
                )
                return
        if not hmac.compare_digest(bytes(row[0]), expected):
            raise VaultKeyError("vault key does not match this vault")

    def _cipher(self) -> AESGCM:
        return AESGCM(self.key_provider.derive(self.purpose, b"record-key"))

    def _index_key(self) -> bytes:
        return self.key_provider.derive(self.purpose, b"index-key")

    def _lookup(self, record_key: str) -> bytes:
        return hmac.new(
            self._index_key(),
            record_key.encode("utf-8"),
            hashlib.sha256,
        ).digest()

    def _aad(self, lookup: bytes) -> bytes:
        return (
            b"bfsb-vault-v2\0"
            + VAULT_VERSION.to_bytes(4, "big")
            + self.purpose.encode("utf-8")
            + b"\0"
            + lookup
        )

    def _write(self, callback) -> None:
        for attempt in range(2):
            try:
                with self._connection() as conn:
                    callback(conn)
                return
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower() or attempt == 1:
                    raise VaultError("vault database is busy") from error
                time.sleep(0.1)

    def put(self, record_key: str, value: bytes) -> None:
        if not record_key:
            raise VaultError("record key is required")
        lookup = self._lookup(record_key)
        nonce = os.urandom(NONCE_SIZE)
        payload = json.dumps(
            {
                "key": record_key,
                "value": base64.b64encode(bytes(value)).decode("ascii"),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        encrypted = self._cipher().encrypt(nonce, payload, self._aad(lookup))
        self._write(
            lambda conn: conn.execute(
                "INSERT OR REPLACE INTO vault_records "
                "(purpose, lookup, key_version, updated_at, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (self.purpose, lookup, VAULT_VERSION, time.time(), nonce + encrypted),
            )
        )
        self.db_path.chmod(0o600)

    def get(self, record_key: str) -> Optional[bytes]:
        if not record_key:
            return None
        lookup = self._lookup(record_key)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT key_version, payload FROM vault_records "
                "WHERE purpose=? AND lookup=?",
                (self.purpose, lookup),
            ).fetchone()
        if row is None:
            return None
        version, stored = int(row[0]), bytes(row[1])
        if version != VAULT_VERSION or len(stored) <= NONCE_SIZE:
            raise VaultIntegrityError("unsupported vault record")
        try:
            payload = self._cipher().decrypt(
                stored[:NONCE_SIZE], stored[NONCE_SIZE:], self._aad(lookup)
            )
            data = json.loads(payload.decode("utf-8"))
            if data.get("key") != record_key:
                raise VaultIntegrityError("vault record key mismatch")
            return base64.b64decode(data["value"], validate=True)
        except VaultIntegrityError:
            raise
        except Exception as error:
            raise VaultIntegrityError("vault record authentication failed") from error

    def delete(self, record_key: str) -> None:
        lookup = self._lookup(record_key)
        self._write(
            lambda conn: conn.execute(
                "DELETE FROM vault_records WHERE purpose=? AND lookup=?",
                (self.purpose, lookup),
            )
        )

    def keys(self, strict: bool = True) -> list[str]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT lookup, key_version, payload FROM vault_records "
                "WHERE purpose=? ORDER BY updated_at",
                (self.purpose,),
            ).fetchall()
        values: list[str] = []
        for lookup, version, stored in rows:
            lookup_bytes = bytes(lookup)
            try:
                if int(version) != VAULT_VERSION or len(stored) <= NONCE_SIZE:
                    raise VaultIntegrityError("unsupported vault record")
                payload = self._cipher().decrypt(
                    bytes(stored)[:NONCE_SIZE],
                    bytes(stored)[NONCE_SIZE:],
                    self._aad(lookup_bytes),
                )
                values.append(str(json.loads(payload.decode("utf-8"))["key"]))
            except VaultKeyError:
                raise
            except Exception as error:
                if strict:
                    raise VaultIntegrityError("vault record authentication failed") from error
        return sorted(values)

    def purge(self) -> None:
        self._write(
            lambda conn: conn.execute(
                "DELETE FROM vault_records WHERE purpose=?", (self.purpose,)
            )
        )
        try:
            with self._connection() as conn:
                conn.execute("VACUUM")
        except sqlite3.Error:
            pass
