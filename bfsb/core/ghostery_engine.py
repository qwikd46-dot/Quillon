from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

# urllib.error / urllib.request are imported inside the two methods that
# use them. urllib.request pulls http.client, ssl and email -- ~0.09s --
# and the engine is constructed during browser startup, long before the
# first request is proxied.


class GhosteryEngineError(RuntimeError):
    """Raised when the Ghostery Node.js backend is unavailable or unhealthy."""


def _die_with_parent() -> None:
    """Ask the kernel to kill this child when its parent dies.

    atexit does not run on SIGTERM, and a Python signal handler does not
    run at all while Qt's C++ event loop is blocking, so neither can be
    relied on. PR_SET_PDEATHSIG needs no cooperation from the parent's
    exit path: the kernel reaps the child the moment BFSB is gone, for
    any reason -- normal quit, signal, crash or a hard kill.
    """
    try:
        import ctypes
        import signal

        PR_SET_PDEATHSIG = 1
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    except Exception:
        pass  # best effort; atexit and the signal path still apply


class GhosteryEngineClient:
    """Persistent Node.js subprocess exposing the Ghostery AdBlocker engine.

    The Ghostery AdBlocker is a TypeScript/JavaScript library. BFSB runs it in
    a long-lived Node.js subprocess and communicates over a local HTTP API.

    Robustness notes (v2):
    * stdout/stderr are drained continuously by background threads into
      ``~/.bfsb/logs/ghostery-engine.log``. Previously the pipes were never
      read, so once the OS pipe buffer (~64 KiB) filled up the Node process
      would block forever on ``console.error`` — a classic subprocess
      deadlock that silently killed ad blocking mid-session.
    * If the backend process dies, ``_request`` attempts a one-shot restart
      before giving up, so a crashed Node doesn't mean zero ad blocking
      for the rest of the session.
    """

    _MAX_TAIL_LINES = 200

    def __init__(self, repo_dir: Path, script_path: Path, node_binary: str = "node") -> None:
        self.repo_dir = repo_dir
        self.script_path = script_path
        self.node_binary = node_binary
        self._process: Optional[subprocess.Popen[str]] = None
        self._port: Optional[int] = None
        self._lock = threading.RLock()
        self._stats: dict[str, Any] = {}
        self._stdout_tail: list[str] = []
        self._stderr_tail: list[str] = []
        self._log_dir = Path.home() / ".bfsb" / "logs"

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    @property
    def ready(self) -> bool:
        """True once the engine has finished starting and is serving requests."""
        return (
            self._process is not None
            and self._process.poll() is None
            and self._port is not None
            and bool(self._stats)
        )

    def start(self) -> dict[str, Any]:
        """Start the Node.js server and wait until the Ghostery engine is ready."""
        import urllib.error
        import urllib.request

        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self._stats

            if shutil.which(self.node_binary) is None:
                raise GhosteryEngineError(
                    f"Node.js executable '{self.node_binary}' is required to run the "
                    "Ghostery AdBlocker backend"
                )

            if not self.script_path.exists():
                raise GhosteryEngineError(
                    f"Ghostery backend script not found: {self.script_path}"
                )

            self._log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self._log_dir / "ghostery-engine.log"

            port = self._free_port()
            env = os.environ.copy()
            env["NODE_ENV"] = "production"
            env["GHOSTERY_PORT"] = str(port)
            self._process = subprocess.Popen(
                [self.node_binary, str(self.script_path)],
                cwd=str(self.repo_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
                # Own process group, so stop() can take the whole tree down.
                # Without this a Node child that forked would survive.
                start_new_session=True,
                preexec_fn=_die_with_parent,
            )
            self._port = port
            # Nothing in the app called stop(), so every BFSB exit left one
            # Node process behind; seventy-one had accumulated, all
            # reparented to systemd --user because their parent had gone.
            # atexit covers a normal quit, an exception, and sys.exit alike.
            self._register_atexit()

            # Drain both pipes so Node can never deadlock on a full buffer.
            drain_args = [
                (self._process.stdout, self._stdout_tail, "stdout"),
                (self._process.stderr, self._stderr_tail, "stderr"),
            ]
            for stream, tail, name in drain_args:
                t = threading.Thread(
                    target=self._drain_stream,
                    args=(stream, tail, name, log_path),
                    daemon=True,
                )
                t.start()

            try:
                deadline = time.time() + 90.0
                while time.time() < deadline:
                    if self._process.poll() is not None:
                        # Give the drain threads a moment to collect output.
                        time.sleep(0.2)
                        stderr = "".join(self._stderr_tail)
                        raise GhosteryEngineError(
                            "Ghostery backend exited during startup:\n" + stderr
                        )
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/stats", timeout=1.0
                        ) as response:
                            payload = json.loads(response.read().decode("utf-8"))
                            if payload.get("error") == "engine not loaded":
                                time.sleep(0.1)
                                continue
                            self._stats = payload
                            return payload
                    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                        time.sleep(0.1)
                raise GhosteryEngineError(
                    "Timed out waiting for the Ghostery AdBlocker backend to become ready"
                )
            except Exception:
                self.stop()
                raise

    def _drain_stream(self, stream, tail: list[str], name: str, log_path: Path) -> None:
        """Continuously read a subprocess pipe into a bounded tail + log file."""
        if stream is None:
            return
        try:
            with open(log_path, "a", buffering=1) as log:
                for line in iter(stream.readline, ""):
                    tail.append(line)
                    if len(tail) > self._MAX_TAIL_LINES:
                        del tail[: len(tail) - self._MAX_TAIL_LINES]
                    try:
                        log.write(f"[{name}] {line}")
                    except Exception:
                        pass
        except (ValueError, OSError):
            pass  # pipe closed during shutdown
        except Exception:
            pass

    def _register_atexit(self) -> None:
        """Guarantee the Node process dies with this one.

        The app has no other shutdown path for it: the browser is closed
        from the window, the tray and the launcher, and a hard exit must
        not be the only thing that ever cleans this up.
        """
        import atexit

        process = self._process
        if process is None:
            return

        def _cleanup() -> None:
            try:
                self._kill_tree(process)
            except Exception:
                pass

        atexit.register(_cleanup)

    @staticmethod
    def _kill_tree(process: "subprocess.Popen[str]") -> None:
        """Terminate the engine and anything it forked."""
        if process.poll() is not None:
            return
        try:
            import os as _os
            import signal as _signal

            _os.killpg(_os.getpgid(process.pid), _signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.terminate()
            except Exception:
                return
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            try:
                import os as _os
                import signal as _signal

                _os.killpg(_os.getpgid(process.pid), _signal.SIGKILL)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
            try:
                process.wait(timeout=2.0)
            except Exception:
                pass

    def stop(self) -> None:
        """Stop the Node.js backend process."""
        with self._lock:
            process = self._process
            self._process = None
            self._port = None
            self._stats = {}
            if process is not None and process.poll() is None:
                self._kill_tree(process)
            for stream in (getattr(process, "stdout", None), getattr(process, "stderr", None)):
                try:
                    if stream:
                        stream.close()
                except Exception:
                    pass

    def close(self) -> None:
        """Alias for stop()."""
        self.stop()

    def _free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    # ------------------------------------------------------------------ #
    # HTTP API
    # ------------------------------------------------------------------ #

    def _request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        import urllib.request

        if self._process is None or self._port is None:
            raise GhosteryEngineError("Ghostery backend is not running")

        if self._process.poll() is not None:
            # The backend died (OOM, crash, ...). Try to bring it back once;
            # if that fails, surface the error so callers fall back to the
            # DNS blocklist instead of silently letting ads through.
            try:
                self.start()
            except GhosteryEngineError:
                raise GhosteryEngineError(
                    "Ghostery backend process exited unexpectedly and could not be restarted"
                )

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self._port}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15.0) as response:
            return json.loads(response.read().decode("utf-8"))

    def match(self, url: str, source_url: str = "", resource_type: str | None = None) -> dict[str, Any]:
        """Ask Ghostery whether a request should be blocked/redirected."""
        return self._request(
            "/match",
            {
                "url": url,
                "sourceUrl": source_url,
                "resourceType": resource_type or "other",
            },
        )

    def cosmetics(self, url: str, hostname: str, domain: str) -> dict[str, Any]:
        """Ask Ghostery for cosmetic filters to inject into a page."""
        return self._request(
            "/cosmetics",
            {"url": url, "hostname": hostname, "domain": domain},
        )

    def scriptlet(self, url: str) -> str:
        """Ask Ghostery for a scriptlet to inject into a page."""
        payload = self._request("/scriptlet", {"url": url})
        return str(payload.get("script") or "")

    def stats(self) -> dict[str, Any]:
        """Return cached Ghostery engine statistics."""
        return dict(self._stats)
