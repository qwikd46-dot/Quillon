"""CLI driver for the BFSB custom crawler.

Usage (single URL):
    python -m bfsb.core.search.crawl_runner <url>
    python -m bfsb.core.search.crawl_runner <url> --recursive

Usage (batch from seeds file):
    python -m bfsb.core.search.crawl_runner --seeds-file <json>
    python -m bfsb.core.search.crawl_runner --seeds-file seeds.json \
        --per-seed-pages 3 --no-sitemap --delay 1.5

seeds.json format:
    ["https://www.facebook.com", "https://www.instagram.com", ...]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from bfsb.core.search.crawler import Crawler, crawl_recursive


async def _run_single(
    crawler: Crawler,
    url: str,
    recursive: bool,
    max_pages: int,
    delay: float,
    use_sitemap: bool,
    quiet: bool,
) -> tuple[int, int]:
    """Crawl one seed URL. Returns (ok_count, fail_count)."""
    if not recursive:
        result = await crawler.fetch(url)
        path, _ = crawler.save(result)
        if not quiet:
            print(f"URL:    {result.url}")
            print(f"Status: {result.status_code} {result.content_type}")
            print(f"Title:  {result.title[:80]}")
            print(f"Text:   {len(result.text)} chars, {len(result.out_links)} out-links")
            print(f"Saved:  {path}")
            if result.error:
                print(f"Error:  {result.error}")
        return (1 if result.ok else 0, 0 if result.ok else 1)

    crawler.delay_seconds = delay
    results = await crawl_recursive(
        crawler, url,
        max_pages=max_pages,
        use_sitemap=use_sitemap,
    )
    for r in results:
        crawler.save(r)
    if not quiet:
        for r in results:
            print(f"[OK ] {r.title[:60]:<60}  {r.url[:60]}")
    return (len(results), 0)


async def main() -> int:
    p = argparse.ArgumentParser(
        description="BFSB custom crawler — fetch URLs (single or batch from seeds file)"
    )
    # Single-URL mode
    p.add_argument("url", nargs="?", help="Single URL to fetch (omit if --seeds-file)")
    p.add_argument(
        "--recursive", action="store_true",
        help="Follow same-domain links (BFS, polite)",
    )
    p.add_argument(
        "--max-pages", type=int, default=20,
        help="Max pages in single-URL recursive mode (default: 20)",
    )
    # Batch mode
    p.add_argument(
        "--seeds-file", type=str, default=None,
        help="JSON file with list of seed URLs for batch crawl",
    )
    p.add_argument(
        "--per-seed-pages", type=int, default=3,
        help="Max pages per seed in batch mode (default: 3)",
    )
    # Common
    p.add_argument(
        "--data-dir", default="/home/zon/bfsb/data/crawled",
        help="Where to write JSON files",
    )
    p.add_argument(
        "--delay", type=float, default=1.5,
        help="Seconds between requests per domain (default: 1.5)",
    )
    p.add_argument(
        "--no-sitemap", action="store_true",
        help="Skip sitemap.xml discovery (faster, fewer pages)",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-page status output",
    )
    args = p.parse_args()

    if not args.url and not args.seeds_file:
        p.error("either URL or --seeds-file is required")

    crawler = Crawler(
        data_dir=Path(args.data_dir),
        delay_seconds=args.delay,
    )
    crawler.delay_seconds = args.delay

    total_ok = 0
    total_fail = 0

    if args.seeds_file:
        seeds_path = Path(args.seeds_file)
        try:
            seeds = json.loads(seeds_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"failed to read {seeds_path}: {e}", file=sys.stderr)
            return 2
        if not isinstance(seeds, list):
            print(f"{seeds_path}: expected JSON list of URL strings", file=sys.stderr)
            return 2
        if not args.quiet:
            print(f"Crawling {len(seeds)} seeds, max {args.per_seed_pages} pages each, "
                  f"delay={args.delay}s/domain, sitemap={'on' if not args.no_sitemap else 'off'}")
        for i, seed in enumerate(seeds, 1):
            if not isinstance(seed, str) or not seed.startswith(("http://", "https://")):
                if not args.quiet:
                    print(f"[{i:>3}/{len(seeds)}] SKIP invalid: {seed!r}")
                total_fail += 1
                continue
            ok, fail = await _run_single(
                crawler, seed,
                recursive=True,
                max_pages=args.per_seed_pages,
                delay=args.delay,
                use_sitemap=not args.no_sitemap,
                quiet=args.quiet,
            )
            total_ok += ok
            total_fail += fail
            if not args.quiet:
                print(f"[{i:>3}/{len(seeds)}] {seed} -> {ok} ok, {fail} fail")
        if not args.quiet:
            print(f"\nDone: {total_ok} ok / {total_fail} fail / {len(seeds)} seeds attempted")
        return 0 if total_fail == 0 else 2

    # Single-URL mode
    ok, fail = await _run_single(
        crawler, args.url,
        recursive=args.recursive,
        max_pages=args.max_pages,
        delay=args.delay,
        use_sitemap=not args.no_sitemap,
        quiet=args.quiet,
    )
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
