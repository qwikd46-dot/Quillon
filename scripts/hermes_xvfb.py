"""Xvfb + Quillon lifecycle for the test harness.

Extracted from ``headless-test.sh`` so the Python orchestrator can
spawn/teardown the display and Quillon without shelling out.

Lifecycle:
    with HermesDisplay() as display:
        display.start_quillon()
        # ... run checks ...
    # __exit__ tears down Xvfb + Quillon

What this gives us:
- A guaranteed-clean Xvfb on a free display
- Quillon launched with the right env vars (XCB, software rendering, no
  GPU — the same flags as the production launcher)
- A log file we can tail if Quillon dies
- A CDP port (9222) we can poll until Quillon is ready
- A full-screen screenshot helper that grabs the Xvfb framebuffer
  via ``import -window root`` (ImageMagick) or scrot, falling back
  to no-screenshot if neither is available
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional


# ── Constants ───────────────────────────────────────────────────────
DEFAULT_DISPLAY = ":99"
DEFAULT_SCREEN = "1400x900x24"
DEFAULT_CDP_PORT = 9222
DEFAULT_SERVER_PORT = 8889
QUILLON_LAUNCH_SCRIPT = "quillon_launcher.sh"  # the production launcher


def _free_display(start: int = 99) -> str:
    """Find a free X display number by trying to create an Xvfb."""
    for n in range(start, start + 50):
        disp = f":{n}"
        # Quick check: any Xvfb on this display?
        result = subprocess.run(
            ["pgrep", "-f", f"Xvfb {disp}"],
            capture_output=True,
        )
        if result.returncode != 0:
            return disp
    return DEFAULT_DISPLAY  # fall through; will likely fail


class HermesDisplay:
    """An Xvfb + Quillon pair for testing."""

    def __init__(
        self,
        project_dir: Path,
        log_dir: Path,
        screen: str = DEFAULT_SCREEN,
        keep: bool = False,
    ) -> None:
        self.project_dir = project_dir
        self.log_dir = log_dir
        self.screen = screen
        self.keep = keep  # if True, don't kill Xvfb/Quillon on exit
        self.display: Optional[str] = None
        self.xvfb_pid: Optional[int] = None
        self.quillon_pid: Optional[int] = None
        self.quillon_log: Optional[Path] = None
        self.cdp_port = DEFAULT_CDP_PORT
        self.server_port = DEFAULT_SERVER_PORT
        log_dir.mkdir(parents=True, exist_ok=True)

    # ── context manager ───────────────────────────────────────────

    def __enter__(self) -> "HermesDisplay":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ── Xvfb lifecycle ────────────────────────────────────────────

    def start(self) -> None:
        """Start Xvfb."""
        self.display = _free_display()
        env_display = f"Xvfb {self.display}"
        xvfb_log = self.log_dir / "xvfb.log"
        self.xvfb_proc = subprocess.Popen(
            [
                "Xvfb", self.display,
                "-screen", "0", self.screen,
                "-ac",
                "+extension", "RANDR",
            ],
            stdout=open(xvfb_log, "wb"),
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,  # new process group for clean kill
        )
        self.xvfb_pid = self.xvfb_proc.pid
        # Give Xvfb a moment to bind.
        time.sleep(0.5)
        if self.xvfb_proc.poll() is not None:
            raise RuntimeError(
                f"Xvfb failed to start (exit={self.xvfb_proc.returncode}); see {xvfb_log}"
            )
        # Make sure subsequent subprocesses inherit the display.
        os.environ["DISPLAY"] = self.display

    def start_quillon(self, timeout_s: float = 30.0) -> None:
        """Launch Quillon under this display; wait for CDP."""
        if self.display is None:
            raise RuntimeError("call start() before start_quillon()")
        self.quillon_log = self.log_dir / "quillon.log"
        quillon_proc = subprocess.Popen(
            ["bash", str(self.project_dir / QUILLON_LAUNCH_SCRIPT)],
            cwd=str(self.project_dir),
            env={**os.environ, "DISPLAY": self.display, "PYTHONUNBUFFERED": "1", "QUILLON_TEST": "1"},
            stdout=open(self.quillon_log, "wb"),
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
        self.quillon_pid = quillon_proc.pid
        # Wait for CDP.
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._cdp_alive():
                return
            if quillon_proc.poll() is not None:
                # Show last 40 lines so the caller can diagnose.
                last = self._tail(self.quillon_log, 40)
                raise RuntimeError(
                    f"Quillon exited with code {quillon_proc.returncode} before CDP came up.\n"
                    f"--- last 40 lines of {self.quillon_log} ---\n{last}"
                )
            time.sleep(0.5)
        raise RuntimeError(
            f"CDP did not come up on port {self.cdp_port} within {timeout_s}s.\n"
            f"--- last 40 lines of {self.quillon_log} ---\n{self._tail(self.quillon_log, 40)}"
        )

    def stop(self) -> None:
        """Tear down Quillon + Xvfb unless --keep-display was requested."""
        if self.keep:
            return
        if self.quillon_pid is not None:
            try:
                os.killpg(os.getpgid(self.quillon_pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        if self.xvfb_pid is not None:
            try:
                os.killpg(os.getpgid(self.xvfb_pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        # Best-effort cleanup of any leftover QtWebEngine processes.
        subprocess.run(["pkill", "-9", "-f", "quillon.main"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "QtWebEngineProc"], capture_output=True)
        # Wait briefly for everything to die.
        time.sleep(0.5)

    # ── helpers ───────────────────────────────────────────────────

    def _cdp_alive(self) -> bool:
        """Check if the CDP HTTP endpoint is responding."""
        import urllib.request
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.cdp_port}/json/version", timeout=0.5
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    @staticmethod
    def _tail(path: Path, n: int) -> str:
        if not path.exists():
            return ""
        with open(path, "rb") as f:
            data = f.read()
        lines = data.splitlines()[-n:]
        return "\n".join(l.decode("utf-8", "replace") for l in lines)

    # ── screenshot helper ─────────────────────────────────────────

    def screenshot(self, out_path: Path) -> bool:
        """Capture the full Xvfb screen to a PNG.

        Tries ``import -window root`` (ImageMagick) first, then
        ``scrot``. Returns True on success. Either tool is fine;
        both are typically installed for headless setups.
        """
        out_path.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "DISPLAY": self.display or DEFAULT_DISPLAY}
        for tool, args in [
            ("import", ["-window", "root", str(out_path)]),
            ("scrot", [str(out_path)]),
        ]:
            if shutil.which(tool) is None:
                continue
            try:
                result = subprocess.run(
                    [tool, *args],
                    env=env,
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode == 0 and out_path.exists():
                    return True
            except Exception:
                continue
        return False
