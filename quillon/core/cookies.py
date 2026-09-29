"""Cookie records backed by the versioned Quillon vault."""

from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from typing import Iterator, Optional

from cryptography.exceptions import InvalidTag

from .config import PATHS
from .crypto import KEY_MANAGER
from .secure_vault import (
    VaultError,
    VaultIntegrityError,
    VaultKeyProvider,
    VaultStore,
)


class CookieVault:
    def __init__(self) -> None:
        self._store: Optional[VaultStore] = None
        self.last_error: Optional[str] = None
        self._ensure_store()

    def _open_store(self) -> None:
        store = VaultStore(
            PATHS.VAULT_DB,
            VaultKeyProvider(PATHS.VAULT_KEY, vault_db=PATHS.VAULT_DB),
            "cookies",
        )
        self._store = store
        try:
            self._init_legacy_db()
            self._migrate_legacy()
        except Exception:
            self._store = None
            raise

    def _ensure_store(self) -> bool:
        if self._store is not None:
            return True
        try:
            self._open_store()
            self.last_error = None
            return True
        except (VaultError, sqlite3.Error, OSError) as error:
            self.last_error = str(error)
            print(f"[Quillon] Cookie vault unavailable: {error}", file=sys.stderr)
            return False

    @property
    def available(self) -> bool:
        return self._store is not None

    @property
    def storage_mode(self) -> Optional[str]:
        """Where the root key lives, e.g. 'keyring' or 'file-degraded'.

        Read straight from the key provider so the page can tell a vault
        that is saving fine on a file key from one that is not saving at
        all. Those are different problems and must not raise the same
        alarm.
        """
        if self._store is None:
            return None
        return self._store.key_provider.storage_mode

    @contextmanager
    def _legacy_connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(PATHS.COOKIE_DB), timeout=1)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_legacy_db(self) -> None:
        PATHS.COOKIE_DB.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._legacy_connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cookies "
                "(domain TEXT, name TEXT, value BLOB, PRIMARY KEY(domain, name))"
            )
        PATHS.COOKIE_DB.chmod(0o600)

    @staticmethod
    def _record_key(domain: str, name: str) -> str:
        return f"{domain}\0{name}"

    @staticmethod
    def _legacy_aad(domain: str) -> bytes:
        return domain.encode("utf-8")

    def _legacy_rows(self) -> list[tuple[str, str, bytes]]:
        try:
            with self._legacy_connection() as conn:
                rows = conn.execute("SELECT domain, name, value FROM cookies").fetchall()
        except sqlite3.Error:
            return []
        values: list[tuple[str, str, bytes]] = []
        for row in rows:
            if row[0] is None or row[1] is None or row[2] is None:
                continue
            values.append((str(row[0]), str(row[1]), bytes(row[2])))
        return values

    def _delete_legacy(self, keys: list[tuple[str, str]]) -> None:
        if not keys:
            return
        with self._legacy_connection() as conn:
            conn.execute("PRAGMA secure_delete=ON")
            conn.executemany(
                "DELETE FROM cookies WHERE domain=? AND name=?", keys
            )
            conn.commit()

    def _migrate_legacy(self) -> None:
        rows = self._legacy_rows()
        if not rows:
            return
        try:
            cipher = KEY_MANAGER.get_aesgcm()
        except Exception:
            return
        migrated: list[tuple[str, str]] = []
        for domain, name, stored in rows:
            if len(stored) <= 12:
                continue
            try:
                value = cipher.decrypt(
                    stored[:12], stored[12:], self._legacy_aad(domain)
                )
                self._store.put(self._record_key(domain, name), value)
            except (InvalidTag, VaultError, ValueError, UnicodeError, sqlite3.Error):
                continue
            migrated.append((domain, name))
        self._delete_legacy(migrated)

    def set_cookie(self, domain: str, name: str, value: bytes) -> bool:
        if not domain or not name or not self._ensure_store():
            return False
        try:
            self._store.put(self._record_key(domain, name), bytes(value))
            self._delete_legacy([(domain, name)])
            return True
        except (VaultError, sqlite3.Error) as error:
            self.last_error = str(error)
            print(f"[Quillon] Cookie save failed: {error}", file=sys.stderr)
            return False

    def get_cookie(self, domain: str, name: str) -> Optional[bytes]:
        if not self._ensure_store():
            return None
        record_key = self._record_key(domain, name)
        try:
            value = self._store.get(record_key)
        except VaultIntegrityError:
            raise
        if value is not None:
            return value
        try:
            with self._legacy_connection() as conn:
                row = conn.execute(
                    "SELECT value FROM cookies WHERE domain=? AND name=?",
                    (domain, name),
                ).fetchone()
            if row is None:
                return None
            if row[0] is None:
                raise VaultIntegrityError("legacy cookie record is empty")
            cipher = KEY_MANAGER.get_aesgcm()
            stored = bytes(row[0])
            value = cipher.decrypt(
                stored[:12], stored[12:], self._legacy_aad(domain)
            )
            self._store.put(record_key, value)
            self._delete_legacy([(domain, name)])
            return value
        except InvalidTag as error:
            raise VaultIntegrityError("legacy cookie record authentication failed") from error
        except ValueError as error:
            raise VaultIntegrityError("legacy cookie record is invalid") from error
        except sqlite3.Error as error:
            raise VaultError("legacy cookie store is unavailable") from error

    def delete_cookie(self, domain: str, name: str) -> bool:
        if not self._ensure_store():
            return False
        self._store.delete(self._record_key(domain, name))
        self._delete_legacy([(domain, name)])
        return True

    def clear_domain(self, domain: str) -> bool:
        if not self._ensure_store():
            return False
        normalized = domain.strip().lower()
        if not normalized:
            raise ValueError("cookie domain is required")
        keys = [
            record_key
            for record_key in self._store.keys(strict=False)
            if record_key.partition("\0")[0].lower() == normalized
        ]
        for record_key in keys:
            self._store.delete(record_key)
        self._delete_legacy(
            [
                (row[0], row[1])
                for row in self._legacy_rows()
                if row[0].strip().lower() == normalized
            ]
        )
        return True

    def entries_for_origin(self, origin: str) -> list[tuple[str, bytes]]:
        """Every (name, value) held for one origin key.

        The cookie sync needs to replay the whole vault into a profile at
        startup, and keys() only returns the opaque record keys -- the
        plaintext name lives inside the encrypted record, so it has to be
        decrypted to be useful.
        """
        if not origin or not self._ensure_store():
            return []
        found: list[tuple[str, bytes]] = []
        for record_key in self._store.keys(strict=False):
            if record_key.partition("\0")[0] != origin:
                continue
            value = self.get_cookie(origin, record_key.partition("\0")[2])
            if value is not None:
                found.append((record_key.partition("\0")[2], value))
        return found

    def all_entries(self) -> list[tuple[str, str, bytes]]:
        """Every (origin, name, value) in the vault."""
        if not self._ensure_store():
            return []
        out: list[tuple[str, str, bytes]] = []
        for record_key in self._store.keys(strict=False):
            origin, _, name = record_key.partition("\0")
            if not origin or not name:
                continue
            value = self.get_cookie(origin, name)
            if value is not None:
                out.append((origin, name, value))
        return out
