"""The CORS guard on the local server.

This server is reachable by anything running on the machine, and by any
page the user visits, so "did that request really come from Quillon's own
page" is a security question and not a formality. The guard had no test
at all, which is why it is easy to weaken by accident.

These tests must NOT set QUILLON_TEST: that variable disables the guard
entirely, and a test that turns the guard off cannot then claim to have
verified it. The bypass itself is pinned separately below, with the
environment restored afterwards.
"""

import asyncio
import os
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "quillon" / "templates"

try:
    from aiohttp import ClientSession, CookieJar
    from quillon.core.server import QuillonHBServer
except (ImportError, ModuleNotFoundError):
    ClientSession = None
    QuillonHBServer = None

# Origins the guard must refuse. None of these is Quillon's own page.
FOREIGN_ORIGINS = (
    "http://evil.example",
    "https://attacker.test",
    # Local hostname but the wrong port.
    "http://127.0.0.1:9999",
    "null",
)

# The origin Quillon's own page actually sends.
LOCAL_ORIGIN = "http://127.0.0.1:8889"


class _FakeWindow:
    def __init__(self):
        self.calls = []

    @staticmethod
    def _safe_action_target(value: str) -> bool:
        value = (value or "").strip()
        return value.startswith(("http://", "https://")) or value == "about:blank"

    def new_tab(self, url=None, private=False):
        self.calls.append(("new_tab", url))

    def go_back(self):
        self.calls.append(("go_back",))

    def go_forward(self):
        self.calls.append(("go_forward",))

    def reload(self):
        self.calls.append(("reload",))


