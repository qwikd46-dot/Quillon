"""Tiny CDP client for BFSB.

Talks directly to QtWebEngine's embedded Chromium via Chrome DevTools Protocol.
No Playwright, no Selenium. We just need:
  - click/hover/move on screen coordinates (via ydotool subprocess)
  - JS eval + DOM queries (via CDP Runtime / DOM)
  - screenshots (CDP Page.captureScreenshot + Input.dispatchMouseEvent)
  - console error collection (CDP Runtime.consoleAPICalled)

Usage:
    from bfsb_cdp import CDP
    async with CDP() as cdp:
        await cdp.eval("document.title")
        await cdp.screenshot("/tmp/shot.png")
        await cdp.click("button#newTabBtn")
        await cdp.hover(".bfsb-tab")
        errors = cdp.errors
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

import websockets


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


@dataclass
class Page:
    ws_url: str
    title: str
    url: str


class CDP:
    """One CDP session against the BFSB embedded browser's main target."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9222):
        self.host = host
        self.port = port
        self._ws: Any = None
        self._msg_id: int = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: Any = None
        self._pages: list[Page] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.page_logs: list[tuple[str, str]] = []
        self._current_page_idx: int = 0
        self._console_handler: Any = None
        self._log_domain_missing: bool = False

    # --- lifecycle ----------------------------------------------------

    async def __aenter__(self) -> "CDP":
        # discover the default page (the BFSB embedded view shows up as the
        # first page target; ignore extension/background targets)
        url = f"http://{self.host}:{self.port}/json"
        pages = _fetch_json(url)
        self._pages = [
            Page(p["webSocketDebuggerUrl"], p.get("title", ""), p.get("url", ""))
            for p in pages
            if p.get("type") == "page"
        ]
        if not self._pages:
            raise RuntimeError(f"No page targets found at {url}")

        await self._connect_current()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    @property
    def current_page(self) -> Page:
        return self._pages[self._current_page_idx]

    async def switch_page(self, idx: int) -> None:
        if not (0 <= idx < len(self._pages)):
            raise IndexError(f"page idx {idx} out of range (have {len(self._pages)})")
        # tear down old connection
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except Exception:
                pass
        if self._ws:
            await self._ws.close()
        self._current_page_idx = idx
        await self._connect_current()

    async def refresh_pages(self) -> None:
        """Re-fetch the list of CDP page targets from the browser.

        QtWebEngine registers a new page target every time BFSB opens
        a new QWebEngineView (tab). The initial snapshot in __aenter__
        only sees the first view; we re-poll here so tab-management
        checks can see new tabs.
        """
        import urllib.request, json
        try:
            with urllib.request.urlopen(
                f"http://{self.host}:{self.port}/json", timeout=2
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return
        # Append any new page targets we haven't seen.
        existing_ws = {p.ws_url for p in self._pages}
        for entry in data:
            if entry.get("type") != "page":
                continue
            if entry["webSocketDebuggerUrl"] in existing_ws:
                continue
            self._pages.append(Page(
                entry["webSocketDebuggerUrl"],
                entry.get("title", ""),
                entry.get("url", ""),
            ))

    async def _connect_current(self) -> None:
        page = self.current_page
        self._ws = await websockets.connect(
            page.ws_url, max_size=32 * 1024 * 1024
        )
        self._pending.clear()
        self._reader_task = asyncio.create_task(self._reader_loop())
        # Runtime.enable gives us Runtime.consoleAPICalled + .exceptionThrown.
        # We deliberately do NOT enable the Log domain: Qt WebEngine's CDP
        # implementation appears to stop acking commands under a backlog
        # of Log.entryAdded events, hanging all subsequent sends.
        await self._send("Runtime.enable")
        try:
            await self._send("Page.enable")
        except Exception:
            pass  # Page domain optional

    # --- reader -------------------------------------------------------

    async def _reader_loop(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                self._dbg(msg)
                mid = msg.get("id")
                if mid is not None and mid in self._pending:
                    fut = self._pending.pop(mid)
                    if "error" in msg:
                        fut.set_exception(RuntimeError(msg["error"]))
                    else:
                        fut.set_result(msg.get("result", {}))
                    continue
                method = msg.get("method")
                if not method:
                    continue
                if method == "Runtime.consoleAPICalled":
                    params = msg["params"]
                    text = " ".join(str(a.get("value", a)) for a in params.get("args", []))
                    level = params.get("type", "log")
                    self.page_logs.append((level, text))
                    if level == "error":
                        self.errors.append(text)
                    elif level == "warning":
                        self.warnings.append(text)
                elif method in ("Runtime.exceptionThrown", "Log.entryAdded"):
                    # surface uncaught exceptions explicitly
                    if method == "Runtime.exceptionThrown":
                        exc = msg["params"].get("exceptionDetails", {})
                        text = exc.get("text", "") + " " + (exc.get("exception", {}).get("description", ""))
                        self.errors.append(f"uncaught: {text.strip()}")
                    else:
                        entry = msg["params"].get("entry", {})
                        if entry.get("level") == "error":
                            self.errors.append(entry.get("text", ""))
        except websockets.ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    # --- send ---------------------------------------------------------

    def _dbg(self, msg: dict) -> None:
        if not os.environ.get("BFSB_CDP_DEBUG"):
            return
        mid = msg.get("id")
        method = msg.get("method")
        if mid is not None:
            print(f"[cdp] ← id={mid}", file=sys.stderr, flush=True)
        elif method:
            print(f"[cdp] ← evt={method}", file=sys.stderr, flush=True)

    async def _send(self, method: str, params: dict | None = None) -> dict:
        self._msg_id += 1
        mid = self._msg_id
        payload = {"id": mid, "method": method, "params": params or {}}
        fut = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        if os.environ.get("BFSB_CDP_DEBUG"):
            print(f"[cdp] → id={mid} method={method}", file=sys.stderr, flush=True)
        await self._ws.send(json.dumps(payload))
        try:
            return await asyncio.wait_for(fut, timeout=15)
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            print(f"[cdp] TIMEOUT id={mid} method={method}", file=sys.stderr, flush=True)
            raise

    # --- high-level helpers -------------------------------------------

    async def eval(self, expr: str) -> Any:
        r = await self._send("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": True,
        })
        result = r.get("result", {})
        if "value" in result:
            return result["value"]
        if result.get("type") == "undefined":
            return None
        return result

    async def query_box(self, expr: str) -> tuple[float, float, float, float] | None:
        """Return {x,y,w,h} for the first element matching `expr`, or None."""
        js = (
            "(()=>{const el=document.querySelector(" + json.dumps(expr) + ");"
            "if(!el)return null;const r=el.getBoundingClientRect();"
            "return [r.x,r.y,r.width,r.height];})()"
        )
        res = await self.eval(js)
        if res is None:
            return None
        if isinstance(res, list) and len(res) == 4:
            return tuple(res)  # type: ignore
        return None

    async def screenshot(self, path: str) -> None:
        r = await self._send("Page.captureScreenshot", {"format": "png"})
        data = base64.b64decode(r["data"])
        with open(path, "wb") as f:
            f.write(data)

    async def wait_for(self, expr: str, timeout: float = 5.0) -> bool:
        """Poll `eval(expr-deemed-truthy)` until True or timeout."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                v = await self.eval(expr)
                if v:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.25)
        return False

    # --- input via CDP Input domain ------------------------------------

    async def _dispatch_mouse(self, mouse_type: str, x: float, y: float,
                              button: str = "left", click_count: int = 1) -> None:
        """mouse_type ∈ {mouseMoved, mousePressed, mouseReleased, mouseWheel}."""
        await self._send("Input.dispatchMouseEvent", {
            "type": mouse_type,
            "x": x, "y": y,
            "button": button,
            "buttons": 1 if button == "left" else 2,
            "clickCount": click_count,
        })

    async def click(self, expr: str) -> tuple[float, float]:
        """Find `expr`, hover its center, then click. Returns (cx, cy)."""
        box = await self.query_box(expr)
        if not box:
            raise RuntimeError(f"element not found: {expr}")
        cx = box[0] + box[2] / 2
        cy = box[1] + box[3] / 2
        await self._dispatch_mouse("mouseMoved", cx, cy)
        await asyncio.sleep(0.05)
        await self._dispatch_mouse("mousePressed", cx, cy, "left", 1)
        await asyncio.sleep(0.05)
        await self._dispatch_mouse("mouseReleased", cx, cy, "left", 1)
        return (cx, cy)

    async def hover(self, expr: str) -> tuple[float, float]:
        box = await self.query_box(expr)
        if not box:
            raise RuntimeError(f"element not found: {expr}")
        cx = box[0] + box[2] / 2
        cy = box[1] + box[3] / 2
        await self._dispatch_mouse("mouseMoved", cx, cy)
        return (cx, cy)

    async def click_at(self, x: float, y: float) -> None:
        await self._dispatch_mouse("mouseMoved", x, y)
        await self._dispatch_mouse("mousePressed", x, y, "left", 1)
        await self._dispatch_mouse("mouseReleased", x, y, "left", 1)

    async def hover_at(self, x: float, y: float) -> None:
        await self._dispatch_mouse("mouseMoved", x, y)

    async def keypress(self, key: str) -> None:
        """Send a single key. key can be CDP-compatible like 'Enter', 'Tab',
        or 'ctrl+d' which we expand to a chord."""
        keys = key.split("+")
        modifiers = [k for k in keys if k in ("ctrl", "alt", "shift", "meta")]
        main = keys[-1]
        mod_args = (
            sum(1 << i for i, m in enumerate(("alt", "ctrl", "meta", "shift"))
                if m in modifiers)
        )
        # key down with modifiers, then up without
        await self._send("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": main,
            "modifiers": mod_args,
        })
        await self._send("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": main,
            "modifiers": mod_args,
        })

    async def ydotool_click(self, x: float, y: float) -> None:
        """Fallback: drive ydotool directly. For native Qt chrome outside the
        embedded webview that CDP-Input can't reach. Coordinates are in the
        Xvfb coordinate space (1400x900)."""
        subprocess.run(
            ["ydotool", "mousemove", "-a", "--", str(int(x)), str(int(y))],
            check=False, timeout=5,
        )
        subprocess.run(
            ["ydotool", "click", "-D", "0", "0x110"],  # BTN_LEFT
            check=False, timeout=5,
        )

    async def ydotool_move(self, x: float, y: float) -> None:
        """Move the cursor (no click). Used for popover-hover tests."""
        subprocess.run(
            ["ydotool", "mousemove", "-a", "--", str(int(x)), str(int(y))],
            check=False, timeout=5,
        )

    # Keycode table — Linux input-event-codes. ydotool uses raw keycodes
    # because it has no concept of layouts. Only the keys we actually
    # press from the harness are listed.
    _YDOTOOL_KEYCODES: dict[str, int] = {
        # letters (lowercase = unshifted)
        "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34,
        "h": 35, "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49,
        "o": 24, "p": 25, "q": 16, "r": 19, "s": 31, "t": 20, "u": 22,
        "v": 47, "w": 17, "x": 45, "y": 21, "z": 44,
        # digits
        "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9,
        "9": 10, "0": 11,
        # common control keys
        "enter": 28, "return": 28, "tab": 15, "space": 57, "escape": 1,
        "esc": 1, "backspace": 14, "delete": 111, "home": 102, "end": 107,
        "pageup": 104, "pagedown": 109,
        "up": 103, "down": 108, "left": 105, "right": 106,
        # modifiers (left-side, conventional)
        "ctrl": 29, "leftctrl": 29,
        "shift": 42, "leftshift": 42,
        "alt": 56, "leftalt": 56,
        "meta": 125, "super": 125,
    }

    async def ydotool_keypress(self, key: str) -> None:
        """Press a key (or chord) at the X11 server level via ydotool.

        Use this for Qt QShortcut bindings that DON'T receive CDP
        Input.dispatchKeyEvent events (those only reach the focused
        webview, not the surrounding QMainWindow). Accepts the same
        "ctrl+t", "ctrl+shift+Tab" notation as cdp.keypress.

        Internally translates each named key to its Linux input-event
        keycode; modifier keys are pressed/released around the main key.
        """
        tokens = [k.strip().lower() for k in key.split("+") if k.strip()]
        if not tokens:
            return
        # Resolve keycodes
        codes = []
        for t in tokens:
            if t not in self._YDOTOOL_KEYCODES:
                raise ValueError(f"ydotool_keypress: unknown key {t!r}")
            codes.append(self._YDOTOOL_KEYCODES[t])
        mod_codes = codes[:-1]
        main_code = codes[-1]
        # Press modifiers, press main, release main, release modifiers
        args: list[str] = ["ydotool", "key", "-d", "5"]
        for m in mod_codes:
            args.append(str(m) + ":1")  # down
        args.append(str(main_code) + ":1")  # main down
        args.append(str(main_code) + ":0")  # main up
        for m in reversed(mod_codes):
            args.append(str(m) + ":0")  # up
        subprocess.run(args, check=False, timeout=5)

    async def test_action(self, name: str, arg: Optional[str] = None) -> bool:
        """Trigger a BFSB test action over the local HTTP server.

        Calls ``GET http://127.0.0.1:8889/test-action/{name}?arg={arg}``,
        which the BFSB server dispatches to the MainWindow. The route
        is only registered when BFSB_TEST=1 is in the server's env.
        Returns True on HTTP 200, False otherwise. Faster and more
        reliable than ydotool under Xvfb.

        Available actions: newTab, closeTab, switchTab, reorderTabs,
        toggleSideOverlay, showMainMenu, hoverBookmarks, hoverHistory,
        closePopovers.
        """
        import urllib.request
        url = f"http://127.0.0.1:8889/test-action/{name}"
        if arg is not None:
            url += f"?arg={arg}"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def test_state(self) -> Optional[dict]:
        """Fetch the Qt-side UI state snapshot from /test-state.

        Returns a dict with keys ``menu_open``, ``bookmark_popover_open``,
        ``history_popover_open``, ``sidebar_open``, ``bookmark_count``,
        ``history_count``, ``view_count`` — all read on the Qt main
        thread inside the BFSB process, so this is the source of truth
        for what the user would see on screen.

        Returns None if the endpoint isn't available (BFSB_TEST not
        set, or the server didn't come up). Use ``None`` to mean
        "couldn't read state" — every consumer should treat None as
        a soft fail, not a verdict of "closed".
        """
        import urllib.request
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:8889/test-state", timeout=3
            ) as resp:
                if resp.status != 200:
                    return None
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None


# --- tiny CLI for ad-hoc checks -----------------------------------

async def _cli() -> None:
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--screenshot", default=None)
    ap.add_argument("--eval", default=None)
    ap.add_argument("--click", default=None)
    ap.add_argument("--hover", default=None)
    args = ap.parse_args()
    async with CDP() as c:
        if args.eval:
            print(await c.eval(args.eval))
        if args.click:
            await c.click(args.click)
            print(f"clicked {args.click}")
        if args.hover:
            await c.hover(args.hover)
            print(f"hovered {args.hover}")
        if args.screenshot:
            await c.screenshot(args.screenshot)
            print(f"saved {args.screenshot}")
        if c.errors:
            print("CONSOLE ERRORS:\n" + "\n".join(c.errors), file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(_cli())
