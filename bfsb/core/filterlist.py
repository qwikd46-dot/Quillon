"""Adblock Plus filter list parser for EasyList/EasyPrivacy support.

Simplified parser for common ABP filter formats:
- ||example.com^ - block domain and subdomains
- ||example.com/path - block path on domain
- @@||example.com^ - exception (allow)
- /regex/ - regex pattern
- http://example.com/ads/* - plain URL pattern (wildcards * and separator ^)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from urllib.parse import urlparse


class FilterType(Enum):
    BLOCK = "block"
    EXCEPTION = "exception"


@dataclass(frozen=True)
class FilterRule:
    """Parsed filter rule."""
    raw: str
    filter_type: FilterType
    domain: Optional[str] = None      # e.g., "example.com" from ||example.com^
    path_pattern: Optional[str] = None # e.g., "/ads/" from ||example.com/ads/
    regex_pattern: Optional[str] = None # for /regex/ rules
    plain_pattern: Optional[str] = None  # for ABP plain URL patterns (no / /)


class FilterListParser:
    """Parses Adblock Plus format filter lists (simplified)."""

    def __init__(self):
        self._rules: list[FilterRule] = []
        self._domain_rules: dict[str, list[FilterRule]] = {}
        self._generic_rules: list[FilterRule] = []
        self._compiled_regex: dict[int, re.Pattern] = {}      # /regex/ rules
        self._compiled_plain: dict[int, tuple[re.Pattern, FilterRule]] = {}  # (pattern, rule)
        self._plain_substrings: list[tuple[str, FilterRule]] = []  # (substring, rule) for fast 'in' check

    def parse_text(self, text: str) -> int:
        """Parse filter list text. Returns number of rules added."""
        count = 0
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("!") or line.startswith("["):
                continue
            rule = self._parse_line(line)
            if rule:
                self._add_rule(rule)
                count += 1
        return count

    def parse_file(self, filepath: str) -> int:
        """Parse filter list from file."""
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            return self.parse_text(f.read())

    def _parse_line(self, line: str) -> Optional[FilterRule]:
        """Parse a single filter line."""
        original = line
        line = line.strip()

        if not line or line.startswith("!"):
            return None

        # Exception rule
        is_exception = False
        if line.startswith("@@"):
            is_exception = True
            line = line[2:]

        # Skip element hiding rules (##, #@#, #?#)
        if line.startswith("#"):
            return None

        # Regex rule: /pattern/
        if line.startswith("/") and line.endswith("/") and len(line) > 2:
            regex = line[1:-1]
            return FilterRule(
                raw=original,
                filter_type=FilterType.EXCEPTION if is_exception else FilterType.BLOCK,
                regex_pattern=regex,
            )

        # Domain rule: ||domain^ or ||domain/path
        if line.startswith("||"):
            # Strip ABP modifiers ($domain=..., $third-party, etc.)
            line = _strip_modifiers(line)
            if not line:
                return None  # $domain= rules dropped — see _strip_modifiers docstring
            remainder = line[2:]
            if "/" in remainder:
                domain_part, path_part = remainder.split("/", 1)
                domain = domain_part.rstrip("^")
                path_pattern = "/" + path_part
            else:
                domain = remainder.rstrip("^")
                path_pattern = None

            domain = domain.strip("^.").lower()
            if not domain:
                return None

            return FilterRule(
                raw=original,
                filter_type=FilterType.EXCEPTION if is_exception else FilterType.BLOCK,
                domain=domain,
                path_pattern=path_pattern,
            )

        # Plain URL pattern: http://example.com/ads/* or /ads/banner/*
        # Strip ABP modifiers ($third-party, $script, etc.) — we don't use them.
        body = _strip_modifiers(line)
        if not body:
            return None

        # Try to extract a host so we can route to domain_rules (fast path).
        host = _extract_host(body)

        return FilterRule(
            raw=original,
            filter_type=FilterType.EXCEPTION if is_exception else FilterType.BLOCK,
            domain=host,          # may be None → generic
            plain_pattern=body,
        )

    def _add_rule(self, rule: FilterRule):
        """Add rule to internal structures. Pre-compile regex/plain patterns."""
        self._rules.append(rule)
        if rule.domain:
            self._domain_rules.setdefault(rule.domain, []).append(rule)
        else:
            self._generic_rules.append(rule)

        rid = id(rule)

        # Pre-compile /regex/ rules
        if rule.regex_pattern:
            try:
                self._compiled_regex[rid] = re.compile(rule.regex_pattern)
            except re.error:
                self._compiled_regex[rid] = None  # type: ignore[assignment]

        # Plain ABP URL patterns: split into fast substring vs compiled regex
        if rule.plain_pattern:
            pat = rule.plain_pattern
            has_wildcards = "*" in pat or "^" in pat or "|" in pat
            if not has_wildcards:
                # Pure substring — use fast 'in' check, no regex overhead.
                self._plain_substrings.append((pat, rule))
            else:
                comp = _compile_plain_pattern(pat)
                if comp is not None:
                    self._compiled_plain[rid] = (comp, rule)

    def match_url(self, url: str) -> tuple[bool, Optional[str]]:
        """Check if URL matches any rule. Returns (blocked, matching_rule_raw)."""
        try:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return False, None
        except Exception:
            return False, None

        host = parsed.netloc.lower()
        path = parsed.path
        if parsed.query:
            path += "?" + parsed.query
        if not host:
            return False, None

        # Walk parent domains to collect domain-scoped rules.
        host_parts = host.split(".")
        domain_bucket: list[FilterRule] = []
        for i in range(len(host_parts) - 1):
            d = ".".join(host_parts[i:])
            bucket = self._domain_rules.get(d)
            if bucket:
                domain_bucket.extend(bucket)

        target = host + path

        # --- Exception pass (overrides blocks) ---
        # 1. Domain-scoped exceptions
        for rule in domain_bucket:
            if rule.filter_type == FilterType.EXCEPTION and self._match_rule(rule, host, path, target):
                return False, rule.raw

        # 2. Generic substring exceptions (fast 'in' check)
        for sub, rule in self._plain_substrings:
            if rule.filter_type == FilterType.EXCEPTION and sub in target:
                return False, rule.raw

        # 3. Generic regex exceptions
        for comp, rule in self._compiled_plain.values():
            if rule.filter_type == FilterType.EXCEPTION and comp.search(target):
                return False, rule.raw

        # --- Block pass ---
        # 1. Domain-scoped blocks
        for rule in domain_bucket:
            if rule.filter_type == FilterType.BLOCK and self._match_rule(rule, host, path, target):
                return True, rule.raw

        # 2. Generic substring blocks (fast 'in' check)
        for sub, rule in self._plain_substrings:
            if rule.filter_type == FilterType.BLOCK and sub in target:
                return True, rule.raw

        # 3. Generic regex blocks
        for comp, rule in self._compiled_plain.values():
            if rule.filter_type == FilterType.BLOCK and comp.search(target):
                return True, rule.raw

        return False, None

    def _match_rule(self, rule: FilterRule, host: str, path: str, target: str) -> bool:
        """Check if rule matches host/path/target (target = host + path)."""
        # Domain check (applies to ||domain^ rules AND plain-pattern rules that
        # had an extractable host).
        if rule.domain:
            if not (host == rule.domain or host.endswith("." + rule.domain)):
                return False

        # Path pattern (substring) check for ||domain/path rules
        if rule.path_pattern:
            if rule.path_pattern not in path:
                return False

        rid = id(rule)

        # /regex/ rule
        comp = self._compiled_regex.get(rid)
        if comp is not None:
            return bool(comp.search(target))

        # Plain ABP URL pattern with wildcards (domain or generic)
        entry = self._compiled_plain.get(rid)
        if entry is not None:
            comp, _ = entry
            return bool(comp.search(target))

        # Plain ABP URL pattern WITHOUT wildcards — substring check
        if rule.plain_pattern:
            return rule.plain_pattern in target

        return True

    def get_stats(self) -> dict:
        return {
            "total_rules": len(self._rules),
            "domain_rules": sum(len(r) for r in self._domain_rules.values()),
            "generic_rules": len(self._generic_rules),
            "exception_rules": sum(
                1 for r in self._rules if r.filter_type == FilterType.EXCEPTION
            ),
        }


# --- Module-level helpers --------------------------------------------------

# Hosts we consider valid for plain-pattern host extraction.
# Matches "http://foo.bar.example.com/path" or "https://..." or "//foo.bar/..."
_HOST_RE = re.compile(
    r"^(?:https?:)?//([a-z0-9._-]+)",  # host from scheme://host/path
    re.IGNORECASE,
)


def _extract_host(pattern: str) -> Optional[str]:
    """Try to extract a hostname from a plain ABP URL pattern.

    Returns lowercased host or None.
    """
    m = _HOST_RE.match(pattern)
    if not m:
        return None
    host = m.group(1).lower()
    if "." not in host:
        return None
    return host


def _compile_plain_pattern(pattern: str) -> Optional[re.Pattern]:
    """Compile an ABP plain URL pattern into a regex.

    ABP wildcards:
      * → .*
      ^ → separator (end of string or non-alphanumeric/-._%)

    Anchors and pipe (`|`) at start/end of the ABP pattern are treated as
    optional boundaries (we use search() so they're informational only).
    """
    out = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch == "^":
            # Separator: end-of-string or a char that's not alnum/-._%
            out.append(r"(?:$|[^\w\-.%])")
        elif ch == "|":
            # ABP pipe anchor: skip (we use search, not match)
            continue
        elif ch in r".+()[]{}$\?":
            out.append(re.escape(ch))
        else:
            out.append(re.escape(ch) if ch in r"\^$.|?*+()[]{}" else ch)
    body = "".join(out)
    if not body:
        return None
    try:
        return re.compile(body)
    except re.error:
        return None


def _strip_modifiers(pattern: str) -> str:
    """Strip ABP modifier suffix ($third-party, $script, etc.) from a pattern.

    Returns the URL portion only. Rules with $domain= constraints (narrow
    exceptions) are flagged for special handling: we drop them because we
    can't enforce the domain restriction in a simplified parser.
    """
    if "$" in pattern:
        modifier_part = pattern.split("$", 1)[1]
        # If the rule has a $domain= modifier, we skip it — we can't safely
        # apply narrow exceptions without the parent page URL context.
        if "domain=" in modifier_part:
            return ""  # signal to drop
        return pattern.split("$", 1)[0]
    return pattern


# --- Convenience downloaders (async) ---------------------------------------

async def download_and_parse_easylist(
    client,
    url: str = "https://easylist.to/easylist/easylist.txt",
) -> FilterListParser:
    """Download and parse EasyList."""
    resp = await client.get(url)
    resp.raise_for_status()
    parser = FilterListParser()
    parser.parse_text(resp.text)
    return parser


async def download_and_parse_easyprivacy(
    client,
    url: str = "https://easylist.to/easylist/easyprivacy.txt",
) -> FilterListParser:
    """Download and parse EasyPrivacy."""
    resp = await client.get(url)
    resp.raise_for_status()
    parser = FilterListParser()
    parser.parse_text(resp.text)
    return parser
