"""BFSB proxy bootstrap - starts mitmdump BEFORE Qt initializes so Chromium
can be routed through it via QTWEBENGINE_CHROMIUM_FLAGS.

Network-level ad blocking architecture: the mitmproxy addon
(bfsb/core/proxy_addon.py) rewrites /youtubei/v1/* JSON responses and strips
adPlacements/adSlots/playerAds BEFORE the page ever sees them - undetectable
by page-side anti-adblock checks (no JS hooks involved).

Fail-safe: if the proxy cannot start, ensure_proxy_running() returns None
and the browser runs WITHOUT proxy flags - degraded ad blocking, zero breakage.

Interception is scoped to YouTube-family hosts only (--allow-hosts), so all
other traffic tunnels through untouched.
"""

from __future__ import annotations

import atexit
import base64
import shutil
import socket
import subprocess
import time
from pathlib import Path

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8228

# Only YouTube-family hosts are intercepted - everything else is untouched.
# CRITICAL: mitmproxy matches these patterns against "host:port" strings
# (e.g. "www.youtube.com:443" - see mitmproxy/addons/next_layer.py), so the
# regex MUST accept an optional trailing ":port". A "$"-anchored regex like
# r"youtube\.com$" never matches and mitmproxy tunnels EVERYTHING untouched
# (the addon would never see a single request).
ALLOW_HOSTS_REGEX = (
    r"(^|\.)(youtube\.com|youtube-nocookie\.com|googlevideo\.com|ytimg\.com)(:|$)"
)

_process: subprocess.Popen | None = None


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except Exception:
        return False


def _mitmdump_pids_on_port(port: int) -> list[int]:
    """Find existing mitmdump process PIDs listening on the port (stale runs)."""
    pids = []
    try:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                cmdline = (proc / "cmdline").read_bytes().decode(
                    "utf-8", errors="ignore"
                )
                if "mitmdump" in cmdline and str(port) in cmdline:
                    pids.append(int(proc.name))
            except Exception:
                continue
    except Exception:
        pass
    return pids


def _compute_spki_hash(ca_path: Path) -> str | None:
    """Compute the Chromium SPKI hash: base64 of SHA-256(SPKI DER).

    CRITICAL: --ignore-certificate-errors-spki-list expects Base64-encoded
    SHA-256 DIGESTS of the SubjectPublicKeyInfo (32-byte hashes, 44 base64
    chars) - NOT base64 of the raw DER (~390 chars). The wrong format never
    matches and Chromium rejects every mitmproxy cert -> "no internet" on
    every intercepted HTTPS page. A previous bug (allow-hosts regex never
    matching) hid this because the proxy never intercepted anything.
    """
    try:
        pem = subprocess.run(
            ["openssl", "x509", "-in", str(ca_path), "-pubkey", "-noout"],
            capture_output=True, timeout=10,
        )
        if pem.returncode != 0:
            return None
        text = pem.stdout.decode()
        b64 = "".join(
            line for line in text.splitlines()
            if "-----" not in line
        )
        der = base64.b64decode(b64)
        import hashlib
        return base64.b64encode(hashlib.sha256(der).digest()).decode()
    except Exception:
        return None


def _cleanup() -> None:
    global _process
    if _process is not None:
        try:
            _process.terminate()
        except Exception:
            pass
        _process = None


def ensure_proxy_running(timeout: float = 20.0) -> tuple[int, str] | None:
    """Start mitmdump with the BFSB adblock addon and return (port, spki_hash).

    Reuses a stale mitmdump only if the process exists (keeps addon state
    consistent within a desktop session). Returns None on any failure -
    caller must then NOT set proxy flags.
    """
    global _process

    # Reuse an already-running BFSB mitmdump (desktop entry relaunches)
    if _port_open(PROXY_HOST, PROXY_PORT):
        stale = _mitmdump_pids_on_port(PROXY_PORT)
        ca = Path.home() / ".bfsb" / "mitmproxy" / "mitmproxy-ca-cert.pem"
        spki = _compute_spki_hash(ca) if ca.exists() else None
        if stale or spki:
            # Existing proxy - kill stale and restart fresh so the addon
            # (with the response() rewriting hook) is guaranteed current.
            for pid in stale:
                try:
                    subprocess.run(["kill", str(pid)], capture_output=True, timeout=5)
                except Exception:
                    pass
            # Wait for the port to actually CLOSE before starting the new
            # instance - otherwise the new mitmdump fails to bind (port still
            # held by the dying process), the ready-check below sees the OLD
            # socket and reports success, and the browser routes into a dead
            # port -> "no internet" on every page.
            for _ in range(20):
                if not _port_open(PROXY_HOST, PROXY_PORT):
                    break
                time.sleep(0.25)
        else:
            # Port taken by something else - fail safe
            return None

    # Prepare cert directory; pre-copy the standard CA so mitmdump reuses it
    from .config import PATHS
    confdir = PATHS.BFSB_DIR / "mitmproxy"
    confdir.mkdir(parents=True, exist_ok=True)

    std_ca = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
    conf_ca = confdir / "mitmproxy-ca-cert.pem"
    if std_ca.exists() and not conf_ca.exists():
        try:
            shutil.copy2(std_ca, conf_ca)
        except Exception:
            pass

    addon_path = Path(__file__).resolve().parent / "proxy_addon.py"

    cmd = [
        "mitmdump",
        "--mode", "regular",
        "--listen-host", PROXY_HOST,
        "--listen-port", str(PROXY_PORT),
        "--set", f"confdir={confdir}",
        "--set", "ssl_insecure=true",
        "--set", "block_global=false",
        "--set", "stream_large_bodies=3m",
        "--allow-hosts", ALLOW_HOSTS_REGEX,
        "-s", str(addon_path),
        "--quiet",
    ]

    try:
        _process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        atexit.register(_cleanup)
    except Exception:
        _process = None
        return None

    # Wait for the port and the CA to be ready
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_open(PROXY_HOST, PROXY_PORT) and conf_ca.exists():
            break
        if _process.poll() is not None:
            # mitmdump died
            return None
        time.sleep(0.3)
    else:
        _cleanup()
        return None

    spki = _compute_spki_hash(conf_ca)

    # Sync CA back to the standard location for webengine.py's NSS installer
    if conf_ca.exists():
        try:
            std_ca.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(conf_ca, std_ca)
        except Exception:
            pass

    if not spki:
        # Without SPKI hash, HTTPS interception would break cert validation.
        _cleanup()
        return None

    # Diagnostics append to a file - desktop-entry launches have no terminal
    # for stdout, so the proxy state was never visible there.
    try:
        log = Path.home() / ".local" / "share" / "bfsb" / "diag.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        with open(log, "a") as f:
            f.write(
                f"{datetime.now().isoformat(timespec='seconds')} [Proxy] active "
                f"on 127.0.0.1:{PROXY_PORT} spki={spki}\n"
            )
    except Exception:
        pass

    return (PROXY_PORT, spki)
