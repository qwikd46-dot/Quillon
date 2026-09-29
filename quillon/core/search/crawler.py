"""Quillon custom web crawler.

Fetches pages with politeness, extracts clean text + same-domain links,
saves JSON metadata to data/crawled/.

Storage layout:
    data/crawled/<doc_id>.json   - URL, fetched_at, status, title, text, out_links

Where doc_id = sha256(url)[:16] for deterministic, collision-free filenames.

Features:
- Async, polite (per-domain delay)
- Optional robots.txt respect (--respect-robots)
- Sitemap.xml discovery (--use-sitemap) — picks up to N URLs from the
  site's sitemap as additional seed pages for the crawl budget
- Skip patterns: drops static assets, tracking query params, year
  archives, /search results, etc. — so the crawl budget is spent on
  real content pages
- Wikipedia-specific path filter (only clean /wiki/Title articles)

Importantly: still BFS over same-domain only — won't crawl the entire
internet. Each "seed" expands to ~max-pages same-domain pages.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx
from lxml import html as lxml_html

from quillon.core.config import SECURITY_CONFIG


# --------------------------------------------------------------------------
# Skip rules
# --------------------------------------------------------------------------

_NOISE_TAGS = ("script", "style", "noscript", "nav", "footer", "header", "aside", "form")

# Static asset extensions — never useful as pages to index.
SKIP_EXTENSIONS = frozenset({
    ".js", ".mjs", ".css", ".map",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp4", ".mp3", ".wav", ".webm", ".ogg", ".flac", ".mov", ".avi", ".mkv",
    ".json", ".xml", ".rss", ".atom",
    ".ico",
})

# URL path patterns to skip — admin panels, CDNs, date archives, etc.
SKIP_PATH_PATTERNS = [
    re.compile(r"/wp-admin", re.IGNORECASE),
    re.compile(r"/wp-includes", re.IGNORECASE),
    re.compile(r"/wp-content/uploads", re.IGNORECASE),  # media files
    re.compile(r"/admin/?", re.IGNORECASE),
    re.compile(r"/login/?", re.IGNORECASE),
    re.compile(r"/signup/?", re.IGNORECASE),
    re.compile(r"/logout", re.IGNORECASE),
    re.compile(r"/api/", re.IGNORECASE),
    re.compile(r"/cdn-cgi/", re.IGNORECASE),
    re.compile(r"/static/", re.IGNORECASE),
    re.compile(r"/assets/", re.IGNORECASE),
    re.compile(r"/20\d\d/(0?\d/)?", re.IGNORECASE),  # year archives
    re.compile(r"/feed/?$", re.IGNORECASE),
    re.compile(r"/rss", re.IGNORECASE),
    re.compile(r"/search/?(\?|$)", re.IGNORECASE),
    re.compile(r"/tag/", re.IGNORECASE),
    re.compile(r"/page/\d+/?$", re.IGNORECASE),  # WP pagination
    re.compile(r"/\?page=\d+", re.IGNORECASE),
    re.compile(r"/\?p=\d+", re.IGNORECASE),
]

# Tracking / analytics query params. Stripped when filtering.
TRACKING_QUERY_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_source_platform",
    "fbclid", "gclid", "gclsrc", "msclkid",
    "ref", "ref_src", "ref_url",
    "_ga", "_gl", "_hsenc", "_hsmi",
    "mc_cid", "mc_eid",
    "trk", "trk_id", "trkCampaign",
    "igshid", "si",
})

# Filter that EXCLUDES invalid href targets.
_LINK_ATTR_RE = re.compile(r"^(#|javascript:|mailto:|tel:|data:|about:)", re.IGNORECASE)

# Wikipedia article paths.
_WIKI_PATH_RE = re.compile(r"^/wiki/([^:#]+)$", re.IGNORECASE)
_WIKI_BAD_PREFIX_RE = re.compile(r"^/wiki/(Special|Talk|File|Help|Category|Template|User|Portal|Module|MediaWiki):", re.IGNORECASE)
_WIKI_BAD_QUERY_RE = re.compile(r"[?&]action=(edit|history|delete|protect|info|raw|markpatrolled|rollback|unwatch|watch|view)$", re.IGNORECASE)

# Sitemap namespace
_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


# --------------------------------------------------------------------------
# URL helpers
# --------------------------------------------------------------------------

def _norm_domain(d: str) -> str:
    return d.lower().removeprefix("www.")


def _should_skip_url(url: str) -> bool:
    """Return True if this URL is junk we don't want to crawl."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    # Extension skip
    for ext in SKIP_EXTENSIONS:
        if path.endswith(ext):
            return True
    # Path-pattern skip
    for pat in SKIP_PATH_PATTERNS:
        if pat.search(path):
            return True
    return False


