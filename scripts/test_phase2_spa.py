import asyncio
import sys
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
        await cdp.eval("window.__phase2Marker = 'kept'")
        await cdp.eval("document.getElementById('searchInput').value = 'phase2 spa'")
        await cdp.eval("document.getElementById('searchInput').dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}))")
        arrived = await cdp.wait_for("location.pathname === '/search' && location.search.includes('phase2')", timeout=20)
        check("search uses same page", bool(arrived))
        check("page marker survives", await cdp.eval("window.__phase2Marker === 'kept'"))
        check("results render", bool(await cdp.wait_for("document.querySelectorAll('#resultsList .result-item').length >= 1", timeout=20)))
        check("progress settles", await cdp.wait_for("!document.getElementById('searchProgress').classList.contains('active')", timeout=5))
    print("SUMMARY:", "ALL PASS" if not failures else f"FAILURES: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
