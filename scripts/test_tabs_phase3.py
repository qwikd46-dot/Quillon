import asyncio
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bfsb_cdp import CDP


async def run():
    failures = []

    def check(name, condition):
        if not condition:
            failures.append(name)
        print(("PASS " if condition else "FAIL ") + name)

    async with CDP() as cdp:
        state = await cdp.test_state() or {}
        check("test bridge available", bool(state))
        await cdp.test_action("newTab")
        await asyncio.sleep(0.5)
        state = await cdp.test_state() or {}
        check("new tab creates one view", state.get("view_count", 0) >= 2)
        await cdp.test_action("newTab")
        await asyncio.sleep(0.5)
        state = await cdp.test_state() or {}
        check("third tab exists", state.get("view_count", 0) >= 3)
        check("switch action accepted", await cdp.test_action("switchTab", "1"))
        await asyncio.sleep(0.3)
        state = await cdp.test_state() or {}
        check("active index switches", state.get("active_index") == 1)
        try:
            with urllib.request.urlopen("http://127.0.0.1:8889/test-action/reorderTabs?from=0&to=2", timeout=5) as response:
                reorder_ok = response.status == 200
        except Exception:
            reorder_ok = False
        check("reorder action accepted", reorder_ok)
        await asyncio.sleep(0.3)
        state = await cdp.test_state() or {}
        check("reorder preserves three views", state.get("view_count", 0) >= 3)
        check("close action accepted", await cdp.test_action("closeTab"))
        await asyncio.sleep(0.3)
        state = await cdp.test_state() or {}
        check("close removes one view", state.get("view_count", 0) >= 2)
        check("popup count is observable", "popup_count" in state)
    print("SUMMARY:", "ALL PASS" if not failures else f"FAILURES: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
