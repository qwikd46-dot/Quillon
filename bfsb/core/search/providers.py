"""Search Engine Providers — Each engine as a separate class.

All providers implement a common interface. Results are normalized to SearchResult.
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode, quote_plus, urlparse
from xml.etree import ElementTree as ET

import httpx
from lxml import html as lxml_html

from bfsb.core.config import ENGINE_CONFIGS_RAW, SECURITY_CONFIG, EngineConfig, get_engine_configs
from bfsb.core.search.models import (
    SearchResult,
    SearchResponse,
    EngineType,
    safe_extract_text,
    safe_extract_url,
)


def _extract_text(elem) -> str:
    """Extract clean text from an lxml element, filtering out CSS."""
    try:
        text = elem.text_content().strip()
        import re
        # Remove CSS class rules iteratively (handles nested @media)
        while True:
            old = text
            text = re.sub(r'\.[a-z0-9_-]+\s*\{[^}]*?\}', '', text)
            if text == old:
                break
        # Remove @media queries iteratively
        while True:
            old = text
            text = re.sub(r'@media\s*\([^)]+\)\s*\{[^}]*?\}', '', text)
            if text == old:
                break
        # Remove any remaining { ... } blocks
        text = re.sub(r'\{[^}]*?\}', '', text)
        # Remove any remaining CSS property lines
        text = re.sub(r'[a-z-]+:\s*[^;}]+;', '', text)
        # Clean up whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception:
        return ""


# ──────────────────────────────────────────────────────────────
# BASE PROVIDER
# ──────────────────────────────────────────────────────────────

class SearchProvider(ABC):
    """Abstract base for all search engines."""
    
    def __init__(self, config: EngineConfig, client: httpx.AsyncClient):
        self.config = config
        self.client = client
        # Convert string to EngineType enum
        from bfsb.core.search.models import EngineType
        self.engine_type = EngineType(config.engine_type)
    
    @property
    @abstractmethod
    def base_url(self) -> str:
        """Base search URL."""
        pass
    
    @property
    @abstractmethod
    def params(self) -> dict:
        """Default query parameters."""
        pass
    
    @abstractmethod
    async def parse_results(self, response: httpx.Response, query: str, page: int) -> SearchResponse:
        """Parse engine-specific response into normalized results."""
        pass
    
    async def search(self, query: str, page: int = 1) -> SearchResponse:
        """Execute search with timing and error handling."""
        start = time.perf_counter()
        
        try:
            # Allow provider to customize the request
            response = await self._execute_request(query, page)
            response.raise_for_status()
            
            response = await self.parse_results(response, query, page)
            elapsed = (time.perf_counter() - start) * 1000
            return SearchResponse(
                query=response.query,
                engine=response.engine,
                results=response.results,
                page=response.page,
                total_estimated=response.total_estimated,
                success=response.success,
                error=response.error,
                response_time_ms=elapsed,
            )
            
        except httpx.TimeoutException:
            return SearchResponse(
                query=query, engine=self.engine_type, results=(),
                page=page, success=False, error="Timeout",
                response_time_ms=(time.perf_counter() - start) * 1000,
            )
        except httpx.HTTPStatusError as e:
            return SearchResponse(
                query=query, engine=self.engine_type, results=(),
                page=page, success=False, error=f"HTTP {e.response.status_code}",
                response_time_ms=(time.perf_counter() - start) * 1000,
            )
        except Exception as e:
            return SearchResponse(
                query=query, engine=self.engine_type, results=(),
                page=page, success=False, error=str(e),
                response_time_ms=(time.perf_counter() - start) * 1000,
            )
    
    async def _execute_request(self, query: str, page: int) -> httpx.Response:
        """Execute the HTTP request - override in subclasses for POST, etc."""
        params = self._build_params(query, page)
        url = f"{self.base_url}?{urlencode(params)}"
        
        headers = {
            "User-Agent": SECURITY_CONFIG.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",  # No brotli - httpx can't decode it reliably
            "DNT": "1",
        }
        
        return await self.client.get(
            url,
            headers=headers,
            timeout=self.config.timeout,
            follow_redirects=True,
        )
    
    def _build_params(self, query: str, page: int) -> dict:
        """Build query parameters with pagination."""
        params = {**self.params, "q": query}
        if page > 1:
            params["page"] = page
        return params


# ──────────────────────────────────────────────────────────────
# DUCKDUCKGO - Uses POST to lite.duckduckgo.com/lite/
# ──────────────────────────────────────────────────────────────

class DuckDuckGoProvider(SearchProvider):
    """DuckDuckGo HTML scraper (lite version) - uses POST."""
    
    @property
    def base_url(self) -> str:
        return "https://lite.duckduckgo.com/lite/"
    
    @property
    def params(self) -> dict:
        return {"kl": "us-en"}
    
    async def _execute_request(self, query: str, page: int) -> httpx.Response:
        """DDG lite requires POST with form data."""
        data = {**self.params, "q": query}
        if page > 1:
            data["s"] = str((page - 1) * 10)  # Start offset
        
        headers = {
            "User-Agent": SECURITY_CONFIG.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/x-www-form-urlencoded",
            "DNT": "1",
        }
        
        return await self.client.post(
            self.base_url,
            data=data,
            headers=headers,
            timeout=self.config.timeout,
            follow_redirects=True,
        )
    
    async def parse_results(self, response: httpx.Response, query: str, page: int) -> SearchResponse:
        from lxml import html as lxml_html
        
        doc = lxml_html.fromstring(response.text)
        results = []
        
        # Lite version uses table rows - find all tables, the results table is usually the last one
        tables = doc.cssselect("table")
        if not tables:
            # Check if we got the search form instead of results (202 response)
            if "DuckDuckGo" in response.text and "<form" in response.text:
                return SearchResponse(
                    query=query, engine=self.engine_type, results=(),
                    page=page, success=False, error="Got search form instead of results",
                )
            return SearchResponse(query=query, engine=self.engine_type, results=(), page=page)
        
        # Results are in the last table typically
        results_table = tables[-1]
        
        for i, row in enumerate(results_table.cssselect("tr"), 1):
            if i == 1:  # Skip header row
                continue
            try:
                cells = row.cssselect("td")
                if len(cells) < 2:
                    continue
                
                # First cell: title/link, second cell: snippet
                link_elem = cells[0].cssselect("a")
                if not link_elem:
                    continue
                
                title = _extract_text(link_elem[0])
                url = safe_extract_url(link_elem[0].get("href", ""))
                
                snippet = _extract_text(cells[1])
                
                if not url or not title:
                    continue
                
                results.append(SearchResult(
                    url=url, title=title, snippet=snippet,
                    engine=EngineType.DUCKDUCKGO, rank=(page - 1) * 10 + len(results) + 1,
                    source_domain=urlparse(url).netloc.replace("www.", ""),
                ))
            except Exception:
                continue
        
        return SearchResponse(
            query=query, engine=self.engine_type, results=tuple(results),
            page=page,
        )
                

class BraveSearchProvider(SearchProvider):
    """Brave Search HTML scraper."""

    @property
    def base_url(self) -> str:
        return "https://search.brave.com/search"

    @property
    def params(self) -> dict:
        return {"source": "web"}

    async def _execute_request(self, query: str, page: int) -> httpx.Response:
        """Brave returns brotli which httpx can't always decode - request gzip only."""
        params = {**self.params, "q": query}
        if page > 1:
            params["page"] = page

        headers = {
            "User-Agent": SECURITY_CONFIG.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",  # No brotli - httpx struggles with it
            "DNT": "1",
        }

        return await self.client.get(
            self.base_url,
            params=params,
            headers=headers,
            timeout=self.config.timeout,
            follow_redirects=True,
        )

    async def parse_results(self, response: httpx.Response, query: str, page: int) -> SearchResponse:
        from lxml import html as lxml_html

        doc = lxml_html.fromstring(response.text)
        results = []

        # Brave uses .snippet for each result
        for i, result in enumerate(doc.cssselect(".snippet"), 1):
            try:
                title_elem = result.cssselect(".snippet-title")
                url_elem = result.cssselect(".snippet-url")
                desc_elem = result.cssselect(".snippet-description")

                title = str(safe_extract_text(lxml_html.tostring(title_elem[0], encoding="unicode"))) if title_elem else ""
                url = safe_extract_url(url_elem[0].get("href", "")) if url_elem else None
                snippet = str(safe_extract_text(lxml_html.tostring(desc_elem[0], encoding="unicode"))) if desc_elem else ""

                if not url or not title:
                    continue

                results.append(SearchResult(
                    url=url, title=title, snippet=snippet,
                    engine=EngineType.BRAVE, rank=(page - 1) * 10 + i,
                    source_domain=urlparse(url).netloc.replace("www.", ""),
                ))
            except Exception:
                continue

        return SearchResponse(
            query=query, engine=self.engine_type, results=tuple(results),
            page=page,
        )