@unittest.skipIf(QuillonHBServer is None, "project runtime dependencies are unavailable")
class CorsGuardTests(unittest.TestCase):
    def setUp(self):
        # QUILLON_TEST must not be set while the guard is under test, or
        # every assertion below is measuring the bypass instead.
        self._had_flag = "QUILLON_TEST" in os.environ
        self._prev_flag = os.environ.get("QUILLON_TEST")
        os.environ.pop("QUILLON_TEST", None)

    def tearDown(self):
        if self._had_flag:
            os.environ["QUILLON_TEST"] = self._prev_flag
        else:
            os.environ.pop("QUILLON_TEST", None)

    def _serve(self, body, prime_token=True):
        async def run():
            server = QuillonHBServer(TEMPLATES_DIR, port=0)
            window = _FakeWindow()
            server._run_on_qt = lambda fn, timeout=5.0: fn(window)
            await server.start()
            port = server._site._server.sockets[0].getsockname()[1]
            try:
                async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as session:
                    if prime_token:
                        # A real page load is what hands the document its
                        # per-session token cookie; the page's own fetches
                        # then carry it. Prime it the same way, unless the
                        # test is proving that a request without it is
                        # refused.
                        await session.get(f"http://127.0.0.1:{port}/health")
                    result = await body(session, port, window)
            finally:
                await server.stop()
            return result

        return asyncio.run(run())

    def test_post_without_a_token_is_refused(self):
        """A caller that never fetched the page holds no token."""
        async def body(session, port, window):
            async with session.post(
                f"http://127.0.0.1:{port}/api/ui/newTab",
                data='{"url": "https://example.com"}',
                headers={"Content-Type": "application/json"},
            ) as response:
                return response.status, (await response.json()), list(window.calls)

        status, payload, calls = self._serve(body, prime_token=False)
        self.assertEqual(status, 403, "a POST with no token must be refused")
        self.assertEqual(window_calls(calls), 0, "the handler must not have run")

    def test_post_from_a_foreign_origin_is_refused(self):
        for origin in FOREIGN_ORIGINS:
            with self.subTest(origin=origin):
                async def body(session, port, window, origin=origin):
                    async with session.post(
                        f"http://127.0.0.1:{port}/api/ui/newTab",
                        data='{"url": "https://example.com"}',
                        headers={
                            "Content-Type": "application/json",
                            "Origin": origin,
                        },
                    ) as response:
                        return response.status, list(window.calls)

                status, calls = self._serve(body)
                self.assertEqual(status, 403, f"Origin {origin} must be refused")
                self.assertEqual(window_calls(calls), 0)

    def test_post_from_the_local_origin_is_allowed(self):
        async def body(session, port, window):
            async with session.post(
                f"http://127.0.0.1:{port}/api/ui/newTab",
                data='{"url": "https://example.com"}',
                headers={
                    "Content-Type": "application/json",
                    "Origin": LOCAL_ORIGIN,
                },
            ) as response:
                return response.status, list(window.calls)

        status, calls = self._serve(body)
        self.assertEqual(status, 200)
        self.assertIn(("new_tab", "https://example.com"), calls)

    def test_state_changing_request_with_token_needs_no_origin(self):
        """The token, not the (forgeable) Origin, authorises the page."""
        async def body(session, port, window):
            async with session.post(
                f"http://127.0.0.1:{port}/api/ui/newTab",
                data='{"url": "https://example.com"}',
                headers={"Content-Type": "application/json"},
            ) as response:
                return response.status, list(window.calls)

        status, calls = self._serve(body)  # primes the token cookie
        self.assertEqual(status, 200)
        self.assertIn(("new_tab", "https://example.com"), calls)

    def test_a_forged_token_is_refused(self):
        async def body(session, port, window):
            async with session.post(
                f"http://127.0.0.1:{port}/api/ui/newTab",
                data='{"url": "https://example.com"}',
                headers={"Content-Type": "application/json"},
                cookies={"quillon_token": "not-the-real-token"},
            ) as response:
                return response.status, list(window.calls)

        status, calls = self._serve(body, prime_token=False)
        self.assertEqual(status, 403)
        self.assertEqual(window_calls(calls), 0)

    def test_token_cookie_is_httponly_and_samesite_strict(self):
        async def body(session, port, window):
            async with session.get(f"http://127.0.0.1:{port}/health") as response:
                return response.headers.getall("Set-Cookie", [])

        cookies = self._serve(body, prime_token=False)
        self.assertTrue(cookies, "a page load must issue a token cookie")
        header = " ".join(cookies).lower()
        self.assertIn("quillon_token=", header)
        self.assertIn("httponly", header)
        self.assertIn("samesite=strict", header)

    def test_state_changing_get_without_a_token_is_refused(self):
        """/_quillon/* changes state even though it is a GET."""
        async def body(session, port, window):
            results = {}
            for name in ("goBack", "goForward", "reload"):
                async with session.get(
                    f"http://127.0.0.1:{port}/_quillon/{name}"
                ) as response:
                    results[name] = response.status
            return results, list(window.calls)

        results, calls = self._serve(body, prime_token=False)
        for name, status in results.items():
            self.assertEqual(status, 403, f"/_quillon/{name} with no token must be refused")
        self.assertEqual(window_calls(calls), 0)


    def test_spoofed_host_header_cannot_certify_a_local_api_request(self):
        """A caller must not be able to declare itself local.

        The guard used to treat a request as local when its Host header
        matched the bound address, but the caller supplies that header, so a
        forged Host skipped the origin check. Authorisation is now the
        per-session token, which a forged Host does not carry.
        """
        for host in ("127.0.0.1:8889", "127.0.0.1:8888", "localhost:8889"):
            with self.subTest(host=host):
                async def body(session, port, window, host=host):
                    async with session.post(
                        f"http://127.0.0.1:{port}/api/ui/newTab",
                        data='{"url": "https://example.com"}',
                        headers={"Content-Type": "application/json", "Host": host},
                    ) as response:
                        return response.status, list(window.calls)

                status, calls = self._serve(body, prime_token=False)
                self.assertEqual(
                    status, 403,
                    f"Host: {host} was accepted as proof of locality",
                )
                self.assertEqual(window_calls(calls), 0, "the handler must not have run")

    def test_cross_site_request_cannot_drive_search(self):
        """/search is a GET with no Origin, but it writes the query into
        the user's history and fans out to every provider, so any page
        could have used <img src="http://127.0.0.1:8889/search?q=...">.
        A browser marks that request cross-site and a remote page cannot
        forge the header."""
        async def body(session, port, window):
            out = {}
            for path in ("/search?q=forged+entry", "/api/search/progress?request_id=x&q=y"):
                async with session.get(
                    f"http://127.0.0.1:{port}{path}",
                    headers={"Sec-Fetch-Site": "cross-site"},
                ) as response:
                    out[path] = response.status
            return out

        for path, status in self._serve(body).items():
            self.assertEqual(status, 403, f"{path} was reachable cross-site")

    def test_same_origin_search_still_works(self):
        """A same-origin fetch from Quillon's own page sends no Origin, so
        requiring one would lock the page out of its own search."""
        async def body(session, port, window):
            out = {}
            for path in ("/search?q=hello", "/api/search/progress?request_id=a&q=b"):
                async with session.get(
                    f"http://127.0.0.1:{port}{path}",
                    headers={"Sec-Fetch-Site": "same-origin"},
                ) as response:
                    out[path] = response.status
            return out

        for path, status in self._serve(body).items():
            self.assertEqual(status, 200, f"{path} broke for the page itself")

    def test_read_only_routes_still_work_without_origin(self):
        """The guard must not break the page loading itself."""
        async def body(session, port, window):
            async with session.get(f"http://127.0.0.1:{port}/") as response:
                home = response.status
            async with session.get(f"http://127.0.0.1:{port}/health") as response:
                health = response.status
            return home, health

        home, health = self._serve(body)
        self.assertEqual(home, 200, "the home page must load without an Origin")
        self.assertEqual(health, 200)

    def test_https_origin_on_the_local_host_port_is_currently_accepted(self):
        """Documents a policy question rather than asserting an answer.

        _is_local_origin accepts scheme in ("http", "https"), so
        https://127.0.0.1:8889 is trusted even though Quillon's own page is
        served over plain http on 8889. Those are different origins, so
        this looks like an over-broad allowance. It is low severity: a
        remote page cannot forge an Origin header, and nothing is served
        over https on that port today.

        This test pins what the code does. Narrowing the scheme list is a
        security policy decision, not a bug fix, so it is left for the
        owner rather than changed quietly here.
        """
        async def body(session, port, window):
            async with session.post(
                f"http://127.0.0.1:{port}/api/ui/newTab",
                data='{"url": "https://example.com"}',
                headers={
                    "Content-Type": "application/json",
                    "Origin": "https://127.0.0.1:8889",
                },
            ) as response:
                return response.status

        self.assertEqual(self._serve(body), 200)

    def test_test_flag_bypasses_the_guard_and_is_restored_afterwards(self):
        """Pin the bypass so it is deliberate, and prove the guard is
        genuinely active by running the same request with it off."""
        async def body(session, port, window):
            async with session.post(
                f"http://127.0.0.1:{port}/api/ui/newTab",
                data='{"url": "https://example.com"}',
                headers={"Content-Type": "application/json"},
            ) as response:
                return response.status

        blocked = self._serve(body, prime_token=False)
        self.assertEqual(blocked, 403)

        os.environ["QUILLON_TEST"] = "1"
        try:
            allowed = self._serve(body, prime_token=False)
        finally:
            os.environ.pop("QUILLON_TEST", None)
        self.assertEqual(allowed, 200, "QUILLON_TEST is documented to bypass the guard")

        # And the guard is still on again afterwards.
        blocked_again = self._serve(body, prime_token=False)
        self.assertEqual(blocked_again, 403)


