"""Launch Quillon Chromium without starting the Qt browser window."""

from __future__ import annotations

import argparse
import asyncio
import sys
import urllib.request
from pathlib import Path
from typing import Optional

from .core.chromium_runtime import (
    ChromiumLaunchSpec,
    PrivacyPolicy,
    default_profile_dir,
    find_chromium,
    load_policy,
    policy_for_mode,
)


def _server_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return response.status < 500
    except Exception:
        return False


async def _start_local_server(host: str, port: int):
    from .core.server import BFSHBServer

    template_dir = Path(__file__).parent / "templates"
    server = BFSHBServer(template_dir, host=host, port=port)
    await server.start()
    return server


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the standalone Quillon Chromium")
    parser.add_argument("--binary", help="Chromium binary path or name")
    parser.add_argument("--profile", type=Path, help="Persistent profile directory")
    parser.add_argument("--url", default="http://127.0.0.1:8889/")
    parser.add_argument("--policy", choices=("standard", "balanced", "strict"), default="balanced")
    parser.add_argument("--policy-file", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8889)
    parser.add_argument("--no-server", action="store_true")
    parser.add_argument("--no-proxy", action="store_true")
    parser.add_argument("--extra-arg", action="append", default=[])
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    binary = find_chromium(args.binary)
    profile = args.profile or default_profile_dir()
    policy: PrivacyPolicy
    if args.policy_file:
        policy = load_policy(args.policy_file)
    else:
        policy = policy_for_mode(args.policy)
    base_url = f"http://{args.host}:{args.port}"
    server = None
    if not args.no_server and not _server_ready(base_url + "/health"):
        server = await _start_local_server(args.host, args.port)
    proxy_url = None
    spki = None
    proxy_owned = False
    if not args.no_proxy:
        from .core.proxy_bootstrap import ensure_proxy_running

        proxy = await asyncio.to_thread(ensure_proxy_running)
        if proxy:
            proxy_port, spki = proxy
            proxy_url = f"http://127.0.0.1:{proxy_port}"
            proxy_owned = True
    spec = ChromiumLaunchSpec(
        binary=binary,
        profile_dir=profile,
        initial_url=args.url,
        policy=policy,
        proxy_url=proxy_url,
        spki_fingerprint=spki,
        extra_args=tuple(args.extra_arg),
    )
    process = await asyncio.create_subprocess_exec(*spec.command())
    try:
        return await process.wait()
    finally:
        if server is not None:
            await server.stop()
        if proxy_owned:
            try:
                from .core.proxy_bootstrap import shutdown_proxy

                shutdown_proxy()
            except Exception:
                pass


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"[Quillon Chromium] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