class StartpageProvider(SearchProvider):
    """Startpage HTML scraper (Google results via proxy)."""

    @property
    def base_url(self) -> str:
        return "https://www.startpage.com/sp/search"

    @property
    def params(self) -> dict:
        return {"lang": "en", "cat": "web"}

    async def _execute_request(self, query: str, page: int) -> httpx.Response:
        """Startpage needs POST with form data."""
        data = {**self.params, "q": query}
        if page > 1:
            data["page"] = str(page)

        headers = {
            "User-Agent": SECURITY_CONFIG.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/x-www-form-urlencoded",
            "DNT": "1",
        }

        return await self.client.post(
            self.base_url,
            data=data,
            headers=headers,
            timeout=self.config.timeout,
            follow_redirects=True,
        )

    async def parse_results(self, response: httpx.Response, query: str, page: int) -> SearchResponse:
        doc = lxml_html.fromstring(response.text)
        results = []

        # Startpage uses .wgl-wgl or .result for organic results
        for i, result in enumerate(doc.cssselect(".wgl-wgl, .result, [data-testid='result']"), 1):
            try:
                # Title
                title_elem = result.cssselect("h2.wgl-title, a.wgl-title, h3, a.result-title")
                # URL
                url_elem = result.cssselect("a.wgl-display-url, a.wgl-site-title, cite, a[href^='http']")
                # Snippet
                desc_elem = result.cssselect(".wgl-oneline, .description, .snippet, p")

                title = _extract_text(title_elem[0]) if title_elem else ""
                url = safe_extract_url(url_elem[0].get("href", "")) if url_elem else None
                snippet = _extract_text(desc_elem[0]) if desc_elem else ""

                if not url or not title:
                    continue

                results.append(SearchResult(
                    url=url, title=title, snippet=snippet,
                    engine=EngineType.STARTPAGE, rank=(page - 1) * 10 + i,
                    source_domain=urlparse(url).netloc.replace("www.", ""),
                ))
            except Exception:
                continue

        return SearchResponse(
            query=query, engine=self.engine_type, results=tuple(results),
            page=page,
        )


