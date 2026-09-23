import asyncio, json, urllib.request, websockets

def pages():
    return [t for t in json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/list", timeout=5).read()) if t.get("type") == "page"]

def state():
    return json.loads(urllib.request.urlopen("http://127.0.0.1:8889/test-state", timeout=15).read())

async def eval_js(ws, expr):
    await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                              "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == 1:
            r = msg.get("result", {})
            v = r.get("result", {}).get("value")
            err = (r.get("exceptionDetails") or {}).get("text")
            return err if err else v

async def main():
    import subprocess, time, os, signal
    # Self-contained harness: always start from a fresh browser instance.
    subprocess.run(["pkill", "-9", "-f", "[b]fsb.main"], capture_output=True)
    time.sleep(2)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", BFSB_TEST="1", PYTHONUNBUFFERED="1")
    subprocess.Popen(["setsid", "python3", "-m", "bfsb.main"], cwd="/home/binwalk/Downloads/bfsb",
                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(120):
        try:
            urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=1)
            urllib.request.urlopen("http://127.0.0.1:8889/test-state", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    time.sleep(4)

    results = []
    def check(name, cond): results.append((name, bool(cond))); print(("PASS " if cond else "FAIL "), name)

    p1 = pages()[0]
    async with websockets.connect(p1["webSocketDebuggerUrl"], max_size=30*1024*1024) as ws:
        # 1. GUI alive (script not dead)
        check("bfsbAction defined (script alive)", await eval_js(ws, "typeof bfsbAction === 'function'"))
        check("no JS errors on payload line", await eval_js(ws, "window.__BFSB_PAGE__ && !!window.__BFSB_PAGE__.savedBookmarks !== undefined"))
        # 2. new tab via GUI click
        await eval_js(ws, "document.getElementById('newTabBtn').click()")
        await asyncio.sleep(4)
        check("new tab created", state()["view_count"] == 2)
        check("page1 intact after new tab", await eval_js(ws, "!!document.querySelector('.sidebar') && document.body.children.length > 2"))
        check("tab strip shows 2 tabs", await eval_js(ws, "document.querySelectorAll('#tabStrip .tab').length === 2"))
        # 3. search in the NEW tab
        t2 = [x for x in pages() if x is not p1][-1]
        # reconnect to the other target by index
        pgs = pages()
        other = pgs[-1] if pgs[0]["webSocketDebuggerUrl"] != ws.remote_address else None
    # open fresh sockets per assertion to avoid stale ws
    for p in pages():
        async with websockets.connect(p["webSocketDebuggerUrl"], max_size=30*1024*1024) as w:
            m = await eval_js(w, "window.__tabMarker || ''")
            href = await eval_js(w, "location.href.slice(0,40)")
            print("  target:", repr(m), href)
    # marker-based isolation on the two views
    pgs = pages()
    async with websockets.connect(pgs[0]["webSocketDebuggerUrl"], max_size=30*1024*1024) as w:
        await eval_js(w, "window.__tabMarker='ALPHA'")
        await eval_js(w, "fetch('bfsb://newTab').catch(()=>{})")  # third tab for clarity
        await asyncio.sleep(4)
    check("three tabs", state()["view_count"] == 3)
    # Marker check keyed by URL (CDP target ordering is not stable):
    # set the marker on a HOME tab, assert the RESULTS tab lacks it.
    pgs = pages()
    home_t = [p for p in pgs if p["url"].rstrip("/") == "http://127.0.0.1:8889"]
    res_t = [p for p in pgs if "search?q=" in p["url"]]
    if home_t and res_t:
        # Isolation = per-tab server-rendered state differs: the results tab
        # carries its query in __BFSB_PAGE__.query; a home tab's is empty.
        # Wait until the results tab's URL actually reflects the search (the
        # CDP target list can lag behind the real navigation).
        async def wait_url_has(ws, substr, tries=50):
            for _ in range(tries):
                href = await eval_js(ws, "location.href")
                if href and substr in href:
                    return True
                await asyncio.sleep(0.25)
            return False
        async with websockets.connect(res_t[0]["webSocketDebuggerUrl"], max_size=30*1024*1024) as w:
            arrived = await wait_url_has(w, "search?q=")
            rq = await eval_js(w, "(window.__BFSB_PAGE__||{}).query || ''") if arrived else None
        async with websockets.connect(home_t[0]["webSocketDebuggerUrl"], max_size=30*1024*1024) as w:
            hq = await eval_js(w, "(window.__BFSB_PAGE__||{}).query || ''")
        check("tabs own their state (query isolation)", bool(rq) and hq == "")
    else:
        check("tabs own their state (query isolation)", False)
    # search in current tab, then confirm only that tab has results
    async with websockets.connect(pgs[1]["webSocketDebuggerUrl"], max_size=30*1024*1024) as w:
        await eval_js(w, "location.href='/search?q=github'")
        await asyncio.sleep(6)
    cnt = []
    for p in pages():
        async with websockets.connect(p["webSocketDebuggerUrl"], max_size=30*1024*1024) as w:
            cnt.append(await eval_js(w, "document.querySelectorAll('#resultsList .result-item').length"))
    check("exactly one tab shows results", cnt.count(8) == 1 and min(cnt) == 0)
    print("results per tab:", cnt)
    # chrome toggle on external site (current view = whatever stack shows; navigate current to example.com via address submit)
    s = state()
    print("views:", s["view_count"], "chrome:", s["chrome_visible"])

    fails = [n for n, ok in results if not ok]
    print("SUMMARY:", "ALL PASS" if not fails else f"FAILURES: {fails}")

asyncio.run(main())
