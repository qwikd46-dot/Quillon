"""CLI driver for the BFSB custom BM25 ranker.

Usage:
    python -m bfsb.core.search.rank_runner "query terms here"
    python -m bfsb.core.search.rank_runner "query" --top-k 5
    python -m bfsb.core.search.rank_runner "query" --db-path PATH
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bfsb.core.search.ranker import Ranker, DEFAULT_DB_PATH


def main() -> int:
    p = argparse.ArgumentParser(description="BFSB ranker — query the local BM25 index")
    p.add_argument("query", help="Search query")
    p.add_argument("--top-k", type=int, default=10, help="Max results to show (default: 10)")
    p.add_argument("--db-path", default=str(DEFAULT_DB_PATH),
                   help="SQLite index path (default: %(default)s)")
    args = p.parse_args()

    ranker = Ranker(db_path=Path(args.db_path))
    hits = ranker.search(args.query, top_k=args.top_k)

    if not hits:
        print(f"(no matches for: {args.query!r})")
        return 0

    print(f"\nQuery: {args.query!r}  ({len(hits)} hit{'s' if len(hits) != 1 else ''})\n")
    for i, h in enumerate(hits, 1):
        print(f"  {i}. score={h.score:7.3f}  {h.title}")
        print(f"      {h.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
