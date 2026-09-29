"""Quillon test harness orchestrator.

Spawns Xvfb + Quillon via ``HermesDisplay``, runs every check in
``hermes_checks.CHECKS`` against the running Quillon, and prints a
color-coded report via ``HermesLog``.

Behavior:
- Each check is independent: a failure does NOT stop subsequent checks.
- Each check has a soft timeout (default 30s). If it hangs, we mark
  it as FAIL with a timeout message and move on.
- The full report is written to ``scripts/screenshots/report.log``;
  the screenshots directory holds per-check PNGs.

Run via ``scripts/headless-test.sh`` (which sets up the right
working directory and passes the project root). Can also be run
directly: ``python3 scripts/hermes.py``.

Why a custom orchestrator (not pytest): pytest fixtures would force
a fixed setup/teardown model; we need to interleave check groups
and produce a single human-readable report. Pytest's output is
optimized for code, not for end-to-end UI smoke tests.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Optional

# Make sure hermes_checks can import its siblings
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from hermes_log import CheckResult, HermesLog
from hermes_xvfb import HermesDisplay
from hermes_checks import CHECKS

import quillon_cdp


# ── Constants ──────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = SCRIPT_DIR / "screenshots"
DEFAULT_CHECK_TIMEOUT_S = 30.0


# ── Per-check timeout wrapper ──────────────────────────────────────

async def _run_with_timeout(
    coro, name: str, timeout_s: float, log: HermesLog
) -> CheckResult:
    """Run a check coroutine with a hard timeout.

    We can't use the ``@_timed`` wrapper's try/except here because we
    need the timeout to also produce a CheckResult. We measure the
    time and stash exceptions in the result.
    """
    t0 = time.time()
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except asyncio.TimeoutError:
        return CheckResult(
            name=name,
            passed=False,
            duration_s=time.time() - t0,
            detail=f"timeout after {timeout_s:.0f}s",
            context=[f"check did not complete in {timeout_s:.0f}s — likely hung"],
        )
    except Exception as e:
        return CheckResult(
            name=name,
            passed=False,
            duration_s=time.time() - t0,
            detail=f"exception: {type(e).__name__}: {e}",
        )


# ── Main ───────────────────────────────────────────────────────────

async def run(
    timeout_s: float = DEFAULT_CHECK_TIMEOUT_S,
    keep_display: bool = False,
) -> int:
    """Run the full suite. Returns the exit code (0 = all pass, 1 = at least one fail)."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = SCREENSHOT_DIR / "report.log"
    log = HermesLog(report_path)

    log.banner("Quillon Hermes Test Harness")
    log.info(f"screenshots → {SCREENSHOT_DIR}")
    log.info(f"report     → {report_path}")

    all_results: list[CheckResult] = []

    with HermesDisplay(PROJECT_DIR, SCREENSHOT_DIR, keep=keep_display) as display:
        log.info(f"Xvfb ready on {display.display}")
        display.start_quillon(timeout_s=30)
        log.info("Quillon ready (CDP up)")

        # Connect CDP.
        async with quillon_cdp.CDP(port=display.cdp_port) as cdp:
            # Run each group, each check.
            for group_name, group_checks in CHECKS:
                if not group_checks:
                    log.group(group_name)
                    log.info("(no checks implemented in this group yet)")
                    continue
                log.group(group_name)
                for check_fn in group_checks:
                    name = check_fn.__name__.replace("check_", "")
                    log.info(f"running {name}…")
                    # Call the check (which is already wrapped with @_timed).
                    result = await _run_with_timeout(
                        check_fn(cdp, display, log),
                        name=name,
                        timeout_s=timeout_s,
                        log=log,
                    )
                    log.check(result)
                    all_results.append(result)
                    # Small breathing room between checks.
                    await asyncio.sleep(0.2)

            log.summary(all_results)

    log.close()
    # Exit code: 0 if all passed, 1 otherwise.
    failed = sum(1 for r in all_results if not r.passed)
    return 0 if failed == 0 else 1


def main() -> None:
    p = argparse.ArgumentParser(description="Quillon Hermes test harness")
    p.add_argument(
        "--timeout", type=float, default=DEFAULT_CHECK_TIMEOUT_S,
        help=f"per-check timeout in seconds (default: {DEFAULT_CHECK_TIMEOUT_S})",
    )
    p.add_argument(
        "--keep-display", action="store_true",
        help="don't kill Xvfb/Quillon on exit (for debugging)",
    )
    p.add_argument(
        "--no-color", action="store_true",
        help="disable ANSI color in output",
    )
    args = p.parse_args()
    if args.no_color:
        os.environ["NO_COLOR"] = "1"
    rc = asyncio.run(run(timeout_s=args.timeout, keep_display=args.keep_display))
    sys.exit(rc)


if __name__ == "__main__":
    main()
