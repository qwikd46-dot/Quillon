"""Strict YouTube content filter.

Blocks a YouTube search whose query text is on the shared blocklist and
drops individual feed/Shorts items whose title, channel or description
text is on it. The term list itself lives in
:mod:`quillon.core.search.safety` so the Quillon search bar and this filter
always agree.

This is a text filter, so it is a safety net rather than a guarantee:
items that carry no suggestive wording, or wording in a language the list
does not cover, still reach the page. YouTube's own Restricted Mode is
the only server-side filter.
"""

from __future__ import annotations

from typing import Any, Optional

from quillon.core.search.safety import (
    EXPLICIT_TERMS,
    SITE_TERMS,
    SUGGESTIVE_TERMS,
    USER_TERMS_PATH,
    enabled,
    matched_term,
    reset_cache,
    terms,
)


IGNORE_KEYS = frozenset({
    "videoId", "channelId", "trackingParams", "clickTrackingParams",
    "canonicalBaseUrl", "url", "link", "params", "serializedShareEntity",
    "loggingDirectives", "clientScreenNonce", "thumbnails", "responseContext",
    "serviceIntegrityDimensions", "playbackTracking", "index",
})

# Only single content items are ever removed. Container renderers (tab,
# section, shelf, itemSection, ...) are matched on purpose: dropping one
# would take a whole shelf of innocent videos with it.
ITEM_RENDERER_KEYS = frozenset({
    "reelItemRenderer", "reelShelfRenderer", "videoRenderer",
    "gridVideoRenderer", "compactVideoRenderer", "playlistVideoRenderer",
    "richItemRenderer", "radioRenderer", "movieRenderer", "lockupViewModel",
})

_MAX_DEPTH = 30
_MAX_TEXT = 4000
_MAX_ITEM_TEXT = 1500
_MAX_LIST_SCAN = 80

__all__ = [
    "EXPLICIT_TERMS", "SITE_TERMS", "SUGGESTIVE_TERMS", "USER_TERMS_PATH",
    "enabled", "matched_term", "reset_cache", "terms", "IGNORE_KEYS",
    "ITEM_RENDERER_KEYS", "filter_tree", "item_text",
]


def _collect_text(value: Any, out: list[str], depth: int = 0) -> None:
    if depth > 12 or len(out) > 200:
        return
    if isinstance(value, str):
        if value:
            out.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in IGNORE_KEYS:
                continue
            _collect_text(item, out, depth + 1)
    elif isinstance(value, list):
        for item in value[:_MAX_LIST_SCAN]:
            _collect_text(item, out, depth + 1)


def _looks_like_item(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(key in ITEM_RENDERER_KEYS for key in value)


def item_text(value: Any) -> str:
    parts: list[str] = []
    _collect_text(value, parts)
    return " ".join(parts)[:_MAX_TEXT]


def filter_tree(data: Any, depth: int = 0) -> tuple[int, list[str]]:
    """Remove feed items whose text matches the blocklist.

    Returns the number of items removed and the terms that matched, so
    the caller can log why a response changed.
    """
    removed = 0
    hits: list[str] = []

    def consider(node: Any) -> bool:
        """Test one node as a possible item. True if it was removed."""
        nonlocal removed
        if not _looks_like_item(node):
            return False
        text = item_text(node)
        # Do not skip long items. A search result with detailedMetadataSnippets
        # routinely exceeds any fixed cap, and skipping on length meant the
        # MORE metadata an adult video carried, the more reliably it got
        # through — the exact inverse of the intent.
        hit = matched_term(text)
        if not hit:
            return False
        removed += 1
        if hit not in hits:
            hits.append(hit)
        return True

    def walk(node: Any, level: int) -> None:
        nonlocal removed
        if level > _MAX_DEPTH:
            return
        if isinstance(node, list):
            for index in range(len(node) - 1, -1, -1):
                if consider(node[index]):
                    del node[index]
                    continue
                walk(node[index], level + 1)
        elif isinstance(node, dict):
            # A dict value can itself be an item renderer. entityBatchUpdate
            # mutations[].payload.videoRenderer arrives exactly that way, so
            # testing only list elements left YouTube's live-update path
            # structurally unfiltered.
            if consider(node):
                node.clear()
                return
            for value in node.values():
                walk(value, level + 1)

    walk(data, depth)
    return removed, hits
