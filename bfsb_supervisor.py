"""BFSB container supervisor — the single process that owns everything.

This is PID 1 in the image. It starts the display, the search backend and
the browser, then stays in the foreground supervising them, so the whole
stack starts as one unit and stops as one unit.

What this process starts, and what it deliberately does not
------------------------------------------------------------
It starts three things: Xvfb, SearXNG, and the BFSB application.

It does NOT start the mitmdump proxy or the Node ad-block engine, because
the application already owns both. bfsb.core.proxy_bootstrap starts
mitmdump on 8228 with a pid file keyed to the port and the owning process,
and the Ghostery engine is started with PR_SET_PDEATHSIG so the kernel
reaps it. Starting a second copy from here would fight that lifecycle
logic and reintroduce the "closing one instance kills another's proxy"
class of bug.

The result is a single ownership chain:

    supervisor (pid 1)
      +-- Xvfb
      +-- SearXNG            127.0.0.1:8888
      +-- bfsb.main          127.0.0.1:8889   (the Qt app)
           +-- mitmdump      127.0.0.1:8228
           +-- node engine   ephemeral

Everything is a separate OS process and cannot be otherwise: SearXNG is
its own WSGI app, mitmdump is a proxy server with its own event loop, and
the ad-block engine is a Node program. What "one process" buys is that you
start and stop one thing, and that nothing survives it.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

APP_DIR = Path(os.environ.get("BFSB_APP_DIR", "/opt/bfsb")).resolve()
HOME = Path(os.environ.get("HOME", "/home/binwalk"))
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp/runtime"))
SEARXNG_PORT = int(os.environ.get("BFSB_SEARXNG_PORT", "8888"))
SEARXNG_URL = os.environ.get("BFSB_SEARXNG_URL", f"http://127.0.0.1:{SEARXNG_PORT}")

# A child that has been up this long without a successful readiness probe
# is treated as failed rather than waited on forever.
STARTUP_TIMEOUT = float(os.environ.get("BFSB_STARTUP_TIMEOUT", "180"))
SHUTDOWN_GRACE = float(os.environ.get("BFSB_SHUTDOWN_GRACE", "10"))


def log(message: str) -> None:
    print(f"[supervisor] {message}", flush=True)


class Child:
    """One supervised process."""

    def __init__(self, name: str, argv: list[str], *, env: Optional[dict] = None,
                 cwd: Optional[Path] = None, essential: bool = True) -> None:
        self.name = name
        self.argv = argv
        self.env = env
        self.cwd = cwd
        self.essential = essential
        self.process: Optional[subprocess.Popen] = None

    def start(self) -> None:
        env = os.environ.copy()
        if self.env:
            env.update(self.env)
        log(f"starting {self.name}: {' '.join(self.argv[:3])} ...")
        self.process = subprocess.Popen(
            self.argv,
            cwd=str(self.cwd or APP_DIR),
            env=env,
            # Its own process group, so a stop() takes the whole subtree
            # and the children do not die on the supervisor's signals
            # before we have had a chance to order a clean shutdown.
            start_new_session=True,
        )

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        if not self.alive():
            return
        pid = self.process.pid
        log(f"stopping {self.name} (pid {pid})")
        _signal_tree(pid, signal.SIGTERM)
        deadline = time.monotonic() + SHUTDOWN_GRACE
        while time.monotonic() < deadline and self.alive():
            time.sleep(0.1)
        if self.alive():
            log(f"{self.name} ignored SIGTERM, sending SIGKILL")
            _signal_tree(pid, signal.SIGKILL)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def _signal_tree(pid: int, sig: int) -> None:
    """Signal a process and its children, deepest first."""
    import signal as _signal

    children: list[int] = []
    try:
        out = subprocess.run(
            ["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=5
        )
        children = [int(x) for x in out.stdout.split()]
    except Exception:
        pass
    for child in children:
        _signal_tree(child, sig)
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def wait_for_http(url: str, timeout: float, process: Optional[subprocess.Popen] = None) -> bool:
    """Poll an HTTP endpoint until it answers, the child dies, or we time out."""
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except urllib.error.HTTPError:
            # It answered, just not with 2xx. That is alive enough.
            return True
        except Exception:
            time.sleep(0.4)
    return False


def write_searxng_settings() -> Path:
    """Generate a settings.yml with JSON output enabled.

    The aggregator queries /search?format=json, which SearXNG will not
    serve unless json is listed, so this is not optional.
    """
    import secrets

    settings_dir = HOME / ".bfsb" / "searxng"
    settings_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    settings = settings_dir / "settings.yml"
    if not settings.exists():
        settings.write_text(
            "use_default_settings: true\n"
            "server:\n"
            '  secret_key: "%s"\n'
            '  bind_address: "127.0.0.1"\n'
            f"  port: {SEARXNG_PORT}\n"
            f'  base_url: "{SEARXNG_URL}/"\n'
            "  limiter: false\n"
            "  image_proxy: false\n"
            "search:\n"
            "  formats:\n"
            "    - html\n"
            "    - json\n" % secrets.token_hex(32),
            encoding="utf-8",
        )
        settings.chmod(0o600)
    return settings


def build_children(headless: bool) -> list[Child]:
    children: list[Child] = []

    if headless and not os.environ.get("DISPLAY"):
        xvfb = shutil.which("Xvfb")
        if xvfb is None:
            raise SystemExit("Xvfb is required for headless mode but is not installed")
        display = os.environ.get("BFSB_DISPLAY", ":99")
        os.environ["DISPLAY"] = display
        children.append(Child(
            "Xvfb",
            [xvfb, display, "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
        ))

    settings = write_searxng_settings()
    gunicorn = shutil.which("gunicorn")
    if gunicorn is None:
        log("gunicorn not found; SearXNG will not start and search will be offline")
    else:
        children.append(Child(
            "searxng",
            [
                gunicorn,
                "--bind", f"127.0.0.1:{SEARXNG_PORT}",
                "--workers", "2",
                "--threads", "4",
                "--timeout", "60",
                "searx.webapp:app",
            ],
            env={
                "SEARXNG_SETTINGS_PATH": str(settings),
                "SEARXNG_BIND_ADDRESS": "127.0.0.1",
                "SEARXNG_PORT": str(SEARXNG_PORT),
            },
        ))

    children.append(Child(
        "bfsb",
        [sys.executable, "-m", "bfsb.main"],
        env={
            # Match the launcher: software rendering and no sandbox. The
            # sandbox cannot work inside a container without user
            # namespaces, and these flags are what the on-host launcher
            # already uses.
            "QT_QUICK_BACKEND": "software",
            "QT_WEBENGINE_DISABLE_GPU": "1",
            "QTWEBENGINE_CHROMIUM_FLAGS": (
                "--no-sandbox --disable-dev-shm-usage --disable-gpu "
                "--disable-gpu-compositing --disable-webgl --disable-webgl2 "
                "--disable-3d-apis --disable-breakpad --disable-extensions "
                "--disable-plugins --disable-default-apps --disable-sync "
                "--disable-background-networking "
                "--disable-background-timer-throttling "
                "--disable-renderer-backgrounding "
                "--disable-features=VizDisplayCompositor,UseSkiaRenderer,CanvasOopRasterization "
                "--num-raster-threads=1"
            ),
        },
    ))

    # The app binds 127.0.0.1 on purpose, so a published port cannot reach
    # it: inside a container, loopback is the container's own namespace,
    # not the host's. Rather than weaken that binding, opt in to a forwarder.
    if os.environ.get("BFSB_EXPOSE", "0") == "1":
        socat = shutil.which("socat")
        address = _container_address()
        if socat is None:
            log("BFSB_EXPOSE=1 but socat is not installed; ports stay unreachable")
        elif address is None:
            log("BFSB_EXPOSE=1 but no container address found; ports stay unreachable")
        else:
            log(f"forwarding 8889 and 8228 on {address} for the published ports")
            for port, name in ((8889, "ui"), (8228, "proxy")):
                children.append(Child(
                    f"expose-{name}",
                    [socat, f"TCP-LISTEN:{port},fork,reuseaddr,bind={address}",
                     f"TCP:127.0.0.1:{port}"],
                ))
    return children


def _container_address() -> Optional[str]:
    """This container's own IPv4 address.

    The forwarder must bind here rather than 0.0.0.0: podman's port
    publishing already holds the wildcard inside the network namespace,
    and socat would fail with EADDRINUSE.
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def main() -> int:
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(RUNTIME, 0o700)

    headless = os.environ.get("BFSB_HEADLESS", "1") == "1"
    children = build_children(headless)
    by_name = {child.name: child for child in children}

    stopping = False

    def handle_signal(signum, _frame):
        nonlocal stopping
        if not stopping:
            stopping = True
            log(f"received {signal.Signals(signum).name}, shutting down")

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    for child in children:
        child.start()
        if child.name == "Xvfb":
            time.sleep(1.0)
        elif child.name == "searxng":
            if not wait_for_http(f"{SEARXNG_URL}/healthz", STARTUP_TIMEOUT, child.process):
                log("SearXNG did not become ready in time; continuing without search")
            else:
                log("SearXNG ready")
        elif child.name == "bfsb":
            if not wait_for_http("http://127.0.0.1:8889/health", STARTUP_TIMEOUT, child.process):
                log("BFSB did not become ready in time; continuing anyway")
            else:
                log("BFSB ready")

    # Supervise. The browser is the point of the container, so if the
    # essential services die we bring the whole thing down rather than
    # leave a half-running stack that looks alive.
    exit_code = 0
    try:
        while not stopping:
            time.sleep(0.5)
            for child in children:
                if child.alive():
                    continue
                code = child.process.returncode if child.process else "?"
                if child is by_name.get("Xvfb"):
                    log(f"Xvfb exited ({code}); display is gone, stopping")
                    stopping = True
                    exit_code = 1
                    break
                if child is by_name.get("bfsb"):
                    log(f"the browser exited ({code}); stopping the stack")
                    exit_code = int(code) if isinstance(code, int) else 0
                    stopping = True
                    break
                log(f"{child.name} exited ({code}); it will not be restarted")
    finally:
        # Reverse start order: the browser first, so it can shut down
        # cleanly before its dependencies disappear underneath it.
        for child in reversed(children):
            child.stop()
        log("stopped")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
