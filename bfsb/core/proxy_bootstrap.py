"""BFSB proxy bootstrap - starts mitmdump BEFORE Qt initializes so Chromium
can be routed through it via QTWEBENGINE_CHROMIUM_FLAGS.

Network-level ad blocking architecture: the mitmproxy addon
(bfsb/core/proxy_addon.py) rewrites /youtubei/v1/* JSON responses and strips
adPlacements/adSlots/playerAds BEFORE the page ever sees them - undetectable
by page-side anti-adblock checks (no JS hooks involved).

Fail-safe: if the proxy cannot start, ensure_proxy_running() returns None
and the browser runs WITHOUT proxy flags - degraded ad blocking, zero breakage.

Interception is scoped to the reviewed YouTube and Google Video hosts only
(--allow-hosts), so all other traffic tunnels through untouched.
"""

from __future__ import annotations

import atexit
import base64
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8228
PROXY_PID_FILE = Path.home() / ".bfsb" / "proxy.pid"

ALLOW_HOSTS = (
    "youtube.com",
    "www.youtube.com",
    "*.googlevideo.com",
)
ALLOW_HOST_PATTERNS = (
    r"youtube\.com",
    r"www\.youtube\.com",
    r"(?:[a-z0-9-]+\.)*googlevideo\.com",
)
ALLOW_HOSTS_REGEX = (
    r"^(?:" + "|".join(ALLOW_HOST_PATTERNS) + r")(?::[0-9]+)?$"
)

_process: subprocess.Popen | None = None


def _mitmdump_command() -> list[str] | None:
    candidates = [shutil.which("mitmdump"), str(Path.home() / ".local" / "bin" / "mitmdump")]
    for candidate in candidates:
        if not candidate or not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
            continue
        try:
            result = subprocess.run([candidate, "--version"], capture_output=True, timeout=5)
            if result.returncode == 0:
                return [candidate]
        except Exception:
            continue
    try:
        import importlib.util
        if importlib.util.find_spec("mitmproxy") is None:
            return None
    except Exception:
        return None
    code = "from mitmproxy.tools.main import mitmdump; mitmdump()"
    probe = "import mitmproxy"
    commands = []
    prefixes = []
    for prefix in (getattr(sys, "base_prefix", ""), sys.prefix, getattr(sys, "exec_prefix", "")):
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)
    for prefix in prefixes:
        base = Path(prefix)
        library_dirs = []
        for name in ("lib", "lib64"):
            directory = base / name
            if directory.is_dir() and str(directory) not in library_dirs:
                library_dirs.append(str(directory))
        for directory in library_dirs:
            for loader in sorted(Path(directory).glob("ld-linux*.so*")):
                if not loader.is_file() or not os.access(loader, os.X_OK):
                    continue
                search_path = library_dirs[:]
                inherited = os.environ.get("LD_LIBRARY_PATH", "")
                if inherited:
                    search_path.extend(part for part in inherited.split(os.pathsep) if part and part not in search_path)
                prefix_command = [
                    str(loader), "--library-path", os.pathsep.join(search_path),
                    sys.executable,
                ]
                commands.append(([*prefix_command, "-c", probe], [*prefix_command, "-c", code]))
    commands.insert(0, ([sys.executable, "-c", probe], [sys.executable, "-c", code]))
    for probe_command, command in commands:
        try:
            result = subprocess.run(probe_command, capture_output=True, timeout=5)
            if result.returncode == 0:
                return command
        except Exception:
            continue
    return None


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except Exception:
        return False


def _is_bfsb_mitmprocess(raw_cmdline: str, port: int) -> bool:
    tokens = [token for token in raw_cmdline.split("\x00") if token]
    has_port = f"--listen-port={port}" in tokens
    if "--listen-port" in tokens:
        index = tokens.index("--listen-port")
        has_port = has_port or (
            index + 1 < len(tokens) and tokens[index + 1] == str(port)
        )
    cmdline = " ".join(tokens)
    return "mitmdump" in cmdline and "proxy_addon.py" in cmdline and has_port


def _mitmdump_pids_on_port(port: int) -> list[int]:
    pids = []
    try:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                raw_cmdline = (proc / "cmdline").read_bytes().decode(
                    "utf-8", errors="ignore"
                )
                if _is_bfsb_mitmprocess(raw_cmdline, port):
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


