"""BFSB Configuration and Constants."""

from pathlib import Path
from dataclasses import dataclass, field
from typing import FrozenSet, Optional


@dataclass(frozen=True)
class Paths:
    """Application paths."""
    BFSB_DIR: Path = Path.home() / ".bfsb"
    COOKIE_DB: Path = BFSB_DIR / "cookies.enc"
    BLOCKLIST_DIR: Path = BFSB_DIR / "blocklists"
    KEY_FILE: Path = BFSB_DIR / ".hw-key"
    LOG_FILE: Path = BFSB_DIR / "bfsb.log"

    def ensure_dirs(self) -> None:
        """Create required directories."""
        self.BFSB_DIR.mkdir(exist_ok=True)
        self.BLOCKLIST_DIR.mkdir(exist_ok=True)


@dataclass(frozen=True)
class AppConfig:
    """Application configuration."""
    WINDOW_TITLE: str = "BFSB"
    WINDOW_WIDTH: int = 1440
    WINDOW_HEIGHT: int = 960
    # SEARX_URL removed — we use local search providers
    TAB_HEIGHT: int = 34
    TAB_MIN_WIDTH: int = 120
    TAB_MAX_WIDTH: int = 220
    NAV_HEIGHT: int = 48
    ADDR_MIN_WIDTH: int = 800
    # Fast mode: single engine (DuckDuckGo lite) for speed
    SEARCH_DEFAULT_ENGINES: tuple[str, ...] = ("duckduckgo",)
    SEARCH_RESULTS_PER_PAGE: int = 10
    SEARCH_TIMEOUT: float = 6.0  # Reduced from 10s
    SEARCH_MAX_CONCURRENT: int = 1  # Single engine = no concurrency needed
    # Cache for instant repeat queries
    SEARCH_CACHE_SIZE: int = 100
    SEARCH_CACHE_TTL: int = 300  # 5 minutes


@dataclass(frozen=True)
class EngineConfig:
    """Per-engine configuration."""
    engine_type: str
    enabled: bool = True
    timeout: float = 10.0
    weight: float = 1.0  # Ranking weight for result merging
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SecurityConfig:
    """Security-related configuration."""
    BAD_EXTENSIONS: FrozenSet[str] = frozenset({
        ".exe", ".msi", ".bat", ".cmd", ".pif", ".scr", ".gadget",
        ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msc",
        ".reg", ".rgs", ".inf", ".ins", ".isp",
        ".sys", ".drv", ".cpl", ".ocx", ".dll",
        ".hta", ".msp", ".mst", ".msu",
        ".webloc", ".inetloc",
        ".appimage", ".run", ".deb", ".rpm",
        ".jar", ".class",
        ".py", ".pyc", ".pyw",
        ".sct", ".shb", ".shs", ".lnk", ".scf",
        ".vhd", ".vmdk", ".img", ".iso", ".bin",
    })
    # Allow all domains by default; only block known malicious ones via blocklist
    WHITELIST_DOMAINS: FrozenSet[str] = frozenset({
        "localhost", "127.0.0.1",
    })
    BLOCKED_RESOURCE_TYPES: FrozenSet[int] = frozenset({0, 1, 2, 5, 6, 11})
    
    # HTTP Client hardening
    USER_AGENT: str = (
        "Mozilla/5.0 (X11; Linux x8640) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    MAX_REDIRECTS: int = 5
    VERIFY_SSL: bool = True


@dataclass(frozen=True)
class CryptoConfig:
    """Cryptographic configuration."""
    PBKDF2_ITERATIONS: int = 100_000
    KEY_LENGTH: int = 32
    SALT_LENGTH: int = 16
    KEY_FILE_PERMS: int = 0o600


# Engine configurations — defined as string keys to avoid circular import
# The actual EngineType enum values are resolved at runtime in providers.py
ENGINE_CONFIGS_RAW: dict[str, EngineConfig] = {
    "duckduckgo": EngineConfig(
        engine_type="duckduckgo",
        enabled=True,
        timeout=8.0,
        weight=1.0,
    ),
    "brave": EngineConfig(
        engine_type="brave",
        enabled=True,
        timeout=8.0,
        weight=1.1,
    ),
    "startpage": EngineConfig(
        engine_type="startpage",
        enabled=True,
        timeout=10.0,
        weight=1.0,
    ),
    "mojeek": EngineConfig(
        engine_type="mojeek",
        enabled=True,
        timeout=8.0,
        weight=0.9,
    ),
}

def get_engine_configs() -> dict:
    """Resolve ENGINE_CONFIGS with EngineType enum keys at runtime."""
    from bfsb.core.search.models import EngineType
    return {
        EngineType(key): config
        for key, config in ENGINE_CONFIGS_RAW.items()
    }

# Global instances
PATHS = Paths()
APP_CONFIG = AppConfig()
SECURITY_CONFIG = SecurityConfig()
CRYPTO_CONFIG = CryptoConfig()