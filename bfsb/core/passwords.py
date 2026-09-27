"""Encrypted password records backed by the versioned BFSB vault."""

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


class PasswordVault:
    def __init__(self) -> None:
        self._store: Optional[VaultStore] = None
        self.last_error: Optional[str] = None
        self._ensure_store()

    def _open_store(self) -> None:
        store = VaultStore(
            PATHS.VAULT_DB,
            VaultKeyProvider(PATHS.VAULT_KEY, vault_db=PATHS.VAULT_DB),
            "passwords",
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
            print(f"[BFSB] Password vault unavailable: {error}", file=sys.stderr)
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
        conn = sqlite3.connect(str(PATHS.PASSWORD_DB), timeout=1)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_legacy_db(self) -> None:
        PATHS.PASSWORD_DB.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._legacy_connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS passwords ("
                "origin TEXT NOT NULL, username TEXT NOT NULL, "
                "value BLOB NOT NULL, updated_at REAL NOT NULL, "
                "PRIMARY KEY(origin, username))"
            )
        PATHS.PASSWORD_DB.chmod(0o600)

    @staticmethod
    def _record_key(origin: str, username: str) -> str:
        return f"{origin}\0{username}"

    @staticmethod
    def _legacy_aad(origin: str, username: str) -> bytes:
        return f"{origin}\0{username}".encode("utf-8")

    def _legacy_rows(self) -> list[tuple[str, str, bytes]]:
        try:
            with self._legacy_connection() as conn:
                rows = conn.execute(
                    "SELECT origin, username, value FROM passwords"
                ).fetchall()
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
                "DELETE FROM passwords WHERE origin=? AND username=?", keys
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
        for origin, username, stored in rows:
            if len(stored) <= 12:
                continue
            try:
                password = cipher.decrypt(
                    stored[:12], stored[12:], self._legacy_aad(origin, username)
                )
                self._store.put(self._record_key(origin, username), password)
            except (InvalidTag, VaultError, ValueError, UnicodeError, sqlite3.Error):
                continue
            migrated.append((origin, username))
        self._delete_legacy(migrated)

    def save(self, origin: str, username: str, password: str) -> bool:
        origin = origin.strip()
        username = username.strip()
        if not origin or not username or not password:
            return False
        if not self._ensure_store():
            return False
        try:
            self._store.put(
                self._record_key(origin, username), password.encode("utf-8")
            )
            self._delete_legacy([(origin, username)])
            return True
        except (VaultError, sqlite3.Error) as error:
            self.last_error = str(error)
            print(f"[BFSB] Password save failed: {error}", file=sys.stderr)
            return False

    def get(self, origin: str, username: str) -> Optional[str]:
        if not self._ensure_store():
            return None
        origin = origin.strip()
        username = username.strip()
        if not origin or not username:
            return None
        record_key = self._record_key(origin, username)
        try:
            value = self._store.get(record_key)
        except VaultIntegrityError:
            raise
        if value is not None:
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError as error:
                raise VaultIntegrityError("password record is not valid UTF-8") from error
        try:
            with self._legacy_connection() as conn:
                row = conn.execute(
                    "SELECT value FROM passwords WHERE origin=? AND username=?",
                    (origin, username),
                ).fetchone()
            if row is None:
                return None
            if row[0] is None:
                raise VaultIntegrityError("legacy password record is empty")
            cipher = KEY_MANAGER.get_aesgcm()
            stored = bytes(row[0])
            value = cipher.decrypt(
                stored[:12], stored[12:], self._legacy_aad(origin, username)
            )
            self._store.put(record_key, value)
            self._delete_legacy([(origin, username)])
            return value.decode("utf-8")
        except InvalidTag as error:
            raise VaultIntegrityError("legacy password record authentication failed") from error
        except (ValueError, UnicodeError) as error:
            raise VaultIntegrityError("legacy password record is invalid") from error
        except sqlite3.Error as error:
            raise VaultError("legacy password store is unavailable") from error

    def delete(self, origin: str, username: str) -> bool:
        if not self._ensure_store():
            return False
        self._store.delete(self._record_key(origin.strip(), username.strip()))
        self._delete_legacy([(origin.strip(), username.strip())])
        return True

    def origins(self) -> list[tuple[str, str]]:
        if not self._ensure_store():
            return []
        values: set[tuple[str, str]] = set()
        for record_key in self._store.keys(strict=False):
            origin, separator, username = record_key.partition("\0")
            if separator:
                values.add((origin, username))
        values.update((origin, username) for origin, username, _ in self._legacy_rows())
        return sorted(values)
