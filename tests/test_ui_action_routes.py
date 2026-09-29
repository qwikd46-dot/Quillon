"""In-page actions must reach the window, not just a scheme that cannot.

The UI document is served over http://127.0.0.1:8889, and it talks to the
window through fetch(). It cannot reach a quillon:// URL: Chromium refuses
to load a local-scheme resource from a standard-scheme origin, so the
fetch fails with "Not allowed to load local resource" and the action is
silently dropped. Tab reordering and the sidebar collapse state were both
wired to the scheme alone and neither did anything.

The convention the rest of the file already follows is HTTP first with the
scheme as a fallback. These assert that convention holds, because a new
action wired the wrong way is otherwise invisible: no error, no test, no
user-visible clue beyond the feature quietly not working.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
TEMPLATE = ROOT / "quillon" / "templates" / "quillon_combined.html"
SERVER = ROOT / "quillon" / "core" / "server.py"


def template_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def server_text() -> str:
    return SERVER.read_text(encoding="utf-8")


def registered_routes() -> set[str]:
    return set(
        re.findall(r"app\.router\.add_(?:post|get)\('([^']+)'", server_text())
    )


def scheme_dispatch_names() -> set[str]:
    block = re.search(r"_TEST_ACTIONS[^=]*=\s*\{(.*?)\}", server_text(), re.S)
    if not block:
        return set()
    return set(re.findall(r'"(\w+)"\s*:', block.group(1)))


class InPageActionRoutingTests(unittest.TestCase):
    def test_template_exists(self):
        self.assertTrue(TEMPLATE.exists(), f"missing {TEMPLATE}")

    def test_reordering_posts_to_an_http_route(self):
        """The bug: reorderTabs went to the scheme only."""
        html = template_text()
        self.assertIn("'/api/ui/reorderTabs'", html,
                      "tab reordering is not posted to the UI server")
        self.assertIn("htmlUiAction", html)

    def test_sidebar_collapse_posts_to_an_http_route(self):
        html = template_text()
        self.assertIn("'/api/ui/sidebarCollapsed'", html,
                      "sidebar collapse is not posted to the UI server")

    def test_those_routes_are_actually_registered(self):
        routes = registered_routes()
        for route in ("/api/ui/reorderTabs", "/api/ui/sidebarCollapsed"):
            self.assertIn(route, routes, f"{route} is called but not registered")

    def test_those_names_are_in_the_scheme_fallback_map(self):
        """The fallback has to exist too, or the HTTP path is the only one."""
        names = scheme_dispatch_names()
        for name in ("reorderTabs", "sidebarCollapsed"):
            self.assertIn(name, names, f"{name} has no scheme fallback handler")

    def test_no_action_is_left_depending_on_the_scheme_alone(self):
        """A bare quillonAction('quillon://...') is the defect, not the fix.

        The only acceptable direct uses are the fallbacks inside
        htmlUiAction/httpNavAction, and the address-bar navigate, which has
        a documented frame-load fallback of its own.
        """
        # Actions that legitimately call the scheme directly, each because
        # it is a fallback after an HTTP attempt that already failed, or
        # because it has a separate fallback of its own. Adding a name here
        # means asserting it really does try HTTP first.
        ALLOWED = {
            "navigate": "address bar; falls back to a direct frame load",
            "switchTab": "fallback inside the /api/ui/switchTab try block",
        }
        html = template_text()
        offenders = []
        # Match the whole argument expression, not just the first string
        # literal: most call sites build the query with + concatenation,
        # and a regex that stops at the first closing quote silently
        # missed every one of them.
        for match in re.finditer(r"quillonAction\((.+?)\)\s*[;\n]", html):
            line = html[: match.start()].count("\n") + 1
            argument = " ".join(match.group(1).split())
            if "quillon://" not in argument:
                continue
            name = re.search(r"quillon://(\w+)", argument).group(1)
            if name in ALLOWED:
                continue
            offenders.append((line, argument[:70]))
        self.assertEqual(
            offenders, [],
            "these actions use the scheme with no HTTP route, so they are "
            "silently dropped: "
            + ", ".join(f"line {n}: {u}" for n, u in offenders),
        )

    def test_every_registered_ui_route_has_a_handler(self):
        handlers = set(re.findall(r"async def (handle_\w+)", server_text()))
        for route, name in re.findall(
            r"app\.router\.add_(?:post|get)\('([^']+)',\s*self\.(\w+)", server_text()
        ):
            self.assertIn(
                name, handlers, f"{route} is registered to {name}, which is not defined"
            )


if __name__ == "__main__":
    unittest.main()
