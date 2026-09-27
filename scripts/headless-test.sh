#!/bin/bash
# BFSB headless test harness — launcher.
#
# This script is now a thin wrapper around ``hermes.py``, which does
# all the actual orchestration (Xvfb spawn, BFSB launch, check run,
# report generation).
#
# Usage:
#   ./scripts/headless-test.sh                  # run the full suite
#   ./scripts/headless-test.sh --keep-display   # leave Xvfb/BFSB running (debug)
#   ./scripts/headless-test.sh --no-color      # plain-text output
#   ./scripts/headless-test.sh --timeout 60    # per-check timeout override
#
# Screenshots and report go to scripts/screenshots/.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Pass through any args to hermes.py.
exec python3 "$SCRIPT_DIR/hermes.py" "$@"
