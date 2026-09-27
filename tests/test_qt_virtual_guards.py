"""Reimplemented Qt virtual methods must not abort the process.

In PyQt6 an exception raised out of a reimplemented virtual method calls
qFatal, which SIGABRTs the whole browser. Four such methods had no guard,
so a deleted view or a bad bookmark during navigation could take the app
down instead of failing one request.
"""

import unittest

try:
    import bfsb.core.webengine as webengine
except Exception:  # pragma: no cover
    webengine = None


def _explode(*args, **kwargs):
    raise RuntimeError("simulated deleted C++ object")


@unittest.skipIf(webengine is None, "webengine module unavailable")
class VirtualGuardTests(unittest.TestCase):
    def setUp(self):
        self._saved = []

    def _break(self, cls, name):
        self._saved.append((cls, name, getattr(cls, name + "_impl", None)))
        setattr(cls, name + "_impl", _explode)

    def tearDown(self):
        for cls, name, original in self._saved:
            setattr(cls, name + "_impl", original)

    def test_accept_navigation_request_refuses_instead_of_raising(self):
        self._break(webengine.BFSBPage, "acceptNavigationRequest")
        page = webengine.BFSBPage.__new__(webengine.BFSBPage)
        self.assertIs(
            webengine.BFSBPage.acceptNavigationRequest(page, None, 0, True), False
        )

    def test_intercept_request_returns_none(self):
        self._break(webengine.RequestInterceptor, "interceptRequest")
        self.assertIsNone(
            webengine.RequestInterceptor.interceptRequest(object(), None)
        )

    def test_create_window_returns_none(self):
        self._break(webengine.BFSBPage, "createWindow")
        page = webengine.BFSBPage.__new__(webengine.BFSBPage)
        self.assertIsNone(webengine.BFSBPage.createWindow(page, None))

    def test_every_risky_virtual_is_wrapped(self):
        for cls, name in (
            (webengine.RequestInterceptor, "interceptRequest"),
            (webengine.SafePage, "acceptNavigationRequest"),
            (webengine.BFSBPage, "acceptNavigationRequest"),
            (webengine.BFSBPage, "createWindow"),
        ):
            with self.subTest(cls=cls.__name__, name=name):
                self.assertEqual(
                    getattr(cls, name).__name__, "wrapper",
                    f"{cls.__name__}.{name} is unguarded and can abort the process",
                )


if __name__ == "__main__":
    unittest.main()