def _terminate_process(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(os.getpgid(pid), sig)
    except Exception:
        try:
            os.kill(pid, sig)
        except Exception:
            pass


def _recorded_proxy_pid() -> Optional[int]:
    try:
        pid = int(PROXY_PID_FILE.read_text().strip())
        if pid <= 1:
            return None
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", errors="ignore")
        if "proxy_addon.py" not in cmdline or str(PROXY_PORT) not in cmdline:
            return None
        return pid
    except Exception:
        return None


def _cleanup_recorded_proxy() -> None:
    pid = _recorded_proxy_pid()
    if pid is not None:
        _terminate_process(pid, signal.SIGTERM)
    try:
        PROXY_PID_FILE.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _is_bfsb_mitmprocess_for_port() -> bool:
    """True when a live BFSB mitmdump is currently serving our port.

    Adopting someone else's healthy proxy is what ensure_proxy_running
    already does, so shutdown must not kill it either.
    """
    for pid in _mitmdump_pids_on_port(PROXY_PORT):
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes().decode(
                "utf-8", errors="ignore"
            )
        except OSError:
            continue
        if _is_bfsb_mitmprocess(raw, PROXY_PORT):
            return True
    return False


def _cleanup() -> None:
    global _process
    process = _process
    if process is not None:
        _terminate_process(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=2)
        except Exception:
            _terminate_process(process.pid, signal.SIGKILL)
        _process = None
        _cleanup_recorded_proxy()
        return
    # No proxy of ours to stop. The pid file is keyed to the PORT, not to
    # the owning process, so another live BFSB instance's proxy is in it.
    # Killing that would break a second running browser, so only reclaim
    # the port when nothing of ours exists AND the port is not serving a
    # healthy proxy we can adopt.
    if not _is_bfsb_mitmprocess_for_port():
        _cleanup_recorded_proxy()


def shutdown_proxy() -> None:
    _cleanup()


def ensure_proxy_running(timeout: float = 20.0) -> tuple[int, str] | None:
    """Start mitmdump with the BFSB adblock addon and return (port, spki_hash).

    Reuses a stale mitmdump only if the process exists (keeps addon state
    consistent within a desktop session). Returns None on any failure -
    caller must then NOT set proxy flags.
    """
    global _process

    if _process is None:
        _cleanup_recorded_proxy()

    if _port_open(PROXY_HOST, PROXY_PORT):
        stale = _mitmdump_pids_on_port(PROXY_PORT)
        if not stale:
            return None
        for pid in stale:
            try:
                subprocess.run(["kill", str(pid)], capture_output=True, timeout=5)
            except Exception:
                pass
        for _ in range(20):
            if not _port_open(PROXY_HOST, PROXY_PORT):
                break
            time.sleep(0.25)
        if _port_open(PROXY_HOST, PROXY_PORT):
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
    mitmdump = _mitmdump_command()
    if mitmdump is None:
        return None

    cmd = [
        *mitmdump,
        "--mode", "regular",
        "--listen-host", PROXY_HOST,
        "--listen-port", str(PROXY_PORT),
        "--set", f"confdir={confdir}",
        "--set", "block_global=false",
        "--allow-hosts", ALLOW_HOSTS_REGEX,
        "-s", str(addon_path),
        "--quiet",
    ]

    try:
        # The proxy used to be started with both streams sent to
        # /dev/null, so when it failed to start the only symptom was a
        # browser error page (ERR_PROXY_CONNECTION_FAILED) and a zombie
        # process, with nothing anywhere saying why. Capture stderr to a
        # log the user and we can both read.
        _proxy_log = PROXY_PID_FILE.parent / "proxy-startup.log"
        try:
            _proxy_log.parent.mkdir(parents=True, exist_ok=True)
            _stderr_handle = open(_proxy_log, "ab")
        except OSError:
            _stderr_handle = subprocess.DEVNULL
        _process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=_stderr_handle,
            start_new_session=True,
        )
    except Exception:
        _process = None
        return None
    try:
        PROXY_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROXY_PID_FILE.write_text(str(_process.pid), encoding="ascii")
    except Exception:
        pass
    atexit.register(_cleanup)

    # Wait for the port and the CA to be ready
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _process.poll() is not None:
            return None
        if _port_open(PROXY_HOST, PROXY_PORT) and conf_ca.exists():
            break
        time.sleep(0.3)
    else:
        _cleanup()
        return None

    if _process.poll() is not None:
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
