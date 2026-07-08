"""URL blocking logic."""

from typing import FrozenSet
from PyQt6.QtCore import QUrl

from .config import SECURITY_CONFIG, PATHS


class URLBlocker:
    """Checks URLs against blocklists and whitelists."""

    def __init__(self) -> None:
        self._blocked: set[str] = set()
        self._whitelist: FrozenSet[str] = SECURITY_CONFIG.WHITELIST_DOMAINS
        self._load_blocklist()

    def _load_blocklist(self) -> None:
        """Load blocklist from combined.txt."""
        blocklist_file = PATHS.BLOCKLIST_DIR / "combined.txt"
        if not blocklist_file.exists():
            return
        for line in blocklist_file.open():
            domain = line.strip().lower()
            if domain and not domain.startswith("#"):
                self._blocked.add(domain)

    def is_blocked(self, url_str: str) -> bool:
        """Check if URL should be blocked."""
        try:
            host = QUrl(url_str).host().lower()
        except Exception:
            return False

        if not host:
            return False

        parts = host.split(".")
        for i in range(len(parts) - 1):
            domain = ".".join(parts[i:])
            if domain in self._whitelist:
                return False

        for i in range(len(parts) - 1):
            domain = ".".join(parts[i:])
            if domain in self._blocked:
                return True

        return False

    @property
    def blocked_count(self) -> int:
        """Number of blocked domains."""
        return len(self._blocked)

    @property
    def whitelist(self) -> FrozenSet[str]:
        """Whitelisted domains."""
        return self._whitelist