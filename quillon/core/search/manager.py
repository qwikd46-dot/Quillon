"""Search Manager — Orchestrates all search providers."""

from __future__ import annotations

import asyncio
import time
from typing import Optional
from collections import OrderedDict

import httpx

from quillon.core.config import APP_CONFIG, SECURITY_CONFIG
from quillon.core.search.models import (
    SearchResult,
    SearchResponse,
    MergedSearchResponse,
    EngineType,
)
from quillon.core.search.providers import create_provider
from quillon.core.search.merger import ResultMerger, MergedResult


class SearchManager:
    """High-level search orchestration with persistent client."""

    def __init__(self, enabled_engines: list[str] | None = None):
        # Filter to only enabled engines
        if enabled_engines is None:
            enabled_engines = list(APP_CONFIG.SEARCH_DEFAULT_ENGINES)
        self.enabled_engines = enabled_engines
        self._client: Optional[httpx.AsyncClient] = None
        self._providers: dict[EngineType, any] = {}
        self._merger = ResultMerger()
        self._initialized = False
        # Simple LRU cache for search results
        self._cache: OrderedDict[str, tuple[MergedSearchResponse, float]] = OrderedDict()
        self._cache_size = APP_CONFIG.SEARCH_CACHE_SIZE
        self._cache_ttl = APP_CONFIG.SEARCH_CACHE_TTL

    async def initialize(self) -> None:
        """Initialize HTTP client and providers once."""
        if self._initialized:
            return

        # Hardened HTTP client with connection pooling
        limits = httpx.Limits(
            max_connections=10,  # Reduced from 20
            max_keepalive_connections=5,  # Reduced from 10
            keepalive_expiry=30.0,  # Reduced from 60s
        )
        timeout = httpx.Timeout(
            connect=2.0,  # Reduced from 3s
            read=6.0,  # Reduced from 8s
            write=2.0,  # Reduced from 3s
            pool=2.0,  # Reduced from 3s
        )
        self._client = httpx.AsyncClient(
            limits=limits,
            timeout=timeout,
            follow_redirects=True,
            verify=SECURITY_CONFIG.VERIFY_SSL,
            max_redirects=SECURITY_CONFIG.MAX_REDIRECTS,
            headers={"User-Agent": SECURITY_CONFIG.USER_AGENT},
        )

        # Create providers
        for engine_name in self.enabled_engines:
            try:
                engine_type = EngineType(engine_name)
                provider = await create_provider(engine_type, self._client)
                self._providers[engine_type] = provider
            except Exception as e:
                print(f"[SearchManager] Failed to create provider {engine_name}: {e}")

        self._initialized = True

    def _get_cache_key(self, query: str, page: int) -> str:
        return f"{query.strip().lower()}:{page}"

    def _get_cached(self, key: str) -> Optional[MergedSearchResponse]:
        if key in self._cache:
            response, timestamp = self._cache[key]
            if time.time() - timestamp < self._cache_ttl:
                # Move to end (most recently used)
                self._cache.move_to_end(key)
                print(f"[SearchManager] Cache HIT for '{key}'")
                return response
            else:
                # Expired
                del self._cache[key]
        return None

    def _set_cached(self, key: str, response: MergedSearchResponse) -> None:
        if len(self._cache) >= self._cache_size:
            self._cache.popitem(last=False)  # Remove oldest
        self._cache[key] = (response, time.time())

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._initialized = False
        self._cache.clear()

    async def search(
        self,
        query: str,
        page: int = 1,
        engines: list[str] | None = None,
    ) -> MergedSearchResponse:
        """Execute search across all enabled engines with retry logic."""
        if not self._initialized:
            await self.initialize()

        if not query or not query.strip():
            return MergedSearchResponse(
                query=query, results=(), page=page,
                engines_used=(), total_results=0,
            )

        query = query.strip()
        cache_key = self._get_cache_key(query, page)

        # Check cache first
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        # Determine which engines to use
        target_engines = engines or [e.value for e in self._providers.keys()]

        # Retry logic: try up to 2 times with a small delay
        max_retries = 2
        for attempt in range(max_retries):
            # Run searches concurrently
            tasks = []
            for engine_name in target_engines:
                try:
                    engine_type = EngineType(engine_name)
                    provider = self._providers.get(engine_type)
                    if provider:
                        tasks.append(provider.search(query, page))
                except ValueError:
                    continue

            if not tasks:
                return MergedSearchResponse(
                    query=query, results=(), page=page,
                    engines_used=(), total_results=0,
                )

            responses = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter successful responses
            valid_responses: list[SearchResponse] = []
            engines_used: list[EngineType] = []

            for i, resp in enumerate(responses):
                if isinstance(resp, SearchResponse) and resp.success:
                    valid_responses.append(resp)
                    engines_used.append(resp.engine)
                elif isinstance(resp, Exception):
                    print(f"[SearchManager] Engine {target_engines[i]} failed: {resp}")

            # If we got results, merge and return
            if valid_responses:
                merged_results = self._merger.merge(valid_responses)

                response = MergedSearchResponse(
                    query=query,
                    results=tuple(
                        m.to_search_result(EngineType(next(iter(m.engines))), m.best_rank) for m in merged_results
                    ),
                    page=page,
                    engines_used=tuple(engines_used),
                    total_results=len(merged_results),
                    response_time_ms=max((r.response_time_ms for r in valid_responses), default=0),
                )

                # Cache the result
                self._set_cached(cache_key, response)
                return response

            # No results - retry if not last attempt
            if attempt < max_retries - 1:
                print(f"[SearchManager] No results for '{query}', retrying ({attempt + 1}/{max_retries})...")
                await asyncio.sleep(0.5)
                continue

        # All retries exhausted - return empty result
        return MergedSearchResponse(
            query=query, results=(), page=page,
            engines_used=(), total_results=0,
        )


# Synchronous wrapper for Qt integration.
#
# As of 2026-07-28, this now uses the Quillon local search engine
# (LocalSearchEngine) instead of external providers. The async
# SearchManager above is kept for backward compatibility but is
# unused by the current server.py render path.

class SyncSearchManager:
    """Synchronous wrapper for Qt thread usage.

    Routes queries through the custom local BM25 engine. No external
    HTTP, no upstream search providers, no ready-to-use services.
    """

    def __init__(self):
        from quillon.core.search.local import LocalSearchEngine
        self._engine = LocalSearchEngine()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Kept for backward compat — some legacy code may still touch this.
        self._manager: Optional[SearchManager] = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create event loop (kept for backward compat)."""
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
            return self._loop

    def search(self, query: str, page: int = 1) -> MergedSearchResponse:
        """Synchronous search using the local engine."""
        return self._engine.search(query, page)

    def close(self) -> None:
        """No-op for the local engine — kept for API compat."""
        self._engine = None  # type: ignore[assignment]
        self._manager = None