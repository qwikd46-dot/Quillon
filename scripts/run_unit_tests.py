#!/usr/bin/env python3
"""Run every unit test in tests/ and report what the run actually covered.

The point of this script is that a green run is only meaningful if you
know which tests were skipped. Passing a hand-written list of modules
also rots: a new test file is silently ignored. So this discovers
tests/test_*.py itself, and prints the interpreter it used plus whether
the optional dependencies were importable, so a run that quietly
skipped half the suite is visible rather than inferred from the word OK.

The environment that actually exercises everything is host python3.14,
because that is where PyQt6 lives. Under the isolated uv env the
server and vault tests skip, so a green uv run is partly green because
it is not testing what it appears to.
"""

import argparse
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def _importable(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def _discover() -> list[pathlib.Path]:
    return sorted(TESTS.glob("test_*.py"))


def _load(path: pathlib.Path) -> unittest.TestSuite:
    name = f"bfsb_suite_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return unittest.defaultTestLoader.loadTestsFromModule(module)


class CountingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.skip_lines: list[str] = []

    def addSkip(self, test, reason):  # noqa: N802 - unittest API
        super().addSkip(test, reason)
        if isinstance(test, tuple):
            test = test[0]
        label = test.id() if hasattr(test, "id") else str(test)
        entry = f"{label} :: {reason}"
        if entry not in self.skip_lines:
            self.skip_lines.append(entry)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="count", default=1)
    parser.add_argument(
        "-k", "--pattern", default="test_*.py", help="glob for test files"
    )
    args = parser.parse_args()

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    files = sorted(TESTS.glob(args.pattern))
    if not files:
        print(f"no test files matched {args.pattern} in {TESTS}", file=sys.stderr)
        return 2

    pyqt6 = _importable("PyQt6")
    crypto = _importable("cryptography")
    keyring = _importable("keyring")

    print("=" * 72)
    print(f"interpreter : {sys.executable}")
    print(f"python      : {sys.version.split()[0]}")
    print(f"test files  : {len(files)}")
    print(f"PyQt6       : {'yes' if pyqt6 else 'NO - server and GUI tests will SKIP'}")
    print(f"cryptography: {'yes' if crypto else 'NO - vault tests will SKIP'}")
    print(f"keyring     : {'yes' if keyring else 'NO - vault key custody tests will SKIP'}")
    if not pyqt6:
        print("-" * 72)
        print("WARNING: this run is not testing what it appears to.")
        print("Run it on host python3.14 for the suite that actually exercises the code.")
    print("=" * 72)

    suite = unittest.TestSuite()
    for path in files:
        suite.addTests(_load(path))

    runner = unittest.TextTestRunner(
        verbosity=args.verbose, resultclass=CountingResult, stream=sys.stderr
    )
    result = runner.run(suite)

    print("=" * 72)
    print(f"ran    : {result.testsRun}")
    print(f"failed : {len(result.failures)}")
    print(f"errors : {len(result.errors)}")
    print(f"SKIPPED : {len(result.skipped)}")
    for line in result.skip_lines:
        print(f"   - {line}")
    print("=" * 72)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
