"""Bookmark storage — CRUD over folders and bookmarks."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .db import BrowserDB


@dataclass
class Bookmark:
    id: int
    url: str
    title: str
    folder: str
    folder_id: int
    pinned: bool
    visit_count: int
    created_at: int
    updated_at: int


class BookmarkStore:
    """Wraps BrowserDB for bookmark-specific operations."""

    def __init__(self, db: Optional[BrowserDB] = None) -> None:
        self._db = db or BrowserDB.instance()

    # ---------------------------------------------------------------- #
    # Bookmarks
    # ---------------------------------------------------------------- #
    def add(self, url: str, title: str = "", folder_id: int = 1,
            pinned: bool = False) -> int:
        """Add or update a bookmark. Returns the bookmark id."""
        now = int(time.time())
        try:
            self._db.execute_write(
                """
                INSERT INTO bookmarks
                    (url, title, folder_id, pinned, visit_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title = excluded.title,
                    folder_id = excluded.folder_id,
                    updated_at = excluded.updated_at
                """,
                (url, title, folder_id, int(pinned), now, now),
            )
        except Exception:
            # Fall back to a simpler insert (URL might have non-ASCII that
            # breaks ON CONFLICT in some builds — extremely rare but cheap).
            self._db.execute_write(
                """
                INSERT OR IGNORE INTO bookmarks
                    (url, title, folder_id, pinned, visit_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (url, title, folder_id, int(pinned), now, now),
            )
        return self.get_id(url) or 0

    def remove(self, url: str) -> bool:
        self._db.execute_write("DELETE FROM bookmarks WHERE url = ?", (url,))
        return True

    def remove_by_id(self, bookmark_id: int) -> bool:
        self._db.execute_write("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))
        return True

    def clear(self) -> int:
        conn = self._db._read_conn()
        try:
            count = conn.execute("SELECT COUNT(*) AS n FROM bookmarks").fetchone()["n"]
        finally:
            conn.close()
        self._db.execute_write("DELETE FROM bookmarks")
        return count

    def set_pinned(self, url: str, pinned: bool) -> None:
        self._db.execute_write(
            "UPDATE bookmarks SET pinned = ?, updated_at = ? WHERE url = ?",
            (int(pinned), int(time.time()), url),
        )

    def set_folder(self, url: str, folder_id: int) -> None:
        self._db.execute_write(
            "UPDATE bookmarks SET folder_id = ?, updated_at = ? WHERE url = ?",
            (folder_id, int(time.time()), url),
        )

    def set_title(self, url: str, title: str) -> None:
        """Update the display title if bookmark exists."""
        self._db.execute_write(
            "UPDATE bookmarks SET title = ?, updated_at = ? WHERE url = ?",
            (title, int(time.time()), url),
        )

    def increment_visit_count(self, url: str) -> None:
        self._db.execute_write(
            "UPDATE bookmarks SET visit_count = visit_count + 1 WHERE url = ?",
            (url,),
        )

    # ---------------------------------------------------------------- #
    # Queries
    # ---------------------------------------------------------------- #
    def get_id(self, url: str) -> Optional[int]:
        conn = self._db._read_conn()
        try:
            row = conn.execute(
                "SELECT id FROM bookmarks WHERE url = ?", (url,)
            ).fetchone()
            return row["id"] if row else None
        finally:
            conn.close()

    def get(self, url: str) -> Optional[Bookmark]:
        conn = self._db._read_conn()
        try:
            row = conn.execute(
                """
                SELECT b.id, b.url, b.title, b.folder_id,
                       f.name AS folder,
                       b.pinned, b.visit_count, b.created_at, b.updated_at
                FROM bookmarks b
                LEFT JOIN folders f ON b.folder_id = f.id
                WHERE b.url = ?
                """,
                (url,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_bookmark(row)
        finally:
            conn.close()

    def is_bookmarked(self, url: str) -> bool:
        return self.get_id(url) is not None

    def list_all(self, limit: int = 1000) -> list[Bookmark]:
        conn = self._db._read_conn()
        try:
            rows = conn.execute(
                """
                SELECT b.id, b.url, b.title, b.folder_id,
                       f.name AS folder,
                       b.pinned, b.visit_count, b.created_at, b.updated_at
                FROM bookmarks b
                LEFT JOIN folders f ON b.folder_id = f.id
                ORDER BY b.pinned DESC, b.visit_count DESC, b.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._row_to_bookmark(r) for r in rows]
        finally:
            conn.close()

    def list_pinned(self) -> list[Bookmark]:
        conn = self._db._read_conn()
        try:
            rows = conn.execute(
                """
                SELECT b.id, b.url, b.title, b.folder_id,
                       f.name AS folder,
                       b.pinned, b.visit_count, b.created_at, b.updated_at
                FROM bookmarks b
                LEFT JOIN folders f ON b.folder_id = f.id
                WHERE b.pinned = 1
                ORDER BY b.visit_count DESC, b.updated_at DESC
                """,
            ).fetchall()
            return [self._row_to_bookmark(r) for r in rows]
        finally:
            conn.close()

    def search(self, query: str, limit: int = 50) -> list[Bookmark]:
        q = f"%{query.lower()}%"
        conn = self._db._read_conn()
        try:
            rows = conn.execute(
                """
                SELECT b.id, b.url, b.title, b.folder_id,
                       f.name AS folder,
                       b.pinned, b.visit_count, b.created_at, b.updated_at
                FROM bookmarks b
                LEFT JOIN folders f ON b.folder_id = f.id
                WHERE LOWER(b.title) LIKE ? OR LOWER(b.url) LIKE ?
                ORDER BY b.visit_count DESC, b.updated_at DESC
                LIMIT ?
                """,
                (q, q, limit),
            ).fetchall()
            return [self._row_to_bookmark(r) for r in rows]
        finally:
            conn.close()

    # ---------------------------------------------------------------- #
    # Folders
    # ---------------------------------------------------------------- #
    def list_folders(self) -> list[tuple[int, str]]:
        conn = self._db._read_conn()
        try:
            rows = conn.execute(
                "SELECT id, name FROM folders ORDER BY id"
            ).fetchall()
            return [(r["id"], r["name"]) for r in rows]
        finally:
            conn.close()

    def create_folder(self, name: str) -> int:
        now = int(time.time())
        self._db.execute_write(
            "INSERT OR IGNORE INTO folders (name, created_at) VALUES (?, ?)",
            (name, now),
        )
        conn = self._db._read_conn()
        try:
            row = conn.execute(
                "SELECT id FROM folders WHERE name = ?", (name,)
            ).fetchone()
            return row["id"] if row else 0
        finally:
            conn.close()

    # ---------------------------------------------------------------- #
    # Internal
    # ---------------------------------------------------------------- #
    @staticmethod
    def _row_to_bookmark(row) -> Bookmark:
        return Bookmark(
            id=row["id"],
            url=row["url"],
            title=row["title"] or row["url"],
            folder=row["folder"] or "Bookmarks Bar",
            folder_id=row["folder_id"] or 1,
            pinned=bool(row["pinned"]),
            visit_count=row["visit_count"] or 0,
            created_at=row["created_at"] or 0,
            updated_at=row["updated_at"] or 0,
        )
