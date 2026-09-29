"""Quillon search shortcuts — keyword-style direct URL navigation.

Like browser address-bar shortcuts: typing a keyword (e.g. "youtube")
returns its target URL as the top hit, before any BM25 results.

The shortcuts list is a user-editable JSON file at ~/.quillon/shortcuts.json.
First run seeds a sensible default set; the user can add/remove via
the CLI (quillon.core.search.shortcuts_cli) or by editing the file.

This is our OWN list — no third-party provider is queried, no Mozilla
or Google keyword lists are pulled. Just a small JSON file the user
controls.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Path: ~/.quillon/shortcuts.json (alongside cookies.enc, quillon.db, etc.)
DEFAULT_SHORTCUTS_PATH = Path.home() / ".quillon" / "shortcuts.json"

# Sensible defaults — privacy-respecting hosts where possible, plus the
# user's own local services. The user can edit ~/.quillon/shortcuts.json
# freely; this is just the seed.
DEFAULT_SHORTCUTS: dict[str, str] = {
    # --- Code ---
    "github":        "https://github.com",
    "gh":            "https://github.com",
    "gitlab":        "https://gitlab.com",
    # --- Linux / OS ---
    "arch":          "https://wiki.archlinux.org",
    "arch linux":    "https://wiki.archlinux.org",
    "archwiki":      "https://wiki.archlinux.org",
    "archlinux":     "https://www.archlinux.org",
    "aur":           "https://aur.archlinux.org",
    "gnome":         "https://www.gnome.org",
    "kde":           "https://kde.org",
    "nixos":         "https://nixos.org",
    # --- Reference ---
    "wikipedia":     "https://www.wikipedia.org",
    "wiki":          "https://www.wikipedia.org",
    # --- Search engines ---
    "startpage":     "https://www.startpage.com",
    "ddg":           "https://duckduckgo.com",
    "duckduckgo":    "https://duckduckgo.com",
    # --- Social / chat ---
    "youtube":       "https://www.youtube.com",
    "yt":            "https://www.youtube.com",
    "facebook":      "https://www.facebook.com",
    "fb":            "https://www.facebook.com",
    "instagram":     "https://www.instagram.com",
    "ig":            "https://www.instagram.com",
    "twitter":       "https://x.com",
    "x":             "https://x.com",
    "tiktok":        "https://www.tiktok.com",
    "reddit":        "https://www.reddit.com",
    "linkedin":      "https://www.linkedin.com",
    "discord":       "https://discord.com",
    "whatsapp":      "https://web.whatsapp.com",
    "telegram":      "https://web.telegram.org",
    # --- Streaming / shopping ---
    "netflix":       "https://www.netflix.com",
    "spotify":       "https://open.spotify.com",
    "twitch":        "https://www.twitch.tv",
    "amazon":        "https://www.amazon.com",
    # --- Google ---
    "gmail":         "https://mail.google.com",
    "maps":          "https://maps.google.com",
    # --- Local services ---
    "invidious":     "http://127.0.0.1:8080",
    "piped":         "http://127.0.0.1:8080",
    # --- Misc ---
    "hyprland":      "https://hypr.land",
    "bfsb":          "https://github.com/JaKooLit/Arch-Hyprland",
}

# Only keys with safe characters. Prevents malformed input from breaking lookup.
_VALID_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9 ._+-]*$", re.IGNORECASE)


@dataclass(frozen=True)
class ShortcutHit:
    """One resolved shortcut."""
    key: str
    url: str
    query: str  # original query, for snippet


class ShortcutManager:
    """Loads + queries the shortcuts JSON file."""

    def __init__(self, path: Path = DEFAULT_SHORTCUTS_PATH):
        self.path = Path(path)

    # --- file I/O ---------------------------------------------------------

    def load(self) -> dict[str, str]:
        """Load shortcuts from disk; seed defaults if file missing or invalid."""
        if not self.path.exists():
            self.save(DEFAULT_SHORTCUTS)
            return dict(DEFAULT_SHORTCUTS)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("shortcuts file root must be a JSON object")
            return {str(k): str(v) for k, v in data.items()}
        except Exception as e:
            print(f"[shortcuts] failed to parse {self.path}: {e}; using defaults")
            return dict(DEFAULT_SHORTCUTS)

    def save(self, shortcuts: dict[str, str]) -> None:
        """Write shortcuts to disk. Creates parent dirs if needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Sort by key for stable, readable output.
        ordered = dict(sorted(shortcuts.items()))
        self.path.write_text(
            json.dumps(ordered, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def reset_to_defaults(self) -> None:
        self.save(DEFAULT_SHORTCUTS)

    # --- mutation ---------------------------------------------------------

    def add(self, key: str, url: str) -> None:
        if not _VALID_KEY_RE.match(key):
            raise ValueError(
                f"invalid shortcut key {key!r}: only [a-z0-9 ._+-] allowed, "
                "must start with [a-z0-9]"
            )
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"invalid shortcut url {url!r}: must start with http:// or https://")
        shortcuts = self.load()
        shortcuts[key.lower()] = url
        self.save(shortcuts)

    def remove(self, key: str) -> bool:
        shortcuts = self.load()
        if key.lower() in shortcuts:
            del shortcuts[key.lower()]
            self.save(shortcuts)
            return True
        return False

    # --- matching ---------------------------------------------------------

    def lookup(self, query: str) -> Optional[ShortcutHit]:
        """Return a shortcut match for the query, or None.

        Match rules (in order):
          1. exact match:  "youtube"            -> youtube URL
          2. prefix match: "youtube cats video" -> youtube URL
          3. no match.
        """
        q = query.strip().lower()
        if not q:
            return None
        shortcuts = self.load()
        if q in shortcuts:
            return ShortcutHit(key=q, url=shortcuts[q], query=q)
        # Prefix: first whitespace-separated token
        first = q.split(None, 1)[0]
        if first in shortcuts:
            return ShortcutHit(key=first, url=shortcuts[first], query=q)
        return None