def window_calls(calls):
    return len(calls)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(QuillonHBServer is None, "project runtime dependencies are unavailable")
class TrustBoundaryTests(unittest.TestCase):
    """Port 8888 is the SearXNG container, not Quillon."""

    def test_searxng_port_is_not_a_trusted_origin(self):
        self.assertTrue(QuillonHBServer._is_local_origin("http://127.0.0.1:8889"))
        self.assertTrue(QuillonHBServer._is_local_origin("http://localhost:8889"))
        for origin in ("http://127.0.0.1:8888", "http://localhost:8888"):
            with self.subTest(origin=origin):
                self.assertFalse(
                    QuillonHBServer._is_local_origin(origin),
                    "SearXNG is a separate network-facing service and must not "
                    "be promoted to a trusted Quillon origin",
                )


@unittest.skipIf(QuillonHBServer is None, "project runtime dependencies are unavailable")
class QtHopDoesNotBlockTheLoopTests(unittest.TestCase):
    """_run_on_qt blocks on a threading.Event. Called from an async handler
    it froze the whole server for up to 5s per request."""

    def test_server_stays_responsive_while_qt_is_blocked(self):
        import time

        async def run():
            server = QuillonHBServer(TEMPLATES_DIR, port=0)
            server._run_on_qt = lambda fn, timeout=5.0: (time.sleep(2.0), fn(None))[1]
            await server.start()
            port = server._site._server.sockets[0].getsockname()[1]
            try:
                async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as session:
                    # Prime the token cookie so the POSTs actually reach the
                    # Qt hop this test is about, instead of being refused.
                    await session.get(f"http://127.0.0.1:{port}/health")
                    headers = {"Origin": LOCAL_ORIGIN, "Content-Type": "application/json"}

                    async def slow_post():
                        async with session.post(
                            f"http://127.0.0.1:{port}/api/ui/closeTab",
                            data='{"index": 0}', headers=headers,
                        ) as response:
                            return response.status

                    await asyncio.gather(slow_post(), slow_post())
                    started = time.monotonic()
                    async with session.get(f"http://127.0.0.1:{port}/health") as response:
                        body = await response.text()
                    return time.monotonic() - started, body.strip()
            finally:
                await server.stop()

        elapsed, body = asyncio.run(run())
        self.assertIn("OK", body)
        self.assertLess(
            elapsed, 0.5,
            "a blocked Qt hop froze the event loop; run_in_executor must be used",
        )


