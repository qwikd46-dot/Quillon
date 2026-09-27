"""The Ghostery Node backend must not outlive BFSB.

Nothing in the app called the engine's stop(), so every browser exit left
one Node process behind, reparented to systemd --user. Seventy-one had
accumulated. These tests spawn a real process and check it dies with the
one that started it, which is the only way to catch this: the old code
passed every test that did not actually look at the process table.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE = REPO_ROOT / "bfsb" / "core" / "ghostery_engine.py"

HAVE_NODE = False
for _candidate in ("node",):
    try:
        subprocess.run([_candidate, "--version"], capture_output=True, timeout=15)
        HAVE_NODE = True
    except Exception:
        HAVE_NODE = False


def _run_child_source(body: str) -> str:
    """Write a child program that exits without touching the process."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write(body)
        return handle.name


@unittest.skipUnless(HAVE_NODE, "node is not installed")
@unittest.skipUnless(ENGINE.exists(), "ghostery engine module is missing")
class GhosteryProcessLifetimeTests(unittest.TestCase):
    def _spawn_and_exit(self, extra_setup: str = "") -> int:
        """Start a child that runs the engine, then exits hard.

        Returns the Node pid so the parent can check whether it survived.
        """
        child = _run_child_source(textwrap.dedent(f"""
            import json, sys
            sys.path.insert(0, {str(REPO_ROOT)!r})
            from pathlib import Path
            from bfsb.core.ghostery_engine import GhosteryEngineClient

            {extra_setup}
            repo = Path({str(REPO_ROOT / "ghostery-adblocker")!r})
            client = GhosteryEngineClient(repo, repo / "server.js")
            client.start()
            print(json.dumps({{"pid": client._process.pid}}), flush=True)
            # Deliberately no stop(), no close(): this is a browser being
            # closed from the window, which is how the orphans were made.
        """))
        try:
            result = subprocess.run(
                [sys.executable, child], capture_output=True, text=True, timeout=180
            )
            self.assertEqual(result.returncode, 0, result.stderr[-800:])
            for line in result.stdout.splitlines():
                if line.startswith("{"):
                    return int(json.loads(line)["pid"]) if False else int(
                        __import__("json").loads(line)["pid"]
                    )
        finally:
            os.unlink(child)
        self.fail("child did not report a pid")

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    def test_node_backend_does_not_survive_the_process_that_started_it(self):
        pid = self._spawn_and_exit()
        # atexit runs on the child's normal exit; give it a moment.
        import time
        for _ in range(20):
            if not self._alive(pid):
                break
            time.sleep(0.25)
        survived = self._alive(pid)
        if survived:
            try:
                os.kill(pid, 9)
            except Exception:
                pass
        self.assertFalse(
            survived,
            f"the Ghostery node process {pid} outlived BFSB; every browser "
            "exit leaks one",
        )


class GhosteryEngineWiringTests(unittest.TestCase):
    """Structural checks that need no node."""

    def setUp(self):
        self.source = ENGINE.read_text(encoding="utf-8")

    def test_start_registers_a_shutdown_hook(self):
        self.assertIn("_register_atexit", self.source)
        self.assertIn("atexit.register", self.source)

    def test_engine_owns_its_process_group(self):
        # Without its own group, stop() can only signal the direct child and
        # anything node forked survives.
        self.assertIn("start_new_session=True", self.source)

    def test_stop_kills_the_whole_tree(self):
        start = self.source.index("def _kill_tree")
        body = self.source[start:start + 2000]
        self.assertIn("killpg", body)


if __name__ == "__main__":
    unittest.main()
