"""Shared adult-content blocklist.

One term list, two consumers:

* the Quillon search bar and results page, which refuse a flagged query and
  show the block notice instead;
* the YouTube proxy filter, which drops matching feed items and blocks
  matching searches.

Users extend or trim the list in ``~/.quillon/youtube_blocklist.txt`` — one
term per line, ``-term`` to un-block a built-in, ``#`` for comments.
Set ``QUILLON_YOUTUBE_STRICT_FILTER=0`` to disable the whole thing.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


USER_TERMS_PATH = Path.home() / ".quillon" / "youtube_blocklist.txt"

# Adult sites and services, listed as bare words so "fansly" or "onlyfans"
# is caught even without a domain. The .com forms live in ADULT_DOMAINS.
SITE_TERMS = frozenset({
    "pornhub", "xvideos", "xhamster", "redtube", "youporn", "tube8",
    "spankbang", "eporner", "motherless", "xtubes", "xnxx", "porn720",
    "txxx", "hotmovs", "brazzers", "bangbros", "realitykings",
    "privatecontent", "onlyfans", "only fans", "fansly", "chaturbate",
    "stripchat", "myfreecams", "camwhores", "camsoda", "flirt4free",
    "xcams", "bongacams", "cam4", "livejasmin", "adultfriendfinder",
    "premium snapchat", "leaked onlyfans", "leaked nudes", "free onlyfans",
})

# Straightforwardly adult wording: the acts, the anatomy, the formats.
EXPLICIT_TERMS = frozenset({
    "porn", "porno", "pornography", "pornographic", "xxx", "nsfw", "18+",
    "sex", "sexual", "sextape", "sexting", "erotic", "erotica", "erotism",
    "hentai", "ecchi", "ahegao", "oppai", "loli", "shota", "doujin",
    "doujinshi", "rule 34", "r34", "ehentai", "nhentai", "javhd",
    "uncensored", "pussy", "dick", "cock", "penis", "vagina", "clitoris",
    "labia", "areola", "nipple", "nipples", "semen", "cumshot", "creampie",
    "blowjob", "blow job", "handjob", "hand job", "footjob", "foot job",
    "rimjob", "rim job", "titjob", "dildo", "orgasm", "orgasmic",
    "masturbate", "masturbation", "jackoff", "jack off", "bukkake",
    "gangbang", "gang bang", "threesome", "foursome", "orgy", "deepthroat",
    "deep throat", "cuckold", "cuckholding", "femdom", "dominatrix",
    "bondage", "bdsm", "milf", "shemale", "cougar", "sugar daddy",
    "sugardaddy", "sugar dating", "hookup", "hook up", "booty call",
    "sex worker", "sex workers", "call girl", "harlot", "hooker",
    "camgirl", "cam girl", "webcam sex", "webcam model", "live cam",
    "nude", "nudes", "naked", "topless", "bottomless", "undressed",
    "undress", "lingerie", "panties", "thong", "cleavage", "underboob",
    "sideboob", "upskirt", "nude pics", "free nudes", "free porn",
    "free sex", "free hentai", "sex video", "sex videos", "sex chat",
    "sex cam", "sex tape", "sex doll", "sex toy", "sex toys", "anal sex",
})

# Sexually suggestive commercial wording. Kept separate from EXPLICIT_TERMS
# so the balance is visible, and because these are the terms that decide what
# a Shorts thumbnail gets dropped over.
SUGGESTIVE_TERMS = frozenset({
    "sexy", "seduce", "seduction", "seductive", "tease", "teasing",
    "big tits", "huge tits", "large breasts", "big boobs", "boobs", "tits",
    "big ass", "huge ass", "thick thighs", "thicc", "anal", "voyeur",
    "voyeurism", "fetish", "kink", "kinky", "bikini", "micro bikini",
    "strip", "stripper", "strip club", "strip show", "lapdance", "lap dance",
    "hot chick", "body shots", "panty", "panty shot", "butt", "ass shot",
    "sex position", "sex positions", "spicy", "sultry", "provocative",
})

# Sites blocked by name, so a query like "pornhub.com" is caught even when
# the wording itself is only a brand name.
ADULT_DOMAINS = frozenset({
    "pornhub.com", "xvideos.com", "xhamster.com", "redtube.com",
    "youporn.com", "onlyfans.com", "tube8.com", "spankbang.com",
    "eporner.com", "motherless.com", "xtubes.com", "brazzers.com",
    "onlyfans.fans", "fansly.com", "chaturbate.com", "stripchat.com",
    "xnxx.com", "porn720.com", "txxx.com", "myfreecams.com",
    "camwhores.com", "flirt4free.com", "xcams.com", "bongacams.com",
    "cam4.com", "livejasmin.com", "adultfriendfinder.com", "javhd.com",
    "e-hentai.org", "nhentai.net",
})

# Words that are only adult next to a format or an act word. Matched as a
# pair with SUPPORT_TERMS below, which is what lets a few hundred entries
# cover the whole "free <x> <y>" space instead of enumerating it.
CORE_HINTS = frozenset({
    "free", "new", "leaked", "leak", "uncensored", "hd", "4k", "1080p",
    "720p", "fhd", "ultra", "raw", "amateur", "authentic", "verified",
    "premium", "paid", "trial", "premiumsnap", "snap", "dm", "telegram",
    "discord", "reddit", "twitter", "only", "fans", "subs", "subscriber",
    "unreleased", "exclusive", "private", "hidden", "secret", "full",
    "whole", "all", "compilation", "collection", "pack", "bundle",
    "download", "downloadd", "watch", "stream", "streaming", "live",
    "video", "videos", "clip", "clips", "footage", "movie", "movies",
    "scene", "scenes", "episode", "episodes", "series", "show", "pics",
    "pictures", "photo", "photos", "images", "gallery", "album", "set",
    "sets", "folder", "link", "links", "url", "chat", "chatting", "group",
    "room", "rooms", "show", "cam", "cams", "webcam", "model", "models",
    "girl", "girls", "guy", "guys", "lady", "ladies", "woman", "women",
    "man", "men", "wife", "husband", "mom", "mommy", "stepmom", "sister",
    "girlfriend", "boyfriend", "gf", "bf", "stranger", "random", "first",
    "time", "young", "old", "big", "small", "large", "huge", "perfect",
    "real", "sexy", "hot", "best", "top", "good", "nice", "cute",
})

# Act / body / format qualifiers that turn a CORE_HINT into a blocked query.
SUPPORT_TERMS = frozenset({
    "nude", "nudes", "naked", "nudity", "topless", "bottomless", "undressed",
    "undress", "sex", "sexual", "sexy", "porn", "porno", "pornography",
    "xxx", "nsfw", "erotic", "erotica", "hentai", "cam", "camgirl", "cams",
    "webcam", "blowjob", "handjob", "footjob", "rimjob", "creampie",
    "cumshot", "orgasm", "masturbation", "dildo", "anal", "bdsm", "fetish",
    "milf", "cougar", "creampie", "semen", "cum", "pussy", "dick", "cock",
    "penis", "vagina", "boobs", "tits", "nipples", "ass", "butt", "lingerie",
    "panty", "panties", "thong", "cleavage", "upskirt", "bikini", "strip",
    "stripper", "erotic", "bukkake", "gangbang", "threesome", "orgy",
    "deepthroat", "cuckold", "femdom", "dominatrix", "bondage", "shemale",
    "transsexual", "playboy", "bdsm", "fetish", "kink", "sugar", "dating",
    "hookup", "escort", "hooker", "harlot", "prostitute", "sexwork",
    "sexworker", "nudes", "leaked", "sextape", "sexting", "onlyfans",
    "fansly", "pornhub", "xvideos", "xhamster", "javhd", "hentai",
})

# A user-supplied wordlist can hold hundreds of thousands of entries. Only
# whole-word entries go in the O(1) lookup set; anything containing a space
# or punctuation goes to the phrase list, which is scanned per query, so the
# loader keeps that share small.
EXTERNAL_WORDS_PATH = Path.home() / ".quillon" / "adult_words.txt"
MAX_EXTERNAL_ENTRIES = 2_000_000
MAX_EXTERNAL_PHRASES = 50_000

# Shipped data files, generated from published blocklists:
#   adult_domains.txt — StevenBlack/hosts "porn-only" + Dan Pollock's list
#   adult_labels.txt  — the distinctive host label of each of those domains
# Sources are recorded in data/SOURCES.md.
DATA_DIR = Path(__file__).resolve().parent / "data"

_MAX_MATCH_CHARS = 16000

# A term made only of these characters can be matched by looking it up in the
# token set, which is far cheaper than running a regex per term.
_WORD_ONLY_RE = re.compile(r"[a-z0-9]+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Cyrillic and Greek letters that render as Latin ones. NFKD handles
# diacritics and fullwidth forms but leaves these, which is the other half
# of the homoglyph bypass.
_CONFUSABLES = str.maketrans({
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p",
    "\u0441": "c", "\u0443": "y", "\u0445": "x", "\u0456": "i",
    "\u0458": "j", "\u03bf": "o", "\u03b1": "a", "\u03b5": "e",
    "\u03c1": "p", "\u03c5": "u", "\u03c7": "x",
})

_lock = threading.Lock()
_pattern_cache: dict[str, re.Pattern[str]] = {}
_cached_terms: Optional[frozenset[str]] = None
_cached_sorted: tuple[str, ...] = ()
_cached_words: tuple[str, ...] = ()
_cached_phrases: tuple[str, ...] = ()
_cached_word_set: frozenset[str] = frozenset()
_cached_phrase_index: dict[str, tuple[str, ...]] = {}
_cached_signature: Optional[tuple] = None
_external_words: frozenset[str] = frozenset()
_external_phrases: tuple[str, ...] = ()
_external_signature: Optional[tuple] = None
_adult_domains: frozenset[str] = frozenset()
_adult_labels: frozenset[str] = frozenset()
_data_loaded = False
_load_stats: dict[str, int] = {}


def enabled() -> bool:
    return os.environ.get("QUILLON_YOUTUBE_STRICT_FILTER", "1") != "0"


def _read_data(name: str) -> frozenset[str]:
    try:
        with open(DATA_DIR / name, encoding="utf-8", errors="replace") as handle:
            # map/filter keep this at C speed across ~140k lines. The
            # generator form stripped every line twice, once to test and
            # once to keep.
            return frozenset(filter(None, map(str.strip, handle)))
    except OSError:
        return frozenset()


def _ensure_data() -> None:
    """Load the shipped domain/label sets once, on first use."""
    global _adult_domains, _adult_labels, _data_loaded
    if _data_loaded:
        return
    with _lock:
        if _data_loaded:
            return
        _adult_domains = _read_data("adult_domains.txt")
        _adult_labels = _read_data("adult_labels.txt")
        _data_loaded = True
        _load_stats.update({
            "shipped_domains": len(_adult_domains),
            "shipped_labels": len(_adult_labels),
        })


def load_stats() -> dict[str, int]:
    """Entry counts, so callers can report what is actually loaded."""
    _ensure_data()
    terms()
    return dict(_load_stats)


def _compile(term: str) -> re.Pattern[str]:
    pattern = _pattern_cache.get(term)
    if pattern is None:
        escaped = re.escape(term)
        if term.isascii():
            pattern = re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)
        else:
            pattern = re.compile(escaped)
        _pattern_cache[term] = pattern
    return pattern


_SIGNATURE_TTL = 2.0
_signature_cache: tuple = ((0.0, 0), 0.0)


def _signature() -> tuple:
    """Cheap change token for the user file, rate limited.

    This runs once per matched item when filtering a feed, and an
    os.stat() per item was a measurable share of the pass.
    """
    global _signature_cache
    value, checked_at = _signature_cache
    now = time.monotonic()
    if now - checked_at < _SIGNATURE_TTL:
        return value
    try:
        stat = os.stat(USER_TERMS_PATH)
        value = (stat.st_mtime, stat.st_size)
    except OSError:
        value = (0.0, 0)
    _signature_cache = (value, now)
    return value


def _user_adjustments() -> tuple[set[str], set[str]]:
    add: set[str] = set()
    remove: set[str] = set()
    try:
        text = USER_TERMS_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return add, remove
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith("-"):
            name = entry[1:].strip().lower()
            if name:
                remove.add(name)
        else:
            add.add(entry.lower())
    return add, remove


def _rebuild(effective: frozenset[str]) -> None:
    """Split the term set into a fast word lookup and a slower phrase scan."""
    global _cached_terms, _cached_sorted, _cached_signature
    global _cached_words, _cached_phrases, _cached_word_set, _cached_phrase_index
    _cached_terms = effective
    word_set = frozenset(t for t in effective if _WORD_ONLY_RE.fullmatch(t))
    _cached_word_set = word_set
    words = list(word_set)
    phrases = [t for t in effective if t not in word_set]
    # Longest first so the most specific term is the one reported.
    _cached_words = tuple(sorted(words, key=lambda t: (-len(t), t)))
    _cached_phrases = tuple(sorted(phrases, key=lambda t: (-len(t), t)))
    # Index phrases by their first token so a query only probes the handful
    # of phrases that could possibly start with a word it actually contains.
    index: dict[str, list[str]] = {}
    for phrase in _cached_phrases:
        head = _TOKEN_RE.search(phrase)
        if head:
            index.setdefault(head.group(0), []).append(phrase)
    _cached_phrase_index = {k: tuple(v) for k, v in index.items()}
    _cached_sorted = tuple(sorted(effective, key=lambda t: (-len(t), t)))
    _cached_signature = _signature()


def terms() -> frozenset[str]:
    """Effective term set: built-in lists adjusted by the user file."""
    with _lock:
        if _cached_terms is not None and _cached_signature == _signature():
            return _cached_terms
        add, remove = _user_adjustments()
        effective = frozenset(
            t for t in (SITE_TERMS | EXPLICIT_TERMS | SUGGESTIVE_TERMS | add) - remove if t
        )
        _rebuild(effective)
        return _cached_terms


def reset_cache() -> None:
    global _cached_terms, _cached_sorted, _cached_signature
    global _cached_words, _cached_phrases, _cached_word_set, _cached_phrase_index
    with _lock:
        _cached_terms = None
        _cached_sorted = ()
        _cached_words = ()
        _cached_phrases = ()
        _cached_word_set = frozenset()
        _cached_phrase_index = {}
        _cached_signature = None


def _fold(text: str) -> str:
    """Normalise text before matching.

    The tokeniser is [a-z0-9]+, so every non-ASCII character SPLITS a
    token. "pórn" became {"p", "rn"} and matched nothing, and the same
    held for fullwidth "ｐｏｒｎ" and for Cyrillic lookalikes. NFKD folds
    diacritics and fullwidth forms to ASCII, which closes that whole class
    in one place. Confusables that NFKD cannot fold (Cyrillic о for o)
    are mapped explicitly.
    """
    import unicodedata

    # The overwhelmingly common case is plain ASCII, where NFKD, the
    # combining-mark pass and the confusables table are all no-ops. This
    # ran once per matched feed item and was a third of the filter's cost.
    if text.isascii():
        return text.lower()

    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.translate(_CONFUSABLES).lower()


def matched_term(text: Optional[str]) -> Optional[str]:
    """First blocklist term found in text, most specific first.

    Single-word terms are resolved with one set lookup against the tokenised
    text, which is exact for the same reason the regex was (a term only
    matches between non-alphanumeric characters). Multi-word phrases and
    non-Latin terms fall back to a substring probe, and only for phrases
    that actually occur does the word-boundary regex confirm them.
    """
    if not text:
        return None
    terms_now = terms()
    if not terms_now:
        return None
    haystack = _fold(text)[:_MAX_MATCH_CHARS]
    words = _cached_words or tuple(
        sorted((t for t in terms_now if _WORD_ONLY_RE.fullmatch(t)), key=lambda t: (-len(t), t))
    )
    phrases = _cached_phrases or tuple(
        sorted((t for t in terms_now if not _WORD_ONLY_RE.fullmatch(t)), key=lambda t: (-len(t), t))
    )
    _ensure_data()
    # Walk the query's own tokens, not the blocklist: membership in the
    # shipped sets is O(1) each, so cost follows the query length rather
    # than the size of the list.
    present = set(_TOKEN_RE.findall(haystack))
    best_word = None
    if present:
        word_set = _cached_word_set or frozenset(words)
        for token in present:
            if token in _adult_labels or token in word_set:
                if best_word is None or len(token) > len(best_word):
                    best_word = token
    # Candidates arrive in set-iteration order, so rank them here rather
    # than taking the first hit: the reported term must be the longest
    # match regardless of how the index was built.
    index = _cached_phrase_index or {}
    candidates: list[str] = []
    if index and present:
        for token in present:
            candidates.extend(index.get(token, ()))
    elif not index:
        candidates = list(phrases)
    best_phrase = None
    if candidates:
        candidates.sort(key=lambda p: (-len(p), p))
        for phrase in candidates:
            if phrase not in haystack:
                continue
            if not phrase.isascii() or _compile(phrase).search(haystack):
                best_phrase = phrase
                break
    if best_word and best_phrase:
        return best_phrase if len(best_phrase) > len(best_word) else best_word
    return best_phrase or best_word


def _host_of(text: str) -> str:
    candidate = text.strip()
    if not candidate:
        return ""
    if "://" not in candidate:
        candidate = "//" + candidate.lstrip("/")
    try:
        host = (urlparse(candidate).hostname or "").lower().rstrip(".")
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


_adult_domain_re: Optional[re.Pattern[str]] = None


def _adult_domain_pattern() -> re.Pattern[str]:
    """One pattern covering every brand domain, matched on a label boundary.

    Keeps ``notpornhub.com`` and ``pornhub.example.org`` out of the net
    while still catching "best pornhub videos".

    This used to be 35 separate compiled patterns scanned one after
    another, so every string the filter touched cost 30-35 regex scans.
    A single alternation does the same work in one pass.
    """
    global _adult_domain_re
    if _adult_domain_re is None:
        # Longest first so an alternative that is a prefix of another
        # cannot shadow it before the trailing lookahead gets its say.
        body = "|".join(
            re.escape(d) for d in sorted(ADULT_DOMAINS, key=len, reverse=True)
        )
        _adult_domain_re = re.compile(
            rf"(?<![a-z0-9-])(?:{body})(?![a-z0-9-])", re.IGNORECASE
        )
    return _adult_domain_re


def matched_domain(text: Optional[str]) -> Optional[str]:
    """Blocklist domain named in text, as typed or embedded in a URL.

    The shipped 89k-domain set is consulted by exact host match, so an
    unrelated host whose name merely contains a listed domain is safe.
    """
    if not text:
        return None
    candidate = text.strip()
    if not candidate:
        return None
    if "." not in candidate and ":" not in candidate and "/" not in candidate:
        # No host shape at all: only the curated brand patterns can match.
        hit = _adult_domain_pattern().search(candidate)
        return hit.group(0).lower() if hit else None
    _ensure_data()
    host = _host_of(candidate)
    if host:
        probe = host
        while probe:
            if probe in _adult_domains or probe in ADULT_DOMAINS:
                return probe
            if "." not in probe:
                break
            probe = probe.split(".", 1)[1]
    hit = _adult_domain_pattern().search(candidate)
    return hit.group(0).lower() if hit else None


def _rule_match(present: set[str]) -> Optional[str]:
    """Catch the combinatorial space without enumerating it.

    A qualifier plus an act word ("free <x> videos", "leaked <x> pics") is
    blocked even when the exact phrase is not in the list, which is what
    lets a few hundred entries stand in for hundreds of thousands of
    generated query strings.
    """
    if not (present & SUPPORT_TERMS):
        return None
    # Set intersection costs O(len(present)) rather than a scan of all
    # 150-odd hints, and both orders are arbitrary anyway.
    return next(iter(present & CORE_HINTS), None)


def classify(text: Optional[str]) -> Optional[str]:
    """What in this text is blocked: a known domain, else a term."""
    if not text:
        return None
    domain = matched_domain(text)
    if domain:
        return domain
    term = matched_term(text)
    if term:
        return term
    if enabled():
        return _rule_match(set(_TOKEN_RE.findall(_fold(text)[:_MAX_MATCH_CHARS])))
    return None
