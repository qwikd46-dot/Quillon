"""Quillon is Linux-only, and should say so before anything breaks.

The alternative is a crash somewhere inside proxy lifecycle handling, or
worse, a live proxy that outlives the browser because nothing on that
platform can reap it. The message is the feature; these keep it honest.
"""

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def run_entrypoint(env_overrides: dict) -> subprocess.CompletedProcess:
    import os

    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.update(env_overrides)
    # Exercise only the guard: importing quillon.main pulls in Qt WebEngine,
    # which is a different (and much slower) thing to be testing here.
    return subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.');"
         "from quillon.main import _check_platform; _check_platform();"
         " print('PASSED')"],
        cwd=ROOT, capture_output=True, text=True, timeout=120, env=env,
    )


class PlatformSupportTests(unittest.TestCase):
    def test_linux_is_allowed(self):
        result = run_entrypoint({})
        self.assertEqual(result.returncode, 0, result.stderr[-500:])
        self.assertIn("PASSED", result.stdout)

    def test_the_guard_rejects_a_non_linux_sys_platform(self):
        """Call the guard with sys.platform forced, which is the real path."""
        probe = (
            "import sys; sys.path.insert(0, '.');\n"
            "from quillon import main as m\n"
            "sys.platform = 'win32'\n"
            "try:\n"
            "    m._check_platform()\n"
            "except SystemExit as e:\n"
            "    text = str(e)\n"
            "    assert 'Linux only' in text, text\n"
            "    assert 'ghcr.io/qwikd46-dot/quillon' in text, text\n"
            "    assert 'QUILLON_ALLOW_UNSUPPORTED_PLATFORM' in text, text\n"
            "    print('REFUSED_OK')\n"
            "else:\n"
            "    raise AssertionError('the guard let a non-Linux platform through')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=ROOT,
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertIn("REFUSED_OK", result.stdout)

    def test_the_override_lets_a_non_linux_platform_through(self):
        probe = (
            "import sys; sys.path.insert(0, '.');\n"
            "from quillon import main as m\n"
            "sys.platform = 'win32'\n"
            "import os; os.environ['QUILLON_ALLOW_UNSUPPORTED_PLATFORM'] = '1'\n"
            "m._check_platform()\n"
            "print('OVERRIDE_OK')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=ROOT,
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertIn("OVERRIDE_OK", result.stdout)
        self.assertIn("at your own risk", result.stderr)


if __name__ == "__main__":
    unittest.main()
