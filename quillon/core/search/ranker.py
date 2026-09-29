"""Quillon custom BM25 ranker.

Takes a user query, tokenizes it (same rules as the indexer), and
scores each document using Okapi BM25. Returns ranked hits.

Pure-Python, no external deps. Talks to the SQLite index built by
`quillon.core.search.indexer`.

Storage: /home/binwalk/Downloads/bfsb/data/index.db (read-only from here).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from quillon.core.search.indexer import (
    DEFAULT_DB_PATH,
    tokenize,
)


# BM25 hyperparameters — standard defaults.
_K1 = 1.2
_B = 0.75


@dataclass(frozen=True)
class SearchHit:
    """One ranked hit returned from Ranker.search()."""
    doc_id: str
    url: str
    title: str
    score: float

    def __repr__(self) -> str:
        return f"<SearchHit score={self.score:.3f} {self.title!r}>"


class Ranker:
    """BM25 over the Quillon index."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"index DB not found: {self.db_path}")

    def _connect(self) -> sqlite3.Connection:
        # Read-only: SQLite honours this for SELECTs but not for writes.
        # `mode=ro` opens an immutable handle that won't touch the DB.
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("PRAGMA query_only = ON")
        return conn

    def _corpus_stats(self, conn: sqlite3.Connection) -> tuple[int, float]:
        """Return (N, avgdl)."""
        cur = conn.execute("SELECT COUNT(*), COALESCE(AVG(length), 0) FROM docs")
        row = cur.fetchone()
        return int(row[0]), float(row[1])

    def search(self, query: str, top_k: int = 10) -> list[SearchHit]:
        """Rank documents by BM25. Empty list if no terms or no matches.

        Match semantics:
          - 1 query term  → OR (any term matches, ranked by BM25).
          - 2+ query terms → AND filter applied: a doc must contain at
            least min(N, 2) of the unique query terms to be returned.
            This kills the "search '67 meme', get pages mentioning 67%"
            noise — when one term is generic (a digit, a stopword-adjacent
            word) and the other is rare, AND requires the rare term to
            actually appear in the doc.
        """
        terms = tokenize(query)
        if not terms:
            return []

        unique_terms = list(set(terms))
        # Min number of unique query terms that must appear in a doc.
        #   1-term query: min(1, 2) = 1  → OR semantics
        #   2-term query: min(2, 2) = 2  → AND semantics
        #   3+ term query: min(N, 2) = 2 → at least 2 of N must match
        min_match = min(len(unique_terms), 2)

        with self._connect() as conn:
            n_docs, avgdl = self._corpus_stats(conn)
            if n_docs == 0 or avgdl == 0:
                return []

            # Accumulate BM25 scores per doc + track which terms matched
            scores: dict[str, float] = {}
            matched_terms_per_doc: dict[str, set[str]] = {}
            for term in unique_terms:
                # 1) df for this term
                cur = conn.execute(
                    "SELECT df FROM terms WHERE term = ?", (term,)
                )
                row = cur.fetchone()
                if row is None:
                    continue
                df = int(row[0])
                # IDF (Lucene/BM25+ variant, always >= 0)
                idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))

                # 2) postings for this term
                cur = conn.execute(
                    "SELECT p.doc_id, p.freq, d.length "
                    "FROM postings p JOIN docs d ON p.doc_id = d.doc_id "
                    "WHERE p.term = ?",
                    (term,),
                )
                for doc_id, freq, length in cur.fetchall():
                    tf_norm = (freq * (_K1 + 1)) / (
                        freq + _K1 * (1 - _B + _B * length / avgdl)
                    )
                    scores[doc_id] = scores.get(doc_id, 0.0) + idf * tf_norm
                    matched_terms_per_doc.setdefault(doc_id, set()).add(term)

            # AND filter: drop docs that don't match enough unique terms.
            if min_match > 1:
                scores = {
                    doc_id: score
                    for doc_id, score in scores.items()
                    if len(matched_terms_per_doc.get(doc_id, set())) >= min_match
                }

            # Sort by score desc, take top_k
            ranked_ids = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]

            # Hydrate with url + title
            hits: list[SearchHit] = []
            for doc_id, score in ranked_ids:
                cur = conn.execute(
                    "SELECT url, title FROM docs WHERE doc_id = ?", (doc_id,)
                )
                row = cur.fetchone()
                if not row:
                    continue
                hits.append(SearchHit(
                    doc_id=doc_id,
                    url=row[0],
                    title=row[1] or "(untitled)",
                    score=score,
                ))
            return hits