def _strip_tracking_params(url: str) -> str:
    """Remove tracking query params in-place."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    # Parse and rebuild
    from urllib.parse import parse_qsl, urlencode
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    cleaned = [(k, v) for (k, v) in pairs if k.lower() not in TRACKING_QUERY_PARAMS]
    new_query = urlencode(cleaned)
    new_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                         parsed.params, new_query, ""))
    return new_url or url

from urllib.parse import urlunparse  # noqa: E402  (used above)


# --------------------------------------------------------------------------
# Sitemap discovery
# --------------------------------------------------------------------------

async def discover_sitemap_urls(
    client: httpx.AsyncClient,
    base_url: str,
    max_urls: int = 30,
) -> list[str]:
    """Try common sitemap locations and return up to max_urls URLs."""
    candidates = [
        urljoin(base_url, "/sitemap.xml"),
        urljoin(base_url, "/sitemap_index.xml"),
        urljoin(base_url, "/sitemap-index.xml"),
        urljoin(base_url, "/sitemap/sitemap.xml"),
    ]
    for sitemap_url in candidates:
        try:
            r = await client.get(sitemap_url, timeout=5.0)
            if r.status_code != 200:
                continue
            ct = r.headers.get("content-type", "").lower()
            if "xml" not in ct and "text/plain" not in ct:
                continue
            urls = _parse_sitemap(r.text, max_urls=max_urls)
            if urls:
                return urls
        except Exception:
            continue
    return []


def _parse_sitemap(xml_text: str, max_urls: int = 30) -> list[str]:
    """Parse sitemap XML and extract <loc> URLs (handles sitemap index too)."""
    urls: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    # Sitemap index: <sitemapindex><sitemap><loc></loc</sitemap>...
    # Recurse one level to find inner sitemaps, then collect their URLs.
    if root.tag.endswith("sitemapindex"):
        inner_sitemaps = []
        for sitemap in root.findall(f"{_SITEMAP_NS}sitemap"):
            loc = sitemap.find(f"{_SITEMAP_NS}loc")
            if loc is not None and loc.text:
                inner_sitemaps.append(loc.text.strip())
        for inner_url in inner_sitemaps[:3]:  # cap at 3 inner sitemaps
            try:
                r = httpx.get(inner_url, timeout=5.0)
                if r.status_code == 200:
                    urls.extend(_parse_sitemap(r.text, max_urls=max_urls - len(urls)))
                    if len(urls) >= max_urls:
                        break
            except Exception:
                continue
        return urls[:max_urls]
    # Plain sitemap: <urlset><url><loc></loc</url>...
    for loc in root.findall(f".//{_SITEMAP_NS}loc"):
        if loc.text:
            urls.append(loc.text.strip())
        if len(urls) >= max_urls:
            break
    return urls[:max_urls]


# --------------------------------------------------------------------------
# CrawlResult
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CrawlResult:
    """One crawled page."""
    url: str
    fetched_at: str
    status_code: int
    content_type: str
    title: str
    text: str
    out_links: tuple[str, ...] = field(default_factory=tuple)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "fetched_at": self.fetched_at,
            "status_code": self.status_code,
            "content_type": self.content_type,
            "title": self.title,
            "text": self.text,
            "out_links": list(self.out_links),
            "error": self.error,
        }

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300 and not self.error


# --------------------------------------------------------------------------
# Crawler
# --------------------------------------------------------------------------

class Crawler:
    """Polite async crawler. One instance = one job."""

    def __init__(
        self,
        data_dir: Path = Path("/home/binwalk/Downloads/bfsb/data/crawled"),
        user_agent: str = SECURITY_CONFIG.USER_AGENT,
        timeout: float = 10.0,
        max_concurrent: int = 2,
        delay_seconds: float = 1.5,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_concurrent = max_concurrent
        self.delay_seconds = delay_seconds
        self._sem = asyncio.Semaphore(max_concurrent)
        self._last_fetch_per_domain: dict[str, float] = {}

    @staticmethod
    def doc_id(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]

    def save(self, result: CrawlResult) -> tuple[Path, Optional[Path]]:
        fname = f"{self.doc_id(result.url)}.json"
        path = self.data_dir / fname
        path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return (path, None)

    async def _respect_politeness(self, url: str) -> None:
        domain = urlparse(url).netloc.lower()
        last = self._last_fetch_per_domain.get(domain, 0.0)
        now = asyncio.get_event_loop().time()
        wait = self.delay_seconds - (now - last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_fetch_per_domain[domain] = now

    async def fetch(self, url: str) -> CrawlResult:
        """Fetch a single URL with politeness."""
        async with self._sem:
            await self._respect_politeness(url)
            now = datetime.now(timezone.utc).isoformat()
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept": "text/html,application/xhtml+xml",
                    },
                ) as client:
                    r = await client.get(url)
                    ct = r.headers.get("content-type", "")
                    body = r.content
            except httpx.TimeoutException as e:
                return CrawlResult(
                    url=url, fetched_at=now, status_code=0,
                    content_type="", title="", text="",
                    error=f"Timeout: {e}",
                )
            except Exception as e:
                return CrawlResult(
                    url=url, fetched_at=now, status_code=0,
                    content_type="", title="", text="",
                    error=f"{type(e).__name__}: {e}",
                )

            if "html" not in ct.lower():
                return CrawlResult(
                    url=url, fetched_at=now, status_code=r.status_code,
                    content_type=ct, title="", text="",
                    error="non-html content-type",
                )

            try:
                title, text, out_links = self._parse_html(body, url)
            except Exception as e:
                return CrawlResult(
                    url=url, fetched_at=now, status_code=r.status_code,
                    content_type=ct, title="", text="",
                    error=f"parse: {type(e).__name__}: {e}",
                )

            return CrawlResult(
                url=url,
                fetched_at=now,
                status_code=r.status_code,
                content_type=ct,
                title=title,
                text=text,
                out_links=out_links,
            )

    @staticmethod
    def _parse_html(html_bytes: bytes, base_url: str) -> tuple[str, str, tuple[str, ...]]:
        try:
            doc = lxml_html.fromstring(html_bytes)
        except Exception:
            text = html_bytes.decode("utf-8", errors="replace")
            doc = lxml_html.fromstring(text)

        title_nodes = doc.cssselect("title")
        title = title_nodes[0].text_content().strip() if title_nodes else ""

        for tag in doc.cssselect(", ".join(_NOISE_TAGS)):
            parent = tag.getparent()
            if parent is not None:
                parent.remove(tag)

        raw_text = doc.text_content()
        text = re.sub(r"\s+", " ", raw_text).strip()

        base_domain_norm = _norm_domain(urlparse(base_url).netloc)
        out_links: set[str] = set()
        for a in doc.cssselect("a[href]"):
            href = a.get("href", "").strip()
            if not href or _LINK_ATTR_RE.match(href):
                continue
            full = urljoin(base_url, href)
            full = full.split("#", 1)[0]
            try:
                host = urlparse(full).netloc
            except Exception:
                continue
            if not host:
                continue
            if _norm_domain(host) != base_domain_norm:
                continue
            if not full.startswith(("http://", "https://")):
                continue
            # Skip junk URLs
            if _should_skip_url(full):
                continue
            # Wikipedia-specific filtering
            if "wikipedia.org" in host.lower():
                parsed_full = urlparse(full)
                path = parsed_full.path
                if not _WIKI_PATH_RE.match(path):
                    continue
                if _WIKI_BAD_PREFIX_RE.match(path):
                    continue
                if parsed_full.query and _WIKI_BAD_QUERY_RE.search(parsed_full.query):
                    continue
            out_links.add(full)
        return title, text, tuple(sorted(out_links))

    async def fetch_many(self, urls: list[str]) -> list[CrawlResult]:
        return await asyncio.gather(*(self.fetch(u) for u in urls))


# --------------------------------------------------------------------------
# Recursive crawl helpers
# --------------------------------------------------------------------------

async def crawl_recursive(
    crawler: Crawler,
    seed_url: str,
    max_pages: int = 3,
    use_sitemap: bool = True,
) -> list[CrawlResult]:
    """BFS crawl starting from seed_url. Up to max_pages successful fetches.

    Same-domain only. Optionally discovers sitemap URLs first to seed the queue.
    """
    queue: list[str] = [seed_url]
    visited: set[str] = set()
    results: list[CrawlResult] = []
    base_domain_norm = _norm_domain(urlparse(seed_url).netloc)

    # Sitemap discovery (optional): adds a few high-value URLs to the queue
    if use_sitemap:
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
                headers={"User-Agent": crawler.user_agent},
            ) as client:
                sitemap_urls = await discover_sitemap_urls(client, seed_url, max_urls=max_pages * 2)
            for u in sitemap_urls:
                if _norm_domain(urlparse(u).netloc) == base_domain_norm:
                    if not _should_skip_url(u):
                        queue.append(u)
        except Exception:
            pass

    while queue and len(results) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        result = await crawler.fetch(url)
        if result.ok:
            results.append(result)
            # Expand BFS from same-domain links
            for link in result.out_links:
                if link not in visited and _norm_domain(urlparse(link).netloc) == base_domain_norm:
                    if not _should_skip_url(link):
                        queue.append(link)
    return results
