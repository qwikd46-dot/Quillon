import asyncio
import json
import unittest
from pathlib import Path


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "bfsb" / "templates"

try:
    from aiohttp import ClientSession
    from bfsb.core.server import BFSHBServer
except (ImportError, ModuleNotFoundError):
    ClientSession = None
    BFSHBServer = None


class _FakeWindow:
    def __init__(self):
        self.opened = []

    @staticmethod
    def _safe_action_target(value: str) -> bool:
        value = value.strip()
        return value.startswith(("http://", "https://")) or value == "about:blank"

    def new_tab(self, url=None, private=False):
        self.opened.append(url)


class NewTabUrlTests(unittest.TestCase):
    @unittest.skipIf(BFSHBServer is None, "project runtime dependencies are unavailable")
    def test_post_opens_validated_url_and_rejects_other_schemes(self):
        async def run():
            server = BFSHBServer(TEMPLATES_DIR, port=0)
            window = _FakeWindow()
            server._run_on_qt = lambda fn, timeout=5.0: fn(window)
            await server.start()
            port = server._site._server.sockets[0].getsockname()[1]
            try:
                async with ClientSession() as session:
                    async def post(payload):
                        # The server binds an ephemeral port in tests, which is
                        # not a "local origin", so the CORS guard would 403 a
                        # request without an Origin. The real page is served
                        # from 8889 and posts to itself, so send that.
                        async with session.post(
                            f"http://127.0.0.1:{port}/api/ui/newTab",
                            data=json.dumps(payload),
                            headers={
                                "Content-Type": "application/json",
                                "Origin": "http://127.0.0.1:8889",
                            },
                        ) as response:
                            return await response.json()

                    opened = await post({"url": "https://youtube.com"})
                    blank = await post({})
                    bad_scheme = await post({"url": "file:///etc/passwd"})
                    bad_js = await post({"url": "javascript:alert(1)"})
            finally:
                await server.stop()
            return opened, blank, bad_scheme, bad_js, window.opened

        opened, blank, bad_scheme, bad_js, urls = asyncio.run(run())
        self.assertTrue(opened["ok"])
        self.assertTrue(blank["ok"])
        self.assertFalse(bad_scheme["ok"])
        self.assertFalse(bad_js["ok"])
        self.assertEqual(urls, ["https://youtube.com", None])

    @unittest.skipIf(BFSHBServer is None, "project runtime dependencies are unavailable")
    def test_get_route_rejects_non_http_scheme(self):
        async def run():
            server = BFSHBServer(TEMPLATES_DIR, port=0)
            window = _FakeWindow()
            server._run_on_qt = lambda fn, timeout=5.0: fn(window)
            await server.start()
            port = server._site._server.sockets[0].getsockname()[1]
            try:
                # /_bfsb/* counts as state-changing, so the CORS guard wants
                # an Origin here too.
                headers = {"Origin": "http://127.0.0.1:8889"}
                async with ClientSession() as session:
                    async with session.get(
                        f"http://127.0.0.1:{port}/_bfsb/newTab?url=file:///etc/passwd",
                        headers=headers,
                    ) as response:
                        blocked = await response.json()
                    async with session.get(
                        f"http://127.0.0.1:{port}/_bfsb/newTab?url=https://youtube.com",
                        headers=headers,
                    ) as response:
                        allowed = await response.json()
            finally:
                await server.stop()
            return blocked, allowed, window.opened

        blocked, allowed, urls = asyncio.run(run())
        self.assertFalse(blocked["ok"])
        self.assertTrue(allowed["ok"])
        self.assertEqual(urls, ["https://youtube.com"])


if __name__ == "__main__":
    unittest.main()
