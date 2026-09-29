"""Search models — Core data structures for the local metasearch engine."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from urllib.parse import urlparse
import re


class EngineType(Enum):
    """Supported search engines."""
    BRAVE = "brave"
    DUCKDUCKGO = "duckduckgo"
    STARTPAGE = "startpage"
    MOJEEK = "mojeek"
    BING = "bing"  # Requires API key
    LOCAL = "local"  # Quillon custom local BM25 engine


@dataclass(frozen=True)
class SearchResult:
    """Single search result — immutable, validated."""
    url: str
    title: str
    snippet: str
    engine: EngineType
    rank: int  # 1-based position in engine's results
    timestamp: datetime = field(default_factory=datetime.now)
    favicon_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    published_date: Optional[datetime] = None
    source_domain: str = ""

    def __post_init__(self) -> None:
        """Validate and normalize on creation."""
        # Normalize URL
        object.__setattr__(self, 'url', self._normalize_url(self.url))
        object.__setattr__(self, 'source_domain', self._extract_domain(self.url))
        
        # Sanitize text fields
        object.__setattr__(self, 'title', self._sanitize_text(self.title, 200))
        object.__setattr__(self, 'snippet', self._sanitize_text(self.snippet, 500))

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Ensure URL is valid and uses HTTPS where possible."""
        url = url.strip()
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        # Basic validation
        try:
            parsed = urlparse(url)
            if not parsed.netloc:
                return "about:blank"
        except Exception:
            return "about:blank"
        return url

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract clean domain from URL."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            # Remove www.
            if domain.startswith('www.'):
                domain = domain[4:]
            return domain
        except Exception:
            return "unknown"

    @staticmethod
    def _sanitize_text(text: str, max_len: int) -> str:
        """Remove control chars, limit length."""
        if not text:
            return ""
        # Remove control characters except newlines/tabs
        cleaned = ''.join(ch for ch in text if ord(ch) >= 32 or ch in '\n\t')
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        if len(cleaned) > max_len:
            cleaned = cleaned[:max_len - 1] + '…'
        return cleaned

    def to_dict(self) -> dict:
        """Serialize for Qt/JSON."""
        return {
            'url': self.url,
            'title': self.title,
            'snippet': self.snippet,
            'engine': self.engine.value,
            'rank': self.rank,
            'timestamp': self.timestamp.isoformat(),
            'favicon_url': self.favicon_url,
            'thumbnail_url': self.thumbnail_url,
            'published_date': self.published_date.isoformat() if self.published_date else None,
            'source_domain': self.source_domain,
        }


@dataclass(frozen=True)
class SearchResponse:
    """Response from a single engine."""
    query: str
    engine: EngineType
    results: tuple[SearchResult, ...]
    page: int
    total_estimated: Optional[int] = None
    response_time_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None

    @property
    def result_count(self) -> int:
        return len(self.results)


@dataclass(frozen=True)
class MergedSearchResponse:
    """Merged results from all engines."""
    query: str
    results: tuple[SearchResult, ...]
    page: int
    engines_used: tuple[EngineType, ...]
    total_results: int
    response_time_ms: float = 0.0

    @property
    def result_count(self) -> int:
        return len(self.results)


# ──────────────────────────────────────────────────────────────
# SAFE HTML PARSING — Virus-proof parsing utilities
# ──────────────────────────────────────────────────────────────

ALLOWED_TAGS = frozenset({'b', 'strong', 'i', 'em', 'u', 'span', 'mark'})
ALLOWED_ATTRS = frozenset({'class', 'id'})

def safe_extract_text(html: str, max_len: int = 500) -> str:
    """Extract plain text from HTML — no scripts, no styles, no events."""
    if not html:
        return ""
    try:
        from lxml import html as lxml_html
        from lxml.html.clean import Cleaner
        
        cleaner = Cleaner(
            scripts=True,
            javascript=True,
            comments=True,
            style=True,
            links=False,
            meta=True,
            page_structure=False,
            processing_instructions=True,
            embedded=True,
            frames=True,
            forms=True,
            annoying_tags=True,
            remove_tags=set(),  # We handle allowed tags manually
            kill_tags={'script', 'style', 'iframe', 'object', 'embed', 
                       'applet', 'form', 'input', 'button', 'select', 'textarea'},
        )
        
        doc = lxml_html.fromstring(html)
        cleaner(doc)
        
        # Get text content only
        text = doc.text_content()
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) > max_len:
            text = text[:max_len - 1] + '…'
        return text
    except Exception:
        # Fallback: strip tags with regex
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) > max_len:
            text = text[:max_len - 1] + '…'
        return text


def safe_extract_url(href: str, base_url: str = "") -> Optional[str]:
    """Validate and normalize URL — reject javascript:, data:, etc."""
    if not href:
        return None
    href = href.strip()
    
    # Reject dangerous schemes
    dangerous_schemes = {'javascript:', 'data:', 'vbscript:', 'file:', 'mailto:', 'tel:'}
    href_lower = href.lower()
    for scheme in dangerous_schemes:
        if href_lower.startswith(scheme):
            return None
    
    # Resolve relative URLs
    if href.startswith('//'):
        href = 'https:' + href
    elif href.startswith('/') and base_url:
        from urllib.parse import urljoin
        href = urljoin(base_url, href)
    elif not href.startswith(('http://', 'https://')):
        return None
    
    # Final validation
    try:
        parsed = urlparse(href)
        if not parsed.netloc or not parsed.scheme in ('http', 'https'):
            return None
    except Exception:
        return None
    
    return href