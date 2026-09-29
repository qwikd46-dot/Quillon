"""Quillon Proxy - Local filtering proxy for ad/tracker blocking."""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
import time
from typing import Optional, Set
from urllib.parse import urlparse

from .blocker import URLBlocker

logger = logging.getLogger("quillon.proxy")


class QuillonProxy:
    """Local filtering proxy that blocks ads/trackers before they reach the browser."""

    def __init__(self, blocker: URLBlocker, host: str = "127.0.0.1", port: int = 8080):
        self._blocker = blocker
        self._host = host
        self._port = port
        self._server: Optional[asyncio.Server] = None
        self._running = False
        self._blocked_count = 0
        self._allowed_count = 0

    @property
    def proxy_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    @property
    def stats(self) -> dict:
        return {
            "blocked": self._blocked_count,
            "allowed": self._allowed_count,
            "running": self._running,
            "url": self.proxy_url,
        }

    async def start(self) -> None:
        """Start the proxy server."""
        if self._running:
            return

        self._server = await asyncio.start_server(
            self._handle_client,
            self._host,
            self._port,
            reuse_address=True,
        )
        self._running = True
        logger.info(f"[Quillon Proxy] Started on {self.proxy_url}")

    async def stop(self) -> None:
        """Stop the proxy server."""
        if not self._running:
            return

        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        logger.info("[Quillon Proxy] Stopped")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle incoming client connection."""
        client_addr = writer.get_extra_info("peername")
        logger.debug(f"[Quillon Proxy] Client connected: {client_addr}")

        try:
            # Read the request line
            request_line = await reader.readline()
            if not request_line:
                return

            request_line = request_line.decode("utf-8", errors="ignore").strip()
            logger.debug(f"[Quillon Proxy] Request: {request_line}")

            parts = request_line.split()
            if len(parts) < 3:
                return

            method, url, version = parts[0], parts[1], parts[2]

            if method == "CONNECT":
                await self._handle_connect(reader, writer, url)
            else:
                await self._handle_http_request(reader, writer, method, url, version)

        except Exception as e:
            logger.error(f"[Quillon Proxy] Error handling client: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def _handle_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, url: str) -> None:
        """Handle HTTPS CONNECT tunneling."""
        # Parse host:port from CONNECT request
        host_port = url.split(":")
        if len(host_port) != 2:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            return

        target_host, target_port = host_port[0], int(host_port[1])

        # Check if target host should be blocked
        if self._should_block_domain(target_host):
            self._blocked_count += 1
            logger.info(f"[Quillon Proxy] BLOCKED CONNECT to {target_host}:{target_port}")
            writer.write(b"HTTP/1.1 502 Proxy Blocked\r\n\r\n")
            await writer.drain()
            return

        # Connect to target
        try:
            target_reader, target_writer = await asyncio.open_connection(target_host, target_port)
        except Exception as e:
            logger.error(f"[Quillon Proxy] Failed to connect to {target_host}:{target_port}: {e}")
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            return

        # Send 200 Connection Established
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()

        # Set up SSL on the target connection
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        try:
            target_writer.transport.get_extra_info("socket")
            target_ssl = ssl_context.wrap_socket(
                target_writer.transport.get_extra_info("socket"),
                server_hostname=target_host,
            )
            target_reader = asyncio.StreamReader()
            target_protocol = asyncio.StreamReaderProtocol(target_reader)
            await asyncio.get_event_loop().connect_read_pipe(
                lambda: target_protocol, target_ssl
            )
        except Exception:
            pass  # Fall back to non-SSL if SSL fails

        # Relay data bidirectionally
        await self._relay(reader, writer, target_reader, target_writer)

    async def _handle_http_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        method: str, url: str, version: str
    ) -> None:
        """Handle regular HTTP request."""
        # Parse URL
        parsed = urlparse(url)
        target_host = parsed.hostname or ""
        target_port = parsed.port or 80

        # Check if should block
        if self._should_block_domain(target_host):
            self._blocked_count += 1
            logger.info(f"[Quillon Proxy] BLOCKED {method} {url}")
            writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return

        self._allowed_count += 1

        # Read headers
        headers = await self._read_headers(reader)
        headers["Host"] = f"{target_host}:{target_port}" if target_port != 80 else target_host
        headers["Proxy-Connection"] = "close"

        # Connect to target
        try:
            target_reader, target_writer = await asyncio.open_connection(target_host, target_port)
        except Exception as e:
            logger.error(f"[Quillon Proxy] Failed to connect to {target_host}:{target_port}: {e}")
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            return

        # Forward request
        request_line = f"{method} {parsed.path or '/'} {version}\r\n"
        target_writer.write(request_line.encode())
        for k, v in headers.items():
            target_writer.write(f"{k}: {v}\r\n".encode())
        target_writer.write(b"\r\n")
        await target_writer.drain()

        # Read body if present
        content_length = int(headers.get("Content-Length", "0"))
        if content_length > 0:
            body = await reader.read(content_length)
            target_writer.write(body)
            await target_writer.drain()

        # Relay response
        await self._relay(target_reader, target_writer, reader, writer)

    def _should_block_domain(self, host: str) -> bool:
        """Check if a domain should be blocked."""
        host = host.lower()

        # Check exact match and parent domains
        parts = host.split(".")
        for i in range(len(parts)):
            domain = ".".join(parts[i:])
            if domain in self._blocker._blocked:
                return True

        # Check Ghostery engine
        try:
            if self._blocker._ghostery and self._blocker._ghostery.ready:
                result = self._blocker._ghostery.match(f"https://{host}/", "", "other")
                if result.get("match"):
                    return True
        except Exception:
            pass

        return False

    async def _read_headers(self, reader: asyncio.StreamReader) -> dict:
        """Read HTTP headers."""
        headers = {}
        while True:
            line = await reader.readline()
            if not line or line in (b"\r\n", b"\n"):
                break
            line = line.decode("utf-8", errors="ignore").strip()
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip()] = v.strip()
        return headers

    async def _relay(
        self,
        src_reader: asyncio.StreamReader,
        src_writer: asyncio.StreamWriter,
        dst_reader: asyncio.StreamReader,
        dst_writer: asyncio.StreamWriter,
    ) -> None:
        """Relay data between two streams bidirectionally."""
        async def copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await src.read(8192)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except Exception:
                pass
            finally:
                dst.close()
                await dst.wait_closed()

        await asyncio.gather(
            copy(src_reader, dst_writer),
            copy(dst_reader, src_writer),
        )


async def run_proxy(blocker: URLBlocker, host: str = "127.0.0.1", port: int = 8080) -> QuillonProxy:
    """Create and start Quillon proxy."""
    proxy = QuillonProxy(blocker, host, port)
    await proxy.start()
    return proxy