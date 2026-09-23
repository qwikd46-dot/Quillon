"""Adblock runtime state — shared between the app process and the
mitmdump addon process.

The toggle writes a one-byte state file; the addon re-reads it per
request (mtime-cached) so flipping the switch takes effect on the next
request without restarting mitmdump or the browser. Missing file means
enabled (fail-safe default).
"""

from __future__ import annotations

from pathlib import Path

STATE_FILE = Path.home() / ".bfsb" / "adblock_state"


def is_adblock_enabled() -> bool:
    try:
        return STATE_FILE.read_text().strip() != "0"
    except Exception:
        return True


def set_adblock_enabled(enabled: bool) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text("1" if enabled else "0")
    except Exception:
        pass
