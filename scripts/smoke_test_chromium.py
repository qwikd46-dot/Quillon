#!/usr/bin/env python3
"""Run non-UI smoke tests against a built Chromium binary."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test a Chromium build")
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    binary = args.binary.expanduser().resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        print(f"binary is not executable: {binary}")
        return 1
    try:
        version = run([str(binary), "--version"], args.timeout)
    except subprocess.TimeoutExpired:
        print("version check timed out")
        return 1
    if version.returncode != 0:
        print(version.stderr.strip() or "version check failed")
        return 1
    print(f"version={version.stdout.strip()}")
    profile = Path(tempfile.mkdtemp(prefix="quillon-chromium-smoke-"))
    profile.chmod(0o700)
    try:
        command = [
            str(binary),
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            "--dump-dom",
            "about:blank",
        ]
        result = run(command, args.timeout)
    except subprocess.TimeoutExpired:
        print("headless smoke test timed out")
        return 1
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    if result.returncode != 0:
        print(result.stderr.strip() or "headless smoke test failed")
        return 1
    print("headless=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
