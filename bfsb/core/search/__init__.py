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

__all__ = [
    # Models
    "SearchResult",
    "SearchResponse",
    "MergedSearchResponse",
    "MergedResult",
    "EngineType",
    "safe_extract_text",
    "safe_extract_url",
    # Providers
    "SearchProvider",
    "PROVIDER_MAP",
    "create_provider",
    # Merger
    "ResultMerger",
    "MergedResult",
    # Manager
    "SearchManager",
    "SyncSearchManager",
]