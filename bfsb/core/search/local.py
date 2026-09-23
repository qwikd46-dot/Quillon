"""BFSB local search engine — orchestrates ranker + snippet generation.

Replaces the external search providers (Brave, DDG, Startpage, Mojeek).
Returns the same MergedSearchResponse shape so existing templates and
the server.py renderer work unchanged.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from bfsb.core.search.indexer import tokenize
from bfsb.core.search.models import (
    EngineType,
    MergedSearchResponse,
    SearchResult,
)
from bfsb.core.search.ranker import Ranker
from bfsb.core.search.shortcuts import ShortcutHit, ShortcutManager


def make_snippet(
    text: str,
    query_terms: list[str],
    window: int = 200,
    context: int = 60,
) -> str:
    """Pick a snippet window around the FIRST matched query term.

    Falls back to the start of the text if no term matched (shouldn't
    happen when called from LocalSearchEngine, but defensive).
    """
    if not text:
        return ""
    if not query_terms:
        return text[:window]

    lower_text = text.lower()
    first_pos = -1
    for term in query_terms:
        pos = lower_text.find(term.lower())
        if pos != -1 and (first_pos == -1 or pos < first_pos):
            first_pos = pos

    if first_pos == -1:
        return text[:window]

    start = max(0, first_pos - context)
    end = min(len(text), first_pos + window - context)
    snippet = text[start:end]
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def _shortcut_to_result(hit: ShortcutHit) -> SearchResult:
    """Convert a ShortcutHit into a SearchResult so it slots into the
    existing template renderer unchanged."""
    return SearchResult(
        url=hit.url,
        title=hit.key,  # show the keyword as the link text
        snippet=f"Direct link (shortcut for '{hit.query}')",
        engine=EngineType.LOCAL,
        rank=1,  # renumbered by caller
        source_domain=urlparse(hit.url).netloc.replace("www", ""),
    )


class LocalSearchEngine:
    """BFSB custom local search engine.

    Combines the BM25 ranker with cached doc text to produce snippets.
    Returns a MergedSearchResponse so it slots into the existing
    server.py renderer without changes to templates or HTML.

    Shortcuts: when the query matches a keyword in
    ~/.bfsb/shortcuts.json, the target URL is prepended as the top hit
    (rank 1) before any BM25 results. Shortcuts are user-controlled
    and never reach out to any third party.
    """

    DEFAULT_INDEX_DB = Path("/home/binwalk/Downloads/bfsb/data/index.db")
    DEFAULT_CRAWLED_DIR = Path("/home/binwalk/Downloads/bfsb/data/crawled")

    def __init__(
        self,
        index_db: Optional[Path] = None,
        crawled_dir: Optional[Path] = None,
        results_per_page: int = 10,
        shortcuts: Optional[ShortcutManager] = None,
    ):
        self.index_db = Path(index_db or self.DEFAULT_INDEX_DB)
        self.crawled_dir = Path(crawled_dir or self.DEFAULT_CRAWLED_DIR)
        self.results_per_page = results_per_page
        self.ranker = Ranker(self.index_db)
        # Lazy cache for doc text+title to avoid re-reading JSON for each query.
        self._doc_cache: dict[str, tuple[str, str]] = {}
        # Shortcut manager (optional; created on first use if None)
        self._shortcuts = shortcuts

    def search(self, query: str, page: int = 1) -> MergedSearchResponse:
        query = (query or "").strip()
        if not query:
            return MergedSearchResponse(
                query=query,
                results=(),
                page=page,
                engines_used=(),
                total_results=0,
            )

        terms = tokenize(query)
        if not terms:
            return MergedSearchResponse(
                query=query,
                results=(),
                page=page,
                engines_used=(),
                total_results=0,
            )

        start = time.perf_counter()

        # 1) Try shortcuts first (always top of results, only on page 1)
        shortcut_result: Optional[SearchResult] = None
        if page == 1:
            sc = self._shortcuts or ShortcutManager()
            self._shortcuts = sc  # cache for next call
            hit = sc.lookup(query)
            if hit is not None:
                shortcut_result = _shortcut_to_result(hit)

        # 2) BM25 results
        offset = max(0, (page - 1) * self.results_per_page)
        # Reserve one slot if a shortcut is prepended, so total result
        # count stays predictable (top_k + shortcut).
        ranker_top_k = offset + self.results_per_page
        all_hits = self.ranker.search(query, top_k=ranker_top_k)
        page_hits = all_hits[offset:offset + self.results_per_page]

        bm25_results: list[SearchResult] = []
        for rank, h in enumerate(page_hits, start=1 + offset):
            text, title = self._load_doc_meta(h.doc_id)
            snippet = make_snippet(text, terms)
            bm25_results.append(
                SearchResult(
                    url=h.url,
                    title=title or h.title or "(untitled)",
                    snippet=snippet,
                    engine=EngineType.LOCAL,
                    rank=rank,
                    source_domain=urlparse(h.url).netloc.replace("www", ""),
                )
            )

        # 3) Combine — shortcut first when present, then BM25
        if shortcut_result is not None:
            # Renumber so ranks are contiguous from 1.
            shortcut_result = SearchResult(
                url=shortcut_result.url,
                title=shortcut_result.title,
                snippet=shortcut_result.snippet,
                engine=shortcut_result.engine,
                rank=1,
                source_domain=shortcut_result.source_domain,
            )
            for i, r in enumerate(bm25_results, start=2):
                bm25_results[i - 2] = SearchResult(
                    url=r.url, title=r.title, snippet=r.snippet,
                    engine=r.engine, rank=i,
                    source_domain=r.source_domain,
                )
            results = [shortcut_result] + bm25_results
            total_results = 1 + len(all_hits)
        else:
            results = bm25_results
            total_results = len(all_hits)

        elapsed_ms = (time.perf_counter() - start) * 1000
        return MergedSearchResponse(
            query=query,
            results=tuple(results),
            page=page,
            engines_used=(EngineType.LOCAL,),
            total_results=total_results,
            response_time_ms=elapsed_ms,
        )

    def _load_doc_meta(self, doc_id: str) -> tuple[str, str]:
        """Read doc text+title from crawler JSON, cached."""
        if doc_id in self._doc_cache:
            return self._doc_cache[doc_id]
        path = self.crawled_dir / f"{doc_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            text = data.get("text", "")
            title = data.get("title", "")
        except Exception:
            text, title = "", ""
        # Cap cache so we don't grow unbounded
        if len(self._doc_cache) > 500:
            self._doc_cache.clear()
        self._doc_cache[doc_id] = (text, title)
        return text, title
