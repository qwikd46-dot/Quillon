"""Cookie vault with encrypted storage."""

import sqlite3
import os

from .config import PATHS
from .crypto import KEY_MANAGER


class CookieVault:
    """Encrypted cookie storage using AES-GCM."""

    def __init__(self) -> None:
        self._aes = KEY_MANAGER.get_aesgcm()
        self._init_db()

    def _init_db(self) -> None:
        """Initialize cookie database."""
        conn = sqlite3.connect(str(PATHS.COOKIE_DB))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cookies "
            "(domain TEXT, name TEXT, value BLOB, PRIMARY KEY(domain, name))"
        )
        conn.commit()
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        return sqlite3.connect(str(PATHS.COOKIE_DB))

    def set_cookie(self, domain: str, name: str, value: bytes) -> None:
        """Store encrypted cookie."""
        nonce = os.urandom(12)
        encrypted = self._aes.encrypt(nonce, value, domain.encode())
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO cookies (domain, name, value) VALUES (?, ?, ?)",
            (domain, name, nonce + encrypted),
        )
        conn.commit()
        conn.close()

    def get_cookie(self, domain: str, name: str) -> bytes | None:
        """Retrieve and decrypt cookie."""
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT value FROM cookies WHERE domain=? AND name=?", (domain, name)
        )
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return None
        try:
            stored = bytes(row[0])
            nonce, encrypted = stored[:12], stored[12:]
            return self._aes.decrypt(nonce, encrypted, domain.encode())
        except Exception:
            return None

    def delete_cookie(self, domain: str, name: str) -> None:
        """Delete a cookie."""
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM cookies WHERE domain=? AND name=?", (domain, name)
        )
        conn.commit()
        conn.close()

    def clear_domain(self, domain: str) -> None:
        """Clear all cookies for a domain."""
        conn = self._get_conn()
        conn.execute("DELETE FROM cookies WHERE domain=?", (domain,))
        conn.commit()
        conn.close()