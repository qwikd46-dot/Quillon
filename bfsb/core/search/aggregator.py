"""Server-side multi-source search aggregator.

Each "source" is a single async coroutine that hits a public API and
returns a list of plain ``Result`` records. The ``aggregate_search``
function runs them concurrently with a single watchdog timeout, then
merges by URL dedupe + rank ordering.

Why we don't use the existing ``SearchManager``-based stack:

* All upstream engines there (Startpage, DuckDuckGo, Brave, Mojeek)
  now serve either an Anubis PoW challenge, a JS-only stub, or 403.
* They were tightly coupled to ``httpx`` + HTML scraping, which
  didn't survive the anti-bot wave.

What we use instead:

* SearXNG (``http://127.0.0.1:8888/search?format=json``) — local
  metasearch engine, started and stopped by the BFSB launcher.
  Fans out to Brave, Mojeek, Marginalia, Wikipedia, etc.
* Hacker News Algolia search — recent stories / discussions. Kept
  as a bonus since it's a different kind of result.

SearXNG is the primary source; HN is a bonus. Earlier sources
(``wikipedia_source``, ``ddg_instant_source``) were removed — they
only returned Wikipedia article URLs and DDG knowledge-graph stubs
respectively, not the real web pages the user wanted.

All outbound traffic comes from BFSB's helper thread, not from the
embedded chromium. The browser's network inspector still sees only
``127.0.0.1:8889``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import time
from typing import Optional
import httpx


# --- shared client lifecycle ------------------------------------------

_http_client: Optional[httpx.AsyncClient] = None
_http_client_lock = asyncio.Lock()


async def get_client() -> httpx.AsyncClient:
    """Module-level shared httpx client."""
    global _http_client
    if _http_client is not None:
        return _http_client
    async with _http_client_lock:
        if _http_client is None:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json,text/javascript,*/*",
            }
            _http_client = httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(connect=3.0, read=6.0, write=3.0, pool=2.0),
                follow_redirects=True,
                verify=True,
            )
    return _http_client


async def shutdown() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


# --- result shape ---------------------------------------------------------

@dataclasses.dataclass
class Result:
    url: str
    title: str
    snippet: str
    source: str
    rank: int = 0


@dataclasses.dataclass
class AggregateResponse:
    query: str
    results: list[Result]
    abstract: Optional[str] = None
    abstract_source: Optional[str] = None
    abstract_url: Optional[str] = None
    total_ms: int = 0
    errors: dict[str, str] = dataclasses.field(default_factory=dict)


# --- sources --------------------------------------------------------------

WIKIPEDIA = "wikipedia"
HACKERNEWS = "hackernews"
DDG_INSTANT = "ddg_instant"
SEARXNG = "searxng"
SEARXNG_URL = "http://127.0.0.1:8888/search"

# Sources still in active use. (Wikipedia + DDG Instant were removed —
# they only returned Wikipedia article URLs and DDG knowledge-graph
# stubs, not the real web pages the user wanted. Kept as constants in
# case they're reinstated later.)
_ACTIVE_SOURCES = (SEARXNG, HACKERNEWS)


async def _safe_json(client, method, url, *, params, label):
    try:
        r = await client.request(method, url, params=params)
        if r.status_code >= 400:
            return label, None, f"http_{r.status_code}"
        return label, r.json(), None
    except Exception as e:
        return label, None, f"{type(e).__name__}: {e}"


async def hackernews_source(q: str) -> list[Result]:
    client = await get_client()
    _, js, err = await _safe_json(
        client, "GET", "https://hn.algolia.com/api/v1/search",
        params={"query": q, "hitsPerPage": 8, "tags": "story"},
        label=HACKERNEWS,
    )
    if err or not js:
        return []
    results: list[Result] = []
    for h in (js.get("hits") or [])[:8]:
        url = h.get("url") or h.get("story_url") or ""
        title = h.get("title") or h.get("story_title") or "(untitled)"
        if not url:
            continue
        clean_title = re.sub(r"<[^>]+>", "", title)
        snippet_parts = []
        if h.get("story_text"):
            snippet_parts.append(re.sub(r"<[^>]+>", "", h["story_text"])[:240])
        if h.get("points") is not None:
            snippet_parts.append(f"{h['points']} points")
        if h.get("num_comments") is not None:
            snippet_parts.append(f"{h['num_comments']} comments")
        results.append(Result(
            url=url,
            title=clean_title[:200],
            snippet=" · ".join(snippet_parts)[:480],
            source=HACKERNEWS,
        ))
    return results


async def searxng_source(q: str) -> list[Result]:
    """Query the local SearXNG instance. Returns an empty list if SearXNG
    isn't running — callers should treat that as a soft miss, not an
    error, since the launcher tears SearXNG down with the browser."""
    client = await get_client()
    _, js, err = await _safe_json(
        client, "GET", SEARXNG_URL,
        params={
            "q": q,
            "format": "json",
            "language": "en",
            "safesearch": 0,
        },
        label=SEARXNG,
    )
    if err or not js:
        return []
    results: list[Result] = []
    for r in (js.get("results") or [])[:8]:
        url = (r.get("url") or "").strip()
        title = (r.get("title") or "").strip()
        content = (r.get("content") or "").strip()
        if not url or not title:
            continue
        # SearXNG returns titles/content with embedded <em> highlight tags.
        # Strip them so they don't show as raw HTML in the results page.
        title = re.sub(r"<[^>]+>", "", title)
        content = re.sub(r"<[^>]+>", "", content)
        results.append(Result(
            url=url,
            title=title[:200],
            snippet=content[:480],
            source=SEARXNG,
        ))
    return results


# --- aggregator -----------------------------------------------------------

async def aggregate_search(query: str, *, max_total_ms: int = 8000) -> AggregateResponse:
    t0 = time.perf_counter()

    async def timed(coro, label="src"):  # PERF-DEBUG(phase1): label param added
        _t = time.perf_counter()  # PERF-DEBUG(phase1)
        try:
            out = await asyncio.wait_for(coro, timeout=max_total_ms / 1000)
            # PERF-DEBUG(phase1): per-source latency
            print(f"[PERF] source {label}: {(time.perf_counter()-_t)*1000:.1f}ms items={len(out) if out is not None else 'TIMEOUT'}", flush=True)
            return out
        except asyncio.TimeoutError:
            print(f"[PERF] source {label}: TIMEOUT >{max_total_ms}ms", flush=True)  # PERF-DEBUG(phase1)
            return None

    # SearXNG is the primary web-search source. HackerNews is a bonus
    # (different kind of result: recent stories / discussions). The legacy
    # Wikipedia + DDG Instant sources were removed because they only ever
    # returned Wikipedia article URLs and knowledge-graph stubs, never
    # the real web pages the user wanted.
    searxng_res, hn_res = await asyncio.gather(
        timed(searxng_source(query), label=SEARXNG),      # PERF-DEBUG(phase1)
        timed(hackernews_source(query), label=HACKERNEWS),  # PERF-DEBUG(phase1)
    )

    errors: dict[str, str] = {}
    sources: list[tuple[str, list[Result]]] = []

    if searxng_res is None:
        # Timeout — SearXNG probably slow. Don't surface as a hard error.
        pass
    elif isinstance(searxng_res, Exception):
        # Connection refused / DNS failure — SearXNG isn't running.
        # Silently skip: the launcher will start it next session.
        pass
    elif searxng_res:
        sources.append((SEARXNG, searxng_res))

    if hn_res is None:
        errors[HACKERNEWS] = "timeout"
    elif isinstance(hn_res, Exception):
        errors[HACKERNEWS] = str(hn_res)
    elif hn_res:
        sources.append((HACKERNEWS, hn_res))

    seen: set[str] = set()
    merged: list[Result] = []
    for _, items in sources:
        for r in items:
            key = r.url.lower().rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            merged.append(r)

    for i, r in enumerate(merged, 1):
        r.rank = i

    total_ms = int((time.perf_counter() - t0) * 1000)
    return AggregateResponse(
        query=query,
        results=merged,
        total_ms=total_ms,
        errors=errors,
    )
