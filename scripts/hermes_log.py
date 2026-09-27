"""Color-coded logger for the BFSB test harness.

Four levels:
- ``PASS`` (green ✓) — a check passed
- ``FAIL`` (red ✗)   — a check failed; a follow-up block of context is
  printed (what was tried, what was expected, last screenshot path)
- ``INFO`` (cyan)    — operational info ("BFSB ready in 4.2s")
- ``WARN`` (yellow)  — something is odd but not fatal

Each call writes ONE line to stderr (live, for humans) AND one line to
``report.log`` (plain text, for diffing across runs). On FAIL the
context block goes to stderr only — keeps the log file scannable.

Why not stdlib logging: we want ANSI color, a one-line entry shape,
and automatic follow-up context on FAIL. Reusing ``logging`` would
either mean custom Formatters and Handlers (more code than this) or
losing the color/context.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── ANSI colors ─────────────────────────────────────────────────────
# Only emit color codes if stderr is a TTY. When invoked from CI or
# from a non-interactive shell, we fall back to plain text.
def _supports_color() -> bool:
    return sys.stderr.isatty() and os.environ.get("NO_COLOR") is None


_USE_COLOR = _supports_color()


def _wrap(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def _green(t: str) -> str:
    return _wrap("32", t)


def _red(t: str) -> str:
    return _wrap("31", t)


def _cyan(t: str) -> str:
    return _wrap("36", t)


def _yellow(t: str) -> str:
    return _wrap("33", t)


def _bold(t: str) -> str:
    return _wrap("1", t)


def _dim(t: str) -> str:
    return _wrap("2", t)


# ── Result data class ───────────────────────────────────────────────

@dataclass
class CheckResult:
    """Outcome of a single check."""
    name: str
    passed: bool
    duration_s: float
    detail: str = ""           # one-line context (popover at 720,108)
    context: list[str] = field(default_factory=list)  # follow-up lines on FAIL
    screenshot: Optional[str] = None  # path to last screenshot taken

    def emoji(self) -> str:
        return "✓" if self.passed else "✗"

    def level_label(self) -> str:
        return "PASS" if self.passed else "FAIL"


# ── Logger ──────────────────────────────────────────────────────────

class HermesLog:
    """The harness's logger.

    Usage:
        log = HermesLog(report_path)
        log.info("starting Xvfb on :99")
        result = CheckResult(name="tab_opens", passed=True, duration_s=0.3)
        log.check(result)
        log.summary([result, result, ...])
    """

    def __init__(self, report_path: Path) -> None:
        self._report = open(report_path, "w", buffering=1)
        self._start = time.time()
        self._group: Optional[str] = None  # current group header (e.g. "MENU + POPOVERS")

    # ── low-level ─────────────────────────────────────────────────

    def _write(self, colored_line: str, plain_line: str) -> None:
        """Write one line to both stderr (colored) and report (plain)."""
        print(colored_line, file=sys.stderr, flush=True)
        self._report.write(plain_line + "\n")
        self._report.flush()

    # ── public API ────────────────────────────────────────────────

    def banner(self, text: str) -> None:
        """Top-of-run banner."""
        line = "═" * 60
        self._write(_bold(line), line)
        self._write(_bold(f"  {text}"), f"  {text}")
        self._write(_bold(line), line)

    def group(self, title: str) -> None:
        """Section header (e.g. 'TABS'). Subsequent checks nest under it."""
        self._group = title
        self._write(_bold(f"\n  {title}"), f"\n  {title}")
        self._write(_dim("  " + "─" * 50), "  " + "-" * 50)

    def info(self, msg: str) -> None:
        self._write(_cyan(f"  ℹ {msg}"), f"  INFO  {msg}")

    def warn(self, msg: str) -> None:
        self._write(_yellow(f"  ⚠ {msg}"), f"  WARN  {msg}")

    def check(self, result: CheckResult) -> None:
        """Log a single check result.

        PASS: one green line with check name + duration.
        FAIL: one red line, then a follow-up block with the context list,
        indented for readability. The follow-up block is omitted from the
        plain-text report (keeps the log scannable).
        """
        dur = f"{result.duration_s:.1f}s"
        detail = f"  {result.detail}" if result.detail else ""
        if result.passed:
            line = f"  {_green('✓ ' + result.level_label().ljust(4))} {_bold(result.name):<48} ({dur}){detail}"
            plain = f"  PASS  {result.name:<48} ({dur}){detail}"
        else:
            line = f"  {_red('✗ ' + result.level_label().ljust(4))} {_bold(result.name):<48} ({dur}){detail}"
            plain = f"  FAIL  {result.name:<48} ({dur}){detail}"
            for ctx_line in result.context:
                ctx_colored = f"      {_red(ctx_line)}"
                self._write(ctx_colored, "")  # NOT in report (keep it scannable)
            if result.screenshot:
                shot = f"      📸 {result.screenshot}"
                self._write(_dim(shot), "")
        self._write(line, plain)

    def summary(self, results: list[CheckResult]) -> None:
        """Final summary line + table of failures."""
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        failed = total - passed
        dur = time.time() - self._start
        status = _green("ALL PASSED") if failed == 0 else _red(f"{failed} FAILED")
        self._write("", "")
        self._write(
            _bold(f"  SUMMARY: {passed}/{total} passed ({dur:.1f}s) — {status}"),
            f"  SUMMARY: {passed}/{total} passed ({dur:.1f}s) — {status}",
        )
        if failed:
            self._write("", "")
            self._write(_bold("  Failures:"), "  Failures:")
            for r in results:
                if r.passed:
                    continue
                line = f"    {_red('✗')} {r.name}"
                if r.detail:
                    line += f"  — {r.detail}"
                if r.screenshot:
                    line += f"  📸 {r.screenshot}"
                self._write(line, line)

    def close(self) -> None:
        self._report.close()
