"""Official-site and result-quality policy for aggregated search results."""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional
from urllib.parse import urlparse


REPOSITORY_DOMAINS = frozenset({
    "github.com",
    "gist.github.com",
    "gitlab.com",
    "bitbucket.org",
    "codeberg.org",
    "sourceforge.net",
})
DISCUSSION_DOMAINS = frozenset({
    "news.ycombinator.com",
    "reddit.com",
    "stackoverflow.com",
    "quora.com",
    "medium.com",
    "pinterest.com",
})
REPOSITORY_QUERY_TERMS = frozenset({
    "github",
    "gitlab",
    "codeberg",
    "sourceforge",
    "repository",
    "repo",
    "source",
    "code",
    "developer",
    "programming",
    "library",
    "sdk",
    "api",
    "open-source",
})
_WORD_RE = re.compile(r"[a-z0-9]+")


def normalized_host(value: str) -> str:
    try:
        host = (urlparse(value).hostname or "").lower().rstrip(".")
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def is_official_host(value: str, official_url: Optional[str]) -> bool:
    host = normalized_host(value)
    official = normalized_host(official_url or "")
    if not host or not official:
        return False
    if host == official or host.endswith("." + official):
        return True
    return "." in host and official.endswith("." + host)


def _query_words(query: str) -> set[str]:
    return set(_WORD_RE.findall((query or "").lower()))


def query_allows_repositories(query: str) -> bool:
    return bool(_query_words(query) & REPOSITORY_QUERY_TERMS)


def is_repository_result(value: str) -> bool:
    host = normalized_host(value)
    return any(host == domain or host.endswith("." + domain) for domain in REPOSITORY_DOMAINS)


def is_discussion_result(value: str) -> bool:
    host = normalized_host(value)
    return any(host == domain or host.endswith("." + domain) for domain in DISCUSSION_DOMAINS)


def rank_results(
    query: str,
    results: Iterable[Any],
    official_url: Optional[str] = None,
) -> tuple[list[Any], int]:
    words = _query_words(query)
    allow_repositories = query_allows_repositories(query)
    ranked: list[tuple[float, int, Any]] = []
    filtered = 0
    for index, result in enumerate(results):
        url = str(getattr(result, "url", "") or "")
        host = normalized_host(url)
        title = str(getattr(result, "title", "") or "").lower()
        source = str(getattr(result, "source", "") or "").lower()
        official = is_official_host(url, official_url)
        if is_repository_result(url) and not official and not allow_repositories:
            filtered += 1
            continue
        score = float(max(0, 1000 - index))
        if official:
            score += 10000
        if source in {"hackernews", "hacker news", "hn"}:
            score -= 5000
        if is_discussion_result(url):
            score -= 2000
        if words and words.intersection(_WORD_RE.findall(title)):
            score += 500
        if words and any(word in host for word in words if len(word) > 2):
            score += 250
        ranked.append((score, index, result))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in ranked], filtered