@unittest.skipIf(QuillonHBServer is None, "project runtime dependencies are unavailable")
class SecurityHeaderTests(unittest.TestCase):
    """Every response must carry the hardening headers.

    They are cheap defence in depth: without them a page the user visits
    could frame the UI, sniff a response's declared type, read a Quillon
    resource cross-origin, or learn the query-bearing local URL as a
    referrer. The headers must also survive a refused request, not just a
    served one.
    """

    def _serve(self, body):
        async def run():
            server = QuillonHBServer(TEMPLATES_DIR, port=0)
            window = _FakeWindow()
            server._run_on_qt = lambda fn, timeout=5.0: fn(window)
            await server.start()
            port = server._site._server.sockets[0].getsockname()[1]
            try:
                async with ClientSession() as session:
                    return await body(session, port)
            finally:
                await server.stop()

        return asyncio.run(run())

    def _assert_hardened(self, headers):
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("Cross-Origin-Resource-Policy"), "same-origin")
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_served_response_carries_security_headers(self):
        async def body(session, port):
            async with session.get(f"http://127.0.0.1:{port}/health") as response:
                return response.status, dict(response.headers)

        status, headers = self._serve(body)
        self.assertEqual(status, 200)
        self._assert_hardened(headers)

    def test_refused_response_carries_security_headers(self):
        async def body(session, port):
            async with session.post(
                f"http://127.0.0.1:{port}/api/ui/newTab",
                data='{"url": "https://example.com"}',
                headers={
                    "Origin": "http://evil.example",
                    "Content-Type": "application/json",
                },
            ) as response:
                return response.status, dict(response.headers)

        status, headers = self._serve(body)
        self.assertEqual(status, 403)
        self._assert_hardened(headers)

    def test_static_assets_stay_cacheable(self):
        """no-store is scoped away from /static so the large icon is cached."""
        async def body(session, port):
            async with session.get(
                f"http://127.0.0.1:{port}/static/quillon-home-logo.png"
            ) as response:
                return response.status, dict(response.headers)

        status, headers = self._serve(body)
        self.assertEqual(status, 200)
        self.assertNotIn("no-store", headers.get("Cache-Control", ""))
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
