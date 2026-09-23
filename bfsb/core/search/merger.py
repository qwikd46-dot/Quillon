"""Result merger — Deduplication, ranking, and merging of multi-engine results."""

from dataclasses import dataclass
from collections import defaultdict
from urllib.parse import urlparse
import re

from .models import SearchResult, SearchResponse, EngineType


@dataclass(frozen=True)
class MergedResult:
    """A deduplicated result with merged metadata."""
    url: str
    title: str
    snippet: str
    source_domain: str
    engines: frozenset[str]  # Which engines returned this
    best_rank: int
    weighted_score: float
    favicon_url: str | None = None
    thumbnail_url: str | None = None
    published_date: str | None = None

    def to_search_result(self, engine: EngineType, rank: int) -> SearchResult:
        """Convert to SearchResult for compatibility."""
        # published_date is string in MergedResult, datetime in SearchResult - omit for now
        return SearchResult(
            url=self.url,
            title=self.title,
            snippet=self.snippet,
            engine=engine,
            rank=rank,
            favicon_url=self.favicon_url,
            thumbnail_url=self.thumbnail_url,
            published_date=None,
            source_domain=self.source_domain,
        )


class ResultMerger:
    """Merge and rank results from multiple engines."""
    
    # URL normalization patterns
    TRACKING_PARAMS = frozenset({
        'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
        'fbclid', 'gclid', 'msclkid', 'ref', 'ref_src', 'ref_url',
        '_ga', '_gl', 'mc_cid', 'mc_eid', 'trk', 'trk_id',
    })
    
    def __init__(self, engine_weights: dict[str, float] | None = None):
        self.engine_weights = engine_weights or {}
    
    @staticmethod
    def normalize_url(url: str) -> str:
        """Normalize URL for deduplication."""
        try:
            parsed = urlparse(url.strip().lower())
            # Remove fragment
            path = parsed.path.rstrip('/')
            # Remove tracking query params
            query_parts = []
            for k, v in (parsed.query.split('&') if parsed.query else []):
                k = k.split('=')[0]
                if k not in ResultMerger.TRACKING_PARAMS:
                    query_parts.append(f"{k}={v.split('=', 1)[1]}" if '=' in k + '=' + v else k)
            query = '&'.join(query_parts)
            return f"{parsed.scheme}://{parsed.netloc}{path}?{query}" if query else f"{parsed.scheme}://{parsed.netloc}{path}"
        except Exception:
            return url
    
    @staticmethod
    def extract_domain(url: str) -> str:
        """Extract clean domain."""
        try:
            domain = urlparse(url).netloc.lower()
            if domain.startswith('www.'):
                domain = domain[4:]
            return domain
        except Exception:
            return "unknown"
    
    def merge(self, responses: list[SearchResponse]) -> list[MergedResult]:
        """Merge multiple engine responses into deduplicated, ranked results."""
        # Group by normalized URL
        url_groups: dict[str, list[SearchResult]] = defaultdict(list)
        
        for resp in responses:
            if not resp.success:
                continue
            weight = self.engine_weights.get(resp.engine.value, 1.0)
            for result in resp.results:
                norm_url = self.normalize_url(result.url)
                url_groups[norm_url].append((result, weight))
        
        # Merge each group
        merged = []
        for norm_url, items in url_groups.items():
            if not items:
                continue
            
            # Best result (highest weight * rank preference)
            primary = max(items, key=lambda x: x[1] / (x[0].rank + 1))
            primary_result, primary_weight = primary
            
            # Collect engines
            engines = frozenset(r.engine.value for r, _ in items)
            
            # Best rank across engines
            best_rank = min(r.rank for r, _ in items)
            
            # Weighted score: engine weight / rank
            weighted_score = sum(w / (r.rank + 1) for r, w in items)
            
            # Pick best snippet (longest meaningful)
            best_snippet = max(
                (r.snippet for r, _ in items if r.snippet),
                key=len,
                default=items[0][0].snippet
            )
            
            merged.append(MergedResult(
                url=norm_url,
                title=primary_result.title,
                snippet=best_snippet,
                source_domain=self.extract_domain(norm_url),
                engines=engines,
                best_rank=best_rank,
                weighted_score=weighted_score,
                favicon_url=next((r.favicon_url for r, _ in items if r.favicon_url), None),
                thumbnail_url=next((r.thumbnail_url for r, _ in items if r.thumbnail_url), None),
                published_date=next((r.published_date for r, _ in items if r.published_date), None),
            ))
        
        # Sort by weighted score descending
        merged.sort(key=lambda m: m.weighted_score, reverse=True)
        return merged


def safe_extract_text(html_or_text: str, max_len: int = 500) -> str:
    """Extract clean text from HTML or plain text."""
    if not html_or_text:
        return ""
    text = html_or_text
    # If it looks like HTML, strip tags
    if '<' in text and '>' in text:
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > max_len:
        text = text[:max_len - 1] + '…'
    return text


def safe_extract_url(url: str) -> str | None:
    """Validate and extract URL."""
    if not url:
        return None
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        if url.startswith('//'):
            url = 'https:' + url
        elif '.' in url:
            url = 'https://' + url
        else:
            return None
    # Basic validation
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if not parsed.netloc:
            return None
    except Exception:
        return None
    return url