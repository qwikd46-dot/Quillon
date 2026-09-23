"""Search package — Local metasearch engine."""

from .models import (
    SearchResult,
    SearchResponse,
    MergedSearchResponse,
    EngineType,
    safe_extract_text,
    safe_extract_url,
)
from .providers import SearchProvider, PROVIDER_MAP, create_provider
from .merger import ResultMerger, MergedResult
from .manager import SearchManager, SyncSearchManager, MergedSearchResponse
from .crawler import Crawler, CrawlResult  # NEW: custom local crawler
from .indexer import Indexer, IndexStats, tokenize, STOPWORDS  # NEW: custom local indexer
from .ranker import Ranker, SearchHit  # NEW: custom BM25 ranker
from .local import LocalSearchEngine, make_snippet  # NEW: orchestrator that replaces external providers
from .shortcuts import ShortcutManager, ShortcutHit, DEFAULT_SHORTCUTS  # NEW: local keyword->URL shortcuts

__all__ = [
    # Models
    "SearchResult",
    "SearchResponse",
    "MergedSearchResponse",
    "MergedResult",
    "EngineType",
    "safe_extract_text",
    "safe_extract_url",
    # Providers (kept for backward compat — will be replaced by local engine)
    "SearchProvider",
    "PROVIDER_MAP",
    "create_provider",
    # Merger
    "ResultMerger",
    "MergedResult",
    # Manager
    "SearchManager",
    "SyncSearchManager",
    # NEW: local crawler
    "Crawler",
    "CrawlResult",
    # NEW: local indexer
    "Indexer",
    "IndexStats",
    "tokenize",
    "STOPWORDS",
    # NEW: local ranker
    "Ranker",
    "SearchHit",
    # NEW: local search engine (replaces external providers)
    "LocalSearchEngine",
    "make_snippet",
    # NEW: shortcuts (browser-style keyword -> URL)
    "ShortcutManager",
    "ShortcutHit",
    "DEFAULT_SHORTCUTS",
]