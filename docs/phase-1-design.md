# docs/phase-1-design.md — Performance: query intake + tab switching

Status: **awaiting user approval** (no fix code written yet).
Measurements: see `PERF_NOTES.md` (all numbers below reference it).

## Root causes (measured, ranked by impact)

1. **Sync SQLite on the event loop + GUI thread.** `HistoryStore().record()` runs
   synchronously: on the aiohttp event loop per query (measured **26-63 ms** event-loop
   stall), and on the **GUI thread** after every external page load
   (`webengine.py:_record_visit`) plus a bookmark read per URL change
   (`main_window.py:_update_chrome_for_current`). This matches the "slow submit that
   slows overall page loading": the stalled loop delays concurrent asset/API requests.
2. **Full-page navigation per query.** Every search is a whole-document navigation of
   the 50 KB template; the renderer spends **200-800 ms** parsing/re-running it
   (navigation timing, PERF_NOTES A). Charged on top of upstream latency on every query.
3. **Upstream latency is the floor** (out of our code): aggregator already runs sources
   in parallel on a shared pooled httpx client (keep-alive confirmed by warm-run
   improvements). SearXNG's own latency (user machine) sets the floor; we can only
   stop adding our own overhead to it.
4. **`_sync_chrome_tabs` full rebuild per switch** (7-15 ms, O(tabs)) — only measurable
   tab-switch waste; total switch cost is ~9-19 ms, i.e. already effectively instant.
   The reported "second click" was the `bfsb://switchTab` dispatch bug (fixed earlier
   today, verified single-click ~70 ms end-to-end incl. CDP overhead).
5. **`_rewrite_youtube_url` sync probe on the GUI thread** (up to 150 ms stall on cache
   miss per navigation) — flagged; fix proposed below since it directly hits both
   query-navigation and site navigation.

## Proposed changes (files touched, risk)

### 1. Stop doing SQLite on the event loop / GUI thread — move it to a worker
- `bfsb/core/server.py` `handle_search`: replace sync `HistoryStore().record(...)`
  with `asyncio.to_thread(...)` (server-side; no GUI involvement).
- `bfsb/core/webengine.py` `_record_visit`: hop the DB write to a `concurrent.futures`
  worker via a small module-level single-thread executor (GUI thread only queues).
- `bfsb/ui/main_window.py` `_update_chrome_for_current`: make the bookmark lookup
  async (QTimer/worker callback) or cache per-URL in the chrome until the worker
  answers; star state updates when the result arrives.
- Files: `server.py`, `webengine.py`, `main_window.py`. Risk: low (best-effort
  writes; failure paths already swallow). Verification: `[PERF] submit→history` drops
  to <2 ms; event-loop stall gone (measure via timing delta between request receive
  and first byte under a concurrent asset request).

### 2. Kill the GUI-thread sync YouTube probe
- `webengine.py` `_rewrite_youtube_url`: keep the 60 s cache, but refresh it from a
  background thread (worker) and have the navigation path use the cached value
  synchronously; on cold start use the default list immediately instead of probing.
- Files: `webengine.py`. Risk: low-medium (frontend instance choice becomes slightly
  less fresh in the first minute; retry-on-dead-frontend logic already exists).
  Verification: navigation-timing `responseStart` deltas during navigation storms;
  no more 150 ms stalls in main-thread logs.

### 3. Trim the per-query page cost (conservative option first)
- Step 1 (small, safe): keep the full navigation but cut what the renderer re-does —
  defer the dots-canvas init and panel rendering until first paint
  (`requestIdleCallback`/`setTimeout`), so `responseStart→DCL` drops.
- Step 2 (bigger, phase-1b if user approves): fetch results as JSON
  (`/search?q=…&format=json`) into the existing page and render client-side —
  no full navigation at all; results view toggles in-page. This changes the
  progress-bar hooks (Phase 2 wants per-engine events anyway) — propose doing 1 now
  and deciding 2 together with Phase 2 design.
- Files: `templates/bfsb_combined.html` (+ `server.py` for the JSON route in step 2).
  Risk: step 1 low; step 2 medium (state/bookmark/history wiring must move to the
  SPA flow) — needs its own sign-off.

### 4. Make `_sync_chrome_tabs` incremental
- Rebuild only on tab add/close; on switch, flip the active tab's style + title in
  place (mirror of the HTML-side sync). O(1) instead of O(tabs).
- Files: `browser_chrome.py` or `main_window.py`. Risk: low. Verification:
  `[PERF] switch_tab chrome_tabs` < 1 ms with 3+ tabs.

### 5. Instrumentation removal at phase end
- All `# PERF-DEBUG(phase1)` marks removed after final before/after re-measure, or
  converted into the permanent `[PERF]` log line behind an env flag (`BFSB_PERF=1`).

## Non-goals (reported, not touched)
- mitmproxy/proxy internals (Phase 5).
- SearXNG's own startup/latency (external service; can be measured on user machine).
- Aggregator upstream timeout values (working as designed; 8 s watchdog).

## Verification plan
1. Rerun the exact measurement script (`/tmp/perf_run.py` flow) before/after on the
   same build → table in PERF_NOTES.md ("before / after").
2. Headless regression: full tab suite (create/switch/close, chrome toggle, results
   render, isolation markers) must pass unchanged.
3. User machine run: same instrumented build against live SearXNG for real upstream
   numbers (I will hand over the instrumented diff for that run if wanted).
4. Revert anything that doesn't measurably help (per ground rules).

## Notes on the "NO HTTPS decryption" statement
The current proxy TLS-intercepts with a pinned CA (feature the user built and uses).
Phase 5's "without decrypting HTTPS" needs reconciling with that before Phase 5
design — flagged in AGENT_PLAN.md, no action now.
