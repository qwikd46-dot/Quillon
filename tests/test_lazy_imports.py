"""Import-cost contracts.

mitmdump loads ``proxy_addon.py`` as a standalone script in a headless
process. Anything the addon pulls in is paid for on every proxy start,
and it used to include the entire PyQt6 stack, because importing any
``bfsb.core`` submodule ran a package ``__init__`` that eagerly imported
``webengine`` (and ``bfsb`` itself eagerly imported ``bfsb.ui``).

These run in a fresh interpreter because the only honest way to ask
"what did importing this cost" is to import it in a clean process --
the test runner has already loaded PyQt6 for the Qt suites.
"""

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]

_ADDON_PROBE = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location(
    "bfsb_addon_probe", "bfsb/core/proxy_addon.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print("ADDON=" + ("ok" if hasattr(module, "youtube_filter") else "broken"))
print("PYQT6=" + ("yes" if any(m.startswith("PyQt6") for m in sys.modules) else "no"))
"""


def run_probe(source):
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )


class ProxyAddonImportCostTests(unittest.TestCase):
    def test_loading_the_addon_does_not_pull_in_pyqt6(self):
        result = run_probe(_ADDON_PROBE)
        self.assertEqual(
            result.returncode, 0, f"addon failed to import: {result.stderr[-500:]}"
        )
        # Guard against a vacuous pass: the addon must really have loaded.
        self.assertIn("ADDON=ok", result.stdout, result.stdout + result.stderr)
        self.assertIn("PYQT6=no", result.stdout, result.stdout + result.stderr)

    def test_the_blocklist_leaf_does_not_pull_in_pyqt6(self):
        result = run_probe(
            "import sys; from bfsb.core.search import safety; "
            "print('PYQT6=' + ('yes' if any(m.startswith('PyQt6') "
            "for m in sys.modules) else 'no'))"
        )
        self.assertEqual(result.returncode, 0, result.stderr[-500:])
        self.assertIn("PYQT6=no", result.stdout)

    def test_importing_bfsb_core_alone_does_not_pull_in_pyqt6(self):
        result = run_probe(
            "import sys; import bfsb.core; "
            "print('PYQT6=' + ('yes' if any(m.startswith('PyQt6') "
            "for m in sys.modules) else 'no'))"
        )
        self.assertEqual(result.returncode, 0, result.stderr[-500:])
        self.assertIn("PYQT6=no", result.stdout)


class LazyExportContractTests(unittest.TestCase):
    """The package __init__ files resolve names on first use.

    A name that stops resolving is a silent AttributeError at some future
    call site, so every advertised export is resolved for real.
    """

    def test_every_core_export_resolves(self):
        probe = (
            "import sys; sys.path.insert(0, '.'); import bfsb.core as m\n"
            "missing = []\n"
            "for n in m.__all__:\n"
            "    try: getattr(m, n)\n"
            "    except Exception as e: missing.append((n, repr(e)))\n"
            "print('MISSING=' + repr(missing))"
        )
        result = run_probe(probe)
        self.assertEqual(result.returncode, 0, result.stderr[-500:])
        self.assertIn("MISSING=[]", result.stdout, result.stdout)

    def test_every_search_export_resolves(self):
        probe = (
            "import sys; sys.path.insert(0, '.'); import bfsb.core.search as m\n"
            "missing = []\n"
            "for n in m.__all__:\n"
            "    try: getattr(m, n)\n"
            "    except Exception as e: missing.append((n, repr(e)))\n"
            "print('MISSING=' + repr(missing))"
        )
        result = run_probe(probe)
        self.assertEqual(result.returncode, 0, result.stderr[-500:])
        self.assertIn("MISSING=[]", result.stdout, result.stdout)

    def test_unknown_name_still_raises_attribute_error(self):
        """Laziness must not turn typos into something else."""
        result = run_probe(
            "import sys; sys.path.insert(0, '.'); import bfsb.core as m\n"
            "try:\n"
            "    m.definitely_not_exported\n"
            "    print('NO_RAISE')\n"
            "except AttributeError:\n"
            "    print('RAISED_OK')"
        )
        self.assertIn("RAISED_OK", result.stdout, result.stdout + result.stderr)
