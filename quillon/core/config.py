"""Quillon Configuration and Constants."""

from pathlib import Path
from dataclasses import dataclass, field
from typing import FrozenSet, Optional


@dataclass(frozen=True)
class Paths:
    """Application paths."""
    QUILLON_DIR: Path = Path.home() / ".quillon"
    COOKIE_DB: Path = QUILLON_DIR / "cookies.enc"
    PASSWORD_DB: Path = QUILLON_DIR / "passwords.enc"
    VAULT_DB: Path = QUILLON_DIR / "vault.enc"
    VAULT_KEY: Path = QUILLON_DIR / "vault.key"
    BLOCKLIST_DIR: Path = QUILLON_DIR / "blocklists"
    KEY_FILE: Path = QUILLON_DIR / ".hw-key"
    LOG_FILE: Path = QUILLON_DIR / "quillon.log"
    HISTORY_DB: Path = QUILLON_DIR / "quillon.db"
    HISTORY_CAP: int = 10000

    def ensure_dirs(self) -> None:
        """Create required directories."""
        self.QUILLON_DIR.mkdir(mode=0o700, exist_ok=True)
        self.BLOCKLIST_DIR.mkdir(mode=0o700, exist_ok=True)
        self.QUILLON_DIR.chmod(0o700)
        self.BLOCKLIST_DIR.chmod(0o700)


@dataclass(frozen=True)
class AppConfig:
    """Application configuration."""
    WINDOW_TITLE: str = "Quillon"
    WINDOW_WIDTH: int = 1440
    WINDOW_HEIGHT: int = 960
    # SEARX_URL removed — we use local search providers
    TAB_HEIGHT: int = 34
    TAB_MIN_WIDTH: int = 120
    TAB_MAX_WIDTH: int = 220
    NAV_HEIGHT: int = 48
    ADDR_MIN_WIDTH: int = 800
    # Fast mode: single engine (Startpage works reliably)
    SEARCH_DEFAULT_ENGINES: tuple[str, ...] = ("startpage",)
    SEARCH_RESULTS_PER_PAGE: int = 10
    SEARCH_TIMEOUT: float = 8.0
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
    # Resource types to intercept for adblocking (Qt6 QWebEngineUrlRequestInfo.ResourceType)
    # 0=MainFrame, 1=SubFrame, 2=Stylesheet, 3=Script, 4=Image, 5=FontResource,
    # 6=SubResource, 7=Object, 8=Media, 9=Worker, 10=SharedWorker, 11=Prefetch,
    # 12=Favicon, 13=Xhr, 14=Ping, 15=ServiceWorker, 17=PluginResource,
    # 21=Json, 254=WebSocket, 255=Unknown
    BLOCKED_RESOURCE_TYPES: FrozenSet[int] = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 21, 254, 255})
    
    # HTTP Client hardening
    USER_AGENT: str = (
        "Mozilla/5.0 (X11; Linux x8640) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    MAX_REDIRECTS: int = 5
    VERIFY_SSL: bool = True

    # Public DNS-over-HTTPS providers (no account needed)
    # Set to one of: "cloudflare-security", "cloudflare-family", "quad9", "adguard", "nextdns", "custom"
    DOH_PROVIDER: str = "adguard"
    # Custom DoH URL (used when DOH_PROVIDER = "custom")
    CUSTOM_DOH_URL: str = ""
    # Enable DoH for malware, cryptojacking, tracking protection at DNS level
    DOH_ENABLED: bool = True


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
        enabled=False,  # DDG lite returns search form (blocked/changed)
        timeout=8.0,
        weight=1.0,
    ),
    "brave": EngineConfig(
        engine_type="brave",
        enabled=False,  # Rate limited (429) + brotli issues
        timeout=8.0,
        weight=1.1,
    ),
    "startpage": EngineConfig(
        engine_type="startpage",
        enabled=True,
        timeout=8.0,
        weight=1.0,
    ),
    "mojeek": EngineConfig(
        engine_type="mojeek",
        enabled=False,  # Returns 403
        timeout=8.0,
        weight=0.9,
    ),
}

def get_engine_configs() -> dict:
    """Resolve ENGINE_CONFIGS with EngineType enum keys at runtime."""
    from quillon.core.search.models import EngineType
    return {
        EngineType(key): config
        for key, config in ENGINE_CONFIGS_RAW.items()
    }

# Global instances
PATHS = Paths()
APP_CONFIG = AppConfig()
SECURITY_CONFIG = SecurityConfig()
CRYPTO_CONFIG = CryptoConfig()

# DNS blocklist sources for DNS-level blocking fallback
_DNS_BLOCKLIST_SOURCES = [
    "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
    "https://mirror1.malwaredomains.com/files/justdomains",
    "https://s3.amazonaws.com/lists.disconnect.me/simple_malvertising.txt",
]
_DNS_BLOCKLIST_CACHE = PATHS.BLOCKLIST_DIR / "dns_blocklist.txt"
_DNS_REFRESH_INTERVAL = 24 * 3600  # 24 hours