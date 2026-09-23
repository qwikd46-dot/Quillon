"""CLI to manage BFSB search shortcuts.

Usage:
    python -m bfsb.core.search.shortcuts_cli list
    python -m bfsb.core.search.shortcuts_cli add <key> <url>
    python -m bfsb.core.search.shortcuts_cli remove <key>
    python -m bfsb.core.search.shortcuts_cli reset
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bfsb.core.search.shortcuts import (
    DEFAULT_SHORTCUTS_PATH,
    ShortcutManager,
)


def main() -> int:
    p = argparse.ArgumentParser(
        description="BFSB shortcuts — manage the local keyword->URL list"
    )
    p.add_argument("--path", default=str(DEFAULT_SHORTCUTS_PATH),
                   help="shortcuts JSON file")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List all shortcuts")
    add = sub.add_parser("add", help="Add a shortcut")
    add.add_argument("key", help="lowercase keyword, e.g. youtube")
    add.add_argument("url", help="target URL, must start with http:// or https://")
    rm = sub.add_parser("remove", help="Remove a shortcut")
    rm.add_argument("key", help="keyword to remove")
    sub.add_parser("reset", help="Restore the default shortcuts")

    args = p.parse_args()
    mgr = ShortcutManager(Path(args.path))

    if args.cmd == "list":
        items = mgr.load()
        if not items:
            print("(no shortcuts)")
            return 0
        width = max(len(k) for k in items)
        for k in sorted(items):
            print(f"  {k:<{width}}  -> {items[k]}")
        return 0

    if args.cmd == "add":
        try:
            mgr.add(args.key, args.url)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"added: {args.key.lower()} -> {args.url}")
        return 0

    if args.cmd == "remove":
        if mgr.remove(args.key):
            print(f"removed: {args.key.lower()}")
            return 0
        print(f"not found: {args.key.lower()}", file=sys.stderr)
        return 1

    if args.cmd == "reset":
        mgr.reset_to_defaults()
        print(f"reset to defaults ({len(mgr.load())} shortcuts)")
        return 0

    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
