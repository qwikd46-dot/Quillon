"""Standalone Chromium runtime policy and command construction."""

from __future__ import annotations

import json
import os
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlparse


POLICY_VERSION = 1
POLICY_MODES = ("standard", "balanced", "strict")
DEFAULT_DISABLE_FEATURES = (
    "AutofillServerCommunication",
    "CertificateTransparencyComponentUpdater",
    "InterestFeedContentSuggestions",
    "MediaRouter",
    "OptimizationHints",
    "CalculateNativeWinOcclusion",
)


@dataclass(frozen=True)
class PrivacyPolicy:
    mode: str = "balanced"
    disable_sync: bool = True
    disable_component_update: bool = True
    disable_background_networking: bool = True
    disable_domain_reliability: bool = True
    disable_default_apps: bool = True
    disable_metrics: bool = True
    disable_breakpad: bool = True
    safe_browsing: bool = True
    disable_features: tuple[str, ...] = DEFAULT_DISABLE_FEATURES
    password_store: Optional[str] = None
    extra_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in POLICY_MODES:
            raise ValueError(f"unsupported privacy mode: {self.mode}")

    def flags(self) -> tuple[str, ...]:
        values: list[str] = []
        if self.disable_sync:
            values.append("--disable-sync")
        if self.disable_component_update:
            values.append("--disable-component-update")
        if self.disable_background_networking:
            values.append("--disable-background-networking")
        if self.disable_domain_reliability:
            values.append("--disable-domain-reliability")
        if self.disable_default_apps:
            values.append("--disable-default-apps")
        if self.disable_metrics:
            values.extend(("--metrics-recording-only", "--no-pings"))
        if self.disable_breakpad:
            values.append("--disable-breakpad")
        if not self.safe_browsing:
            values.append("--disable-client-side-phishing-detection")
        if self.disable_features:
            values.append("--disable-features=" + ",".join(self.disable_features))
        if self.password_store:
            values.append(f"--password-store={self.password_store}")
        values.extend(self.extra_flags)
        return tuple(values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": POLICY_VERSION,
            "mode": self.mode,
            "disable_sync": self.disable_sync,
            "disable_component_update": self.disable_component_update,
            "disable_background_networking": self.disable_background_networking,
            "disable_domain_reliability": self.disable_domain_reliability,
            "disable_default_apps": self.disable_default_apps,
            "disable_metrics": self.disable_metrics,
            "disable_breakpad": self.disable_breakpad,
            "safe_browsing": self.safe_browsing,
            "disable_features": list(self.disable_features),
            "password_store": self.password_store,
            "extra_flags": list(self.extra_flags),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PrivacyPolicy":
        if int(data.get("version", POLICY_VERSION)) != POLICY_VERSION:
            raise ValueError("unsupported privacy policy version")
        features = data.get("disable_features", DEFAULT_DISABLE_FEATURES)
        extra = data.get("extra_flags", ())
        if isinstance(features, str):
            features = tuple(part for part in features.split(",") if part)
        if isinstance(extra, str):
            extra = tuple(shlex.split(extra))
        return cls(
            mode=str(data.get("mode", "balanced")),
            disable_sync=bool(data.get("disable_sync", True)),
            disable_component_update=bool(data.get("disable_component_update", True)),
            disable_background_networking=bool(data.get("disable_background_networking", True)),
            disable_domain_reliability=bool(data.get("disable_domain_reliability", True)),
            disable_default_apps=bool(data.get("disable_default_apps", True)),
            disable_metrics=bool(data.get("disable_metrics", True)),
            disable_breakpad=bool(data.get("disable_breakpad", True)),
            safe_browsing=bool(data.get("safe_browsing", True)),
            disable_features=tuple(str(item) for item in features),
            password_store=data.get("password_store"),
            extra_flags=tuple(str(item) for item in extra),
        )


@dataclass(frozen=True)
class ChromiumLaunchSpec:
    binary: Path
    profile_dir: Path
    initial_url: str
    policy: PrivacyPolicy = field(default_factory=PrivacyPolicy)
    proxy_url: Optional[str] = None
    spki_fingerprint: Optional[str] = None
    extra_args: tuple[str, ...] = ()

    def command(self) -> list[str]:
        parsed = urlparse(self.initial_url)
        if parsed.scheme not in {"http", "https", "about", "data"}:
            raise ValueError(f"unsupported initial URL: {self.initial_url}")
        ensure_profile_dir(self.profile_dir)
        command = [
            str(self.binary),
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            *self.policy.flags(),
        ]
        if self.proxy_url:
            command.append(f"--proxy-server={self.proxy_url}")
        if self.spki_fingerprint:
            command.append(
                f"--ignore-certificate-errors-spki-list={self.spki_fingerprint}"
            )
        command.extend(self.extra_args)
        command.append(self.initial_url)
        return command


def ensure_profile_dir(path: Path) -> Path:
    path = path.expanduser()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def default_profile_dir() -> Path:
    configured = os.environ.get("QUILLON_CHROMIUM_PROFILE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "quillon" / "chromium-profile"


def find_chromium(
    explicit: Optional[str] = None,
    lookup: Any = shutil.which,
) -> Path:
    candidates: list[str] = []
    if explicit:
        candidates.append(os.path.expanduser(explicit))
    configured = os.environ.get("QUILLON_CHROMIUM_BIN")
    if configured:
        candidates.append(os.path.expanduser(configured))
    candidates.extend(("quillon-chromium", "chromium", "chromium-browser", "google-chrome", "google-chrome-stable"))
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return path
        resolved = lookup(candidate)
        if resolved:
            path = Path(resolved)
            if path.is_file() and os.access(path, os.X_OK):
                return path
    raise FileNotFoundError("no Chromium binary was found")


def policy_for_mode(mode: str) -> PrivacyPolicy:
    normalized = mode.strip().lower()
    if normalized == "standard":
        return PrivacyPolicy(
            mode=normalized,
            disable_background_networking=False,
            disable_domain_reliability=False,
            disable_metrics=False,
        )
    if normalized == "strict":
        return PrivacyPolicy(
            mode=normalized,
            disable_features=DEFAULT_DISABLE_FEATURES + (
                "AutofillEnableAccountWalletStorage",
                "GlobalMediaControls",
                "InterestFeedContentSuggestions",
            ),
            extra_flags=("--disable-search-engine-choice-screen",),
        )
    return PrivacyPolicy(mode="balanced")


def load_policy(path: Path) -> PrivacyPolicy:
    return PrivacyPolicy.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_policy(path: Path, policy: PrivacyPolicy) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(policy.to_dict(), indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
