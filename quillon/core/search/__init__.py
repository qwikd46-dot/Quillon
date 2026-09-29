"""Search package — Local metasearch engine.

Exports resolve on first use (PEP 562). Importing this package eagerly
built the crawler, indexer, ranker and local engine, which costs far more
than a caller that only wants one leaf -- the proxy addon needs just
``safety``.

``MergedSearchResponse`` is bound to ``manager``, exactly as before, where
it shadowed the identically named import from ``models``.
"""

from importlib import import_module as _import_module

_SUBMODULES = {
    "SearchResult": "models",
    "SearchResponse": "models",
    "EngineType": "models",
    "safe_extract_text": "models",
    "safe_extract_url": "models",
    "SearchProvider": "providers",
    "PROVIDER_MAP": "providers",
    "create_provider": "providers",
    "ResultMerger": "merger",
    "MergedResult": "merger",
    "SearchManager": "manager",
    "SyncSearchManager": "manager",
    "MergedSearchResponse": "manager",
    "Crawler": "crawler",
    "CrawlResult": "crawler",
    "Indexer": "indexer",
    "IndexStats": "indexer",
    "tokenize": "indexer",
    "STOPWORDS": "indexer",
    "Ranker": "ranker",
    "SearchHit": "ranker",
    "rank_results": "policy",
    "is_official_host": "policy",
    "normalized_host": "policy",
    "safety": "safety",
    "LocalSearchEngine": "local",
    "make_snippet": "local",
    "ShortcutManager": "shortcuts",
    "ShortcutHit": "shortcuts",
    "DEFAULT_SHORTCUTS": "shortcuts",
}

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
    # NEW: indexer
    "Indexer",
    "IndexStats",
    "tokenize",
    "STOPWORDS",
    # NEW: local ranker
    "Ranker",
    "SearchHit",
    # NEW: official-site result policy
    "rank_results",
    "is_official_host",
    "normalized_host",
    # NEW: shared adult-content blocklist
    "safety",
    # NEW: local search engine (replaces external providers)
    "LocalSearchEngine",
    "make_snippet",
    # NEW: shortcuts (browser-style keyword -> URL)
    "ShortcutManager",
    "ShortcutHit",
    "DEFAULT_SHORTCUTS",
]


def __getattr__(name):
    module_name = _SUBMODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = _import_module(f".{module_name}", __name__)
    # "safety" names the module itself; everything else is a name inside it.
    value = module if name == module_name else getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
