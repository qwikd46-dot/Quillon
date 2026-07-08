"""Search Manager — Orchestrates all search providers."""

from __future__ import annotations

import asyncio
import time
from typing import Optional
from collections import OrderedDict

import httpx

from bfsb.core.config import APP_CONFIG, SECURITY_CONFIG
from bfsb.core.search.models import (
    SearchResponse,
    MergedSearchResponse,
    EngineType,
)
from bfsb.core.search.providers import create_provider
from bfsb.core.search.merger import ResultMerger, MergedResult


class SearchManager:
    """High-level search orchestration with persistent client."""

    def __init__(self, enabled_engines: list[str] | None = None):
        from bfsb.core.config import ENGINE_CONFIGS_RAW
        self.enabled_engines = enabled_engines or list(ENGINE_CONFIGS_RAW.keys())
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
        """Execute search across all enabled engines."""
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

        # Merge results
        merged_results = self._merger.merge(valid_responses)

        # Convert to final response
        response = MergedSearchResponse(
            query=query,
            results=tuple(
                MergedResult(
                    url=m.url,
                    title=m.title,
                    snippet=m.snippet,
                    source_domain=m.source_domain,
                    engines=m.engines,
                    best_rank=m.best_rank,
                    weighted_score=m.weighted_score,
                    favicon_url=m.favicon_url,
                    thumbnail_url=m.thumbnail_url,
                    published_date=m.published_date,
                ) for m in merged_results
            ),
            page=page,
            engines_used=tuple(engines_used),
            total_results=len(merged_results),
            response_time_ms=max((r.response_time_ms for r in valid_responses), default=0),
        )

        # Cache the result
        self._set_cached(cache_key, response)
        return response


# Synchronous wrapper for Qt integration
class SyncSearchManager:
    """Synchronous wrapper for Qt thread usage — reuses SearchManager."""

    def __init__(self):
        self._manager: Optional[SearchManager] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create event loop."""
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
            return self._loop

    def _get_manager(self) -> SearchManager:
        """Get or create SearchManager."""
        if self._manager is None:
            self._manager = SearchManager()
        return self._manager

    def search(self, query: str, page: int = 1) -> MergedSearchResponse:
        """Synchronous search (for Qt thread) — reuses manager."""
        manager = self._get_manager()

        async def _search():
            await manager.initialize()
            return await manager.search(query, page)

        loop = self._get_loop()
        if loop.is_running():
            # If loop is running (Qt event loop), run in thread pool
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, _search())
                return future.result(timeout=30)
        else:
            return loop.run_until_complete(_search())

    def close(self) -> None:
        """Close the search manager."""
        if self._manager:
            loop = self._get_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(asyncio.run, self._manager.close()).result(timeout=10)
            else:
                loop.run_until_complete(self._manager.close())
            self._manager = None