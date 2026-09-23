"""BFSB custom search indexer.

Reads crawler output (data/crawled/*.json) and builds a SQLite
inverted index in data/index.db. Schema is intentionally minimal —
just enough to feed the BM25 ranker (Component 3) and to enable
future features (phrase queries, snippets, etc.).

Schema:
    docs       — doc_id PK, url, title, fetched_at, length (tokens)
    terms      — term PK, df (document frequency)
    postings   — (term, doc_id) PK, freq

Storage: /home/binwalk/Downloads/bfsb/data/index.db
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------

# English stopwords. Small on purpose — BM25 downweights frequent terms
# automatically, so we only need to filter the truly noisy ones.
STOPWORDS: frozenset[str] = frozenset(
    """
    a an and are as at be by for from has have he her his in is it its
    of on or our ours she that the their them then there these they this
    to was we were will with you your yours i me my
    about above after again against all am any because been before being
    below between both but by can did do does doing don during each few
    further had having here himself herself himself how if into itself
    just more most must no nor not now off once only other own same
    should so some such than that through under until up upon very
    when which while who whom why would s t d re ll ve
    also could would should might must shall
    """.split()
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase → split on non-word → drop stopwords + singletons.

    No stemming (v1). Add a stemmer later if needed; BM25 still ranks
    reasonably without stemming because the ranker handles term
    frequency and document length.
    """
    if not text:
        return []
    return [t for t in _WORD_RE.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    doc_id     TEXT PRIMARY KEY,
    url        TEXT NOT NULL,
    title      TEXT,
    fetched_at TEXT,
    length     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS terms (
    term TEXT PRIMARY KEY,
    df   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS postings (
    term   TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    freq   INTEGER NOT NULL,
    PRIMARY KEY (term, doc_id)
);

CREATE INDEX IF NOT EXISTS idx_postings_term ON postings(term);
CREATE INDEX IF NOT EXISTS idx_postings_doc  ON postings(doc_id);
"""

DEFAULT_DB_PATH = Path("/home/binwalk/Downloads/bfsb/data/index.db")
DEFAULT_DATA_DIR = Path("/home/binwalk/Downloads/bfsb/data/crawled")


# --------------------------------------------------------------------------
# Indexer
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexStats:
    docs_indexed: int
    unique_terms: int
    total_postings: int
    total_tokens: int


class Indexer:
    """Build / update the inverted index from crawler JSON files."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # --- indexing ---------------------------------------------------------

    def index_file(self, json_path: Path) -> bool:
        """Index one crawler JSON file. Returns True on success."""
        try:
            doc = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[indexer] bad JSON {json_path}: {e}")
            return False

        doc_id = json_path.stem
        url = doc.get("url", "")
        title = doc.get("title", "")
        fetched_at = doc.get("fetched_at", "")
        text = doc.get("text", "")
        # Tokenize the title with extra weight by including it twice.
        # (Inverted index still counts unique terms per doc, but two passes
        # gives terms in the title a higher tf — useful for ranking.)
        tokens = tokenize(text + " " + title + " " + title)
        if not tokens:
            return False
        tf = Counter(tokens)

        with self._connect() as conn:
            # Replace any previous version of this doc (re-index support).
            conn.execute("DELETE FROM postings WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM terms  WHERE term IN "
                         "(SELECT term FROM postings WHERE doc_id = ?)", (doc_id,))
            conn.execute("DELETE FROM docs   WHERE doc_id = ?", (doc_id,))

            conn.execute(
                "INSERT INTO docs (doc_id, url, title, fetched_at, length) "
                "VALUES (?, ?, ?, ?, ?)",
                (doc_id, url, title, fetched_at, len(tokens)),
            )

            # Batch insert terms + postings.
            conn.executemany(
                "INSERT OR IGNORE INTO terms (term, df) VALUES (?, 0)",
                ((term,) for term in tf),
            )
            conn.executemany(
                "INSERT INTO postings (term, doc_id, freq) VALUES (?, ?, ?)",
                ((term, doc_id, freq) for term, freq in tf.items()),
            )
            conn.execute(
                "UPDATE terms SET df = (SELECT COUNT(*) FROM postings WHERE postings.term = terms.term)"
            )
            conn.commit()
        return True

    def index_directory(self, data_dir: Path = DEFAULT_DATA_DIR) -> IndexStats:
        """Index every *.json in data_dir. Returns stats."""
        data_dir = Path(data_dir)
        if not data_dir.exists():
            raise FileNotFoundError(f"data dir not found: {data_dir}")
        files = sorted(data_dir.glob("*.json"))
        ok = 0
        for fp in files:
            if self.index_file(fp):
                ok += 1
        return self.stats(ok_expected=ok)

    # --- stats / queries --------------------------------------------------

    def stats(self, ok_expected: int = 0) -> IndexStats:
        with self._connect() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM docs")
            docs_n = cur.fetchone()[0]
            cur = conn.execute("SELECT COUNT(*) FROM terms")
            terms_n = cur.fetchone()[0]
            cur = conn.execute("SELECT COUNT(*) FROM postings")
            post_n = cur.fetchone()[0]
            cur = conn.execute("SELECT COALESCE(SUM(length), 0) FROM docs")
            toks = cur.fetchone()[0]
        return IndexStats(
            docs_indexed=docs_n,
            unique_terms=terms_n,
            total_postings=post_n,
            total_tokens=toks,
        )

    def reset(self) -> None:
        """Drop everything. Useful in tests."""
        with self._connect() as conn:
            conn.executescript(
                "DELETE FROM postings; DELETE FROM terms; DELETE FROM docs;"
            )
            conn.commit()
