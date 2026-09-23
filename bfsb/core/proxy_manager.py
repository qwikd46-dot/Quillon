"""BFSB Proxy Manager - Starts/stops mitmproxy with adblock addon."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("bfsb.proxy_manager")


class BFSBProxyManager:
    """Manages mitmproxy subprocess for system-wide ad/tracker blocking."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        addon_path: Optional[str] = None,
    ):
        self._host = host
        self._port = port
        self._addon_path = addon_path or str(
            Path(__file__).parent / "proxy_addon.py"
        )
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._cert_dir: Optional[Path] = None

    @property
    def proxy_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start mitmproxy with the adblock addon."""
        if self._running:
            return

        # Use persistent cert directory in BFSB data dir
        from .config import PATHS
        self._cert_dir = PATHS.BFSB_DIR / "mitmproxy"
        self._cert_dir.mkdir(parents=True, exist_ok=True)

        # Also copy CA to standard mitmproxy location for auto-detection
        self._standard_cert_dir = Path.home() / ".mitmproxy"
        self._standard_cert_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"[BFSB Proxy] Certificate directory: {self._cert_dir}")

        # Build mitmproxy command
        cmd = [
            "mitmdump",  # headless version, no TTY required
            "--mode", "regular",
            "--listen-host", self._host,
            "--listen-port", str(self._port),
            "--set", f"confdir={self._cert_dir}",
            "--set", "ssl_insecure=true",
            "--set", "block_global=false",
            "-s", self._addon_path,
            "--quiet",
        ]

        logger.info(f"[BFSB Proxy] Starting: {' '.join(cmd)}")

        # Start mitmproxy
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )

        # Wait for proxy to be ready
        await self._wait_ready()

        self._running = True
        logger.info(f"[BFSB Proxy] Started on {self.proxy_url}")

        # Copy CA to standard locations for auto-detection
        asyncio.create_task(self._sync_ca_certificates())

        # Start log reader
        asyncio.create_task(self._read_logs())

    async def _sync_ca_certificates(self) -> None:
        """Copy mitmproxy CA to standard locations after it's generated."""
        import shutil
        # Wait a bit for mitmproxy to generate the CA
        await asyncio.sleep(2.0)

        src = self._cert_dir / "mitmproxy-ca-cert.pem"
        if src.exists():
            # Copy to standard mitmproxy location
            dst1 = self._standard_cert_dir / "mitmproxy-ca-cert.pem"
            try:
                shutil.copy2(src, dst1)
                logger.info(f"[BFSB Proxy] CA copied to {dst1}")
            except Exception as e:
                logger.debug(f"[BFSB Proxy] Failed to copy CA to standard location: {e}")

            # Also copy to BFSB data dir
            from .config import PATHS
            dst2 = PATHS.BFSB_DIR / "mitmproxy-ca-cert.pem"
            try:
                shutil.copy2(src, dst2)
                logger.info(f"[BFSB Proxy] CA copied to {dst2}")
            except Exception as e:
                logger.debug(f"[BFSB Proxy] Failed to copy CA to BFSB dir: {e}")

    async def _wait_ready(self, timeout: float = 10.0) -> None:
        """Wait for proxy to accept connections."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                reader, writer = await asyncio.open_connection(
                    self._host, self._port
                )
                writer.close()
                await writer.wait_closed()
                return
            except Exception:
                await asyncio.sleep(0.2)
        raise RuntimeError(f"Proxy failed to start on {self._host}:{self._port}")

    async def _read_logs(self) -> None:
        """Read and log proxy output."""
        if not self._process or not self._process.stderr:
            return

        loop = asyncio.get_event_loop()
        while self._running:
            try:
                line = await loop.run_in_executor(
                    None, self._process.stderr.readline
                )
                if not line:
                    break
                line = line.decode("utf-8", errors="ignore").strip()
                if line and not line.startswith("Proxy listening"):
                    logger.debug(f"[mitmproxy] {line}")
            except Exception:
                break

    async def stop(self) -> None:
        """Stop the proxy."""
        if not self._running:
            return

        self._running = False
        logger.info("[BFSB Proxy] Stopping...")

        if self._process:
            try:
                # Terminate process group
                if hasattr(os, "killpg"):
                    os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                else:
                    self._process.terminate()

                # Wait for termination
                try:
                    await asyncio.wait_for(
                        loop.run_in_executor(None, self._process.wait),
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    if hasattr(os, "killpg"):
                        os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                    else:
                        self._process.kill()
                    await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"[BFSB Proxy] Error stopping: {e}")

        # Cleanup cert directory
        if self._cert_dir and self._cert_dir.exists():
            import shutil
            try:
                shutil.rmtree(self._cert_dir)
            except Exception:
                pass

        logger.info("[BFSB Proxy] Stopped")

    def get_ca_cert_path(self) -> Optional[Path]:
        """Get the CA certificate path for browser trust."""
        if self._cert_dir:
            ca_path = self._cert_dir / "mitmproxy-ca-cert.pem"
            if ca_path.exists():
                return ca_path
        return None


async def run_proxy(
    host: str = "127.0.0.1",
    port: int = 8080,
) -> BFSBProxyManager:
    """Create and start BFSB proxy manager."""
    proxy = BFSBProxyManager(host, port)
    await proxy.start()
    return proxy