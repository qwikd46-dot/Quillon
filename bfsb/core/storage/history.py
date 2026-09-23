"""History storage — capture on navigation, dedupe by URL, FIFO cap."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .db import BrowserDB
from ..config import PATHS


@dataclass
class HistoryEntry:
    id: int
    url: str
    title: str
    visit_count: int
    last_visit: int


class HistoryStore:
    """Captures page visits. Dedupes by URL so the same page doesn't spam."""

    # URLs that aren't worth remembering
    _SKIP_SCHEMES = ("bfsb://", "data:", "about:", "chrome:", "chrome-error://")

    def __init__(self, db: Optional[BrowserDB] = None, cap: Optional[int] = None) -> None:
        self._db = db or BrowserDB.instance()
        self._cap = cap or PATHS.HISTORY_CAP

    def record(self, url: str, title: str = "") -> Optional[int]:
        """Record a page visit. Returns the entry id, or None if skipped."""
        if not url:
            return None
        # Internal / data / about URLs are skipped
        if url.startswith(self._SKIP_SCHEMES):
            return None

        # Decide whether to skip based on host (loopback, local, search-only,
        # empty paths). All bookkeeping for the browser's own UI lives here.
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host in {"127.0.0.1", "::1", "localhost", "0.0.0.0"}:
            # Loopback is BFSB's local backend. Its routes (home page,
            # static assets, error pages) are UI navigation events, not
            # web visits — dropped, EXCEPT explicit /search?q= URLs, which
            # the server logs as first-class search history entries.
            if not (parsed.path == "/search" and parsed.query):
                return None
        if not title:
            title = url  # fall back to URL until page loads

        now = int(time.time())
        try:
            self._db.execute_write(
                """
                INSERT INTO history (url, title, last_visit)
                VALUES (?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    visit_count = visit_count + 1,
                    title = excluded.title,
                    last_visit = excluded.last_visit
                """,
                (url, title, now),
            )
        except Exception:
            # URLs with non-ASCII hostnames occasionally fail the upsert
            # fallback; in that case silently drop the visit.
            return None

        # FIFO eviction in a single transaction — doesn't happen on every
        # record() call, only when we cross the cap (cheap amortised check).
        # Doing it inline keeps the cap tight; rowcount moves are O(1)-ish.
        self._db.enforce_history_cap(self._cap)

        conn = self._db._read_conn()
        try:
            row = conn.execute("SELECT id FROM history WHERE url = ?", (url,)).fetchone()
            return row["id"] if row else None
        finally:
            conn.close()

    def list_recent(self, limit: int = 50) -> list[HistoryEntry]:
        conn = self._db._read_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, url, title, visit_count, last_visit
                FROM history
                ORDER BY last_visit DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._row_to_entry(r) for r in rows]
        finally:
            conn.close()

    def search(self, query: str, limit: int = 50) -> list[HistoryEntry]:
        q = f"%{query.lower()}%"
        conn = self._db._read_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, url, title, visit_count, last_visit
                FROM history
                WHERE LOWER(title) LIKE ? OR LOWER(url) LIKE ?
                ORDER BY last_visit DESC
                LIMIT ?
                """,
                (q, q, limit),
            ).fetchall()
            return [self._row_to_entry(r) for r in rows]
        finally:
            conn.close()

    def remove(self, entry_id: int) -> None:
        self._db.execute_write("DELETE FROM history WHERE id = ?", (entry_id,))

    def clear(self) -> None:
        self._db.execute_write("DELETE FROM history")

    def count(self) -> int:
        conn = self._db._read_conn()
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM history").fetchone()
            return row["c"] if row else 0
        finally:
            conn.close()

    @staticmethod
    def _row_to_entry(row) -> HistoryEntry:
        return HistoryEntry(
            id=row["id"],
            url=row["url"],
            title=row["title"] or row["url"],
            visit_count=row["visit_count"] or 1,
            last_visit=row["last_visit"] or 0,
        )
