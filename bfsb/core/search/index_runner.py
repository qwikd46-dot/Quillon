"""CLI driver for the BFSB custom indexer.

Usage:
    python -m bfsb.core.search.index_runner                   # index default dir
    python -m bfsb.core.search.index_runner --reset           # wipe DB then index
    python -m bfsb.core.search.index_runner --data-dir PATH
    python -m bfsb.core.search.index_runner --db-path PATH
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bfsb.core.search.indexer import Indexer, DEFAULT_DATA_DIR, DEFAULT_DB_PATH


def main() -> int:
    p = argparse.ArgumentParser(description="BFSB indexer — build SQLite inverted index from crawler output")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR),
                   help="Directory of *.json files from the crawler")
    p.add_argument("--db-path", default=str(DEFAULT_DB_PATH),
                   help="Output SQLite path")
    p.add_argument("--reset", action="store_true",
                   help="Wipe the DB before indexing")
    args = p.parse_args()

    idx = Indexer(db_path=Path(args.db_path))
    if args.reset:
        print("[indexer] resetting DB...")
        idx.reset()

    print(f"[indexer] indexing files in {args.data_dir} → {args.db_path}")
    stats = idx.index_directory(Path(args.data_dir))
    print()
    print("Done.")
    print(f"  docs indexed:        {stats.docs_indexed}")
    print(f"  unique terms:        {stats.unique_terms}")
    print(f"  total postings:      {stats.total_postings}")
    print(f"  total tokens:        {stats.total_tokens}")
    if stats.docs_indexed:
        print(f"  avg tokens/doc:      {stats.total_tokens // stats.docs_indexed}")
        print(f"  avg postings/term:   {stats.total_postings // max(stats.unique_terms, 1)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
