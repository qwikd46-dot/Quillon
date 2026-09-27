import asyncio
import unittest
from pathlib import Path


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "bfsb" / "templates"

try:
    from aiohttp import ClientSession
    from bfsb.core.server import BFSHBServer
except (ImportError, ModuleNotFoundError):
    ClientSession = None
    BFSHBServer = None


class SearchProgressTests(unittest.TestCase):
    @unittest.skipIf(BFSHBServer is None, "project runtime dependencies are unavailable")
    def test_json_and_sse_share_request_lifecycle(self):
        async def run():
            from bfsb.core import server as server_module

            original = server_module.search_async

            async def fake_search(query, progress=None):
                if progress:
                    progress({"event": "engine_start", "source": "test"})
                    progress({"event": "engine_done", "source": "test", "ms": 1, "n": 1})
                return server_module.AggregateResponse(
                    query=query,
                    results=[server_module.Result("https://example.com", "Example", "Snippet", "test")],
                )

            server_module.search_async = fake_search
            server = BFSHBServer(TEMPLATES_DIR, port=0)
            server._bookmarks_json = lambda: "[]"
            await server.start()
            port = server._site._server.sockets[0].getsockname()[1]
            try:
                async with ClientSession() as session:
                    async def events():
                        async with session.get(f"http://127.0.0.1:{port}/api/search/progress?q=hello&request_id=verify") as response:
                            return await response.text()
                    task = asyncio.create_task(events())
                    await asyncio.sleep(0.02)
                    async with session.get(f"http://127.0.0.1:{port}/search?q=hello&format=json&request_id=verify") as response:
                        payload = await response.json()
                    text = await asyncio.wait_for(task, 5)
            finally:
                await server.stop()
                server_module.search_async = original
            names = [line[7:] for line in text.splitlines() if line.startswith("event: ")]
            self.assertEqual(payload["count"], 1)
            self.assertEqual(names, ["submitted", "engine_start", "engine_done", "done"])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