class MojeekProvider(SearchProvider):
    """Mojeek HTML scraper (independent index)."""

    @property
    def base_url(self) -> str:
        return "https://www.mojeek.com/search"

    @property
    def params(self) -> dict:
        return {"fmt": "html"}

    async def _execute_request(self, query: str, page: int) -> httpx.Response:
        params = {**self.params, "q": query}
        if page > 1:
            params["s"] = str((page - 1) * 10)

        headers = {
            "User-Agent": SECURITY_CONFIG.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
        }

        return await self.client.get(
            self.base_url,
            params=params,
            headers=headers,
            timeout=self.config.timeout,
            follow_redirects=True,
        )

    async def parse_results(self, response: httpx.Response, query: str, page: int) -> SearchResponse:
        from lxml import html as lxml_html

        doc = lxml_html.fromstring(response.text)
        results = []

        # Mojeek uses .results-standard .result
        for i, result in enumerate(doc.cssselect(".results-standard .result, .result-item, .result"), 1):
            try:
                title_elem = result.cssselect(".title, h3 a, a.title")
                url_elem = result.cssselect(".url a, a[href^='http']")
                desc_elem = result.cssselect(".desc, .snippet, p")

                title = str(safe_extract_text(lxml_html.tostring(title_elem[0], encoding="unicode"))) if title_elem else ""
                url = safe_extract_url(url_elem[0].get("href", "")) if url_elem else None
                snippet = str(safe_extract_text(lxml_html.tostring(desc_elem[0], encoding="unicode"))) if desc_elem else ""

                if not url or not title:
                    continue

                results.append(SearchResult(
                    url=url, title=title, snippet=snippet,
                    engine=EngineType.MOJEEK, rank=(page - 1) * 10 + i,
                    source_domain=urlparse(url).netloc.replace("www.", ""),
                ))
            except Exception:
                continue

        return SearchResponse(
            query=query, engine=self.engine_type, results=tuple(results),
            page=page,
        )


# ──────────────────────────────────────────────────────────────
# PROVIDER REGISTRY
# ──────────────────────────────────────────────────────────────

PROVIDER_MAP: dict[EngineType, type[SearchProvider]] = {
    EngineType.DUCKDUCKGO: DuckDuckGoProvider,
    EngineType.BRAVE: BraveSearchProvider,
    EngineType.STARTPAGE: StartpageProvider,
    EngineType.MOJEEK: MojeekProvider,
}


async def create_provider(engine: EngineType, client: httpx.AsyncClient) -> SearchProvider:
    """Factory function to create provider instance."""
    from bfsb.core.config import get_engine_configs

    configs = get_engine_configs()
    config = configs.get(engine)
    if not config or not config.enabled:
        raise ValueError(f"Engine {engine} not configured or disabled")

    provider_class = PROVIDER_MAP.get(engine)
    if not provider_class:
        raise ValueError(f"No provider for engine {engine}")

    return provider_class(config, client)