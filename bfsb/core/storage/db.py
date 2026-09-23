"""SQLite storage for bookmarks, history, and related metadata.

Single-writer model: all writes go through this module's serialised queue to
avoid SQLite lock contention in a multithreaded Qt app. WAL mode keeps reads
fast while a write is in progress.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from ..config import PATHS


# ══════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════

class BrowserDB:
    """Thread-safe SQLite wrapper for bookmarks + history."""

    _instance: Optional["BrowserDB"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._db_path = PATHS.HISTORY_DB
        self._write_lock = threading.Lock()
        self._init_schema()

    # Singleton via thread-safe DCL
    @classmethod
    def instance(cls) -> "BrowserDB":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ---------------------------------------------------------------- #
    # Connection management
    # ---------------------------------------------------------------- #
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._db_path),
            timeout=5.0,
            isolation_level=None,  # autocommit; we manage transactions explicitly
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _read_conn(self) -> sqlite3.Connection:
        return self._connect()

    def _write_conn(self) -> sqlite3.Connection:
        with self._write_lock:
            return self._connect()

    # ---------------------------------------------------------------- #
    # Schema
    # ---------------------------------------------------------------- #
    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        import time as _t
        ts = int(_t.time())
        conn = self._write_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS folders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bookmarks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    visit_count INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(url)
                );

                CREATE INDEX IF NOT EXISTS bookmarks_folder_idx
                    ON bookmarks(folder_id);
                CREATE INDEX IF NOT EXISTS bookmarks_pinned_idx
                    ON bookmarks(pinned) WHERE pinned = 1;

                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    visit_count INTEGER NOT NULL DEFAULT 1,
                    last_visit INTEGER NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS history_url_idx
                    ON history(url);
                CREATE INDEX IF NOT EXISTS history_last_visit_idx
                    ON history(last_visit DESC);
            """)
            # Default folder so user-created bookmarks have a home. Use a
            # parameterised execute (executescript doesn't bind params).
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR IGNORE INTO folders (id, name, created_at) VALUES (1, 'Bookmarks Bar', ?)",
                (ts,),
            )
            conn.execute("COMMIT")
        finally:
            conn.close()

    # ---------------------------------------------------------------- #
    # Write helpers (every write goes through here, serialised)
    # ---------------------------------------------------------------- #
    def execute_write(self, sql: str, params: tuple = ()) -> None:
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(sql, params)
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                conn.close()

    def execute_write_many(self, sql: str, params_list) -> None:
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.executemany(sql, params_list)
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                conn.close()

    # ---------------------------------------------------------------- #
    # History cap (FIFO eviction)
    # ---------------------------------------------------------------- #
    def enforce_history_cap(self, cap: int = PATHS.HISTORY_CAP) -> int:
        """Evict oldest entries past the cap. Returns rows removed."""
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute(
                    """
                    DELETE FROM history WHERE id IN (
                        SELECT id FROM history
                        ORDER BY last_visit DESC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (cap,),
                )
                removed = cur.rowcount
                conn.execute("COMMIT")
                return removed
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                conn.close()
