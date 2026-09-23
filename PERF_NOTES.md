# PERF_NOTES.md — measured data (Phase 1)

All numbers captured 2026-09-23, offscreen Qt (`QT_QPA_PLATFORM=offscreen`), Python 3.14,
instrumented build (temporary `# PERF-DEBUG(phase1)` marks in `server.py`,
`search/aggregator.py`, `main_window.py` — remove at end of phase 1).

**Environment limits:** SearXNG (127.0.0.1:8888) and outbound DNS are NOT available in
this sandbox. SearXNG numbers here are connection-refused timings; HackerNews numbers
are real (DNS worked intermittently). Upstream-stage numbers on a machine with SearXNG
running will differ — rerun this instrumented build there or paste the `[PERF]` lines.

## A. Query intake — where the time goes

Server-side stage table (`[PERF] search …`, handle_search):

| stage | cold | warm ×3 | what runs there |
|---|---|---|---|
| submit → history | **62.7 ms** | 25.7 / 37.8 / 32.3 ms | sync SQLite `HistoryStore().record()` **on the aiohttp event loop** |
| history → agg | **2079.7 ms** | 849.6 / 457.2 / 634.6 ms | `aggregate_search` (parallel SearXNG + HN, 8 s watchdog) |
| agg → render | 34.3 ms | 5.7 / 2.4 / 16.1 ms | jinja render (first render pays template compile) |
| **server total** | **2176.7 ms** | 880.9 / 497.4 / 683.0 ms | |

Per-source (`[PERF] source …`):

| source | cold | warm | notes |
|---|---|---|---|
| searxng | 945.0 ms (items=0) | 21.7 / 13.7 / 6.2 ms (refused) | service down in sandbox; real numbers need user machine |
| hackernews | 1146.9 ms | 838.5 / 452.5 / 632.8 ms | shared httpx client works — warm queries get pooled keep-alive connections (already reused, no per-query handshake) |

Renderer waterfall (CDP `performance.getEntriesByType('navigation')` on the results page):

| stage | run1 | run2 | run3 |
|---|---|---|---|
| submit → responseStart (= server total + HTTP) | 905.5 ms | 509.6 ms | 699.8 ms |
| responseStart → domContentLoaded | **813.1 ms** | **306.6 ms** | **214.3 ms** |
| DCL → load | 2.4 ms | 144.7 ms | 209.5 ms |

Reading: the results **page is a full navigation that re-parses the entire 50 KB
template on every query**; the renderer needs 200-800 ms to parse + run it (first
run worst). This cost repeats identically on the user's machine and is charged on
top of upstream latency.

## B. Tab switching — what the main thread actually does

`[PERF] switch_tab` (QElapsedTimer breakdown, Qt main thread):

| step | range over 8 switches |
|---|---|
| `_sync_single_view` (runJavaScript dispatch) | 0.2 – 1.1 ms |
| `_stack.setCurrentIndex` | 1.1 – 5.3 ms |
| `_sync_chrome_tabs` (full native tab-bar rebuild) | **7.0 – 15.0 ms** |
| `_update_chrome_for_current` | 0.02 – 0.04 ms |
| **total main-thread work** | **~9 – 19 ms** |

In-page Home↔Results sidebar switch (same page, `showView`): **0.3 – 2.4 ms**
(first panel open: 54.9 ms — history fetch + list build).

End-to-end single-click switch (CDP click → state flip): ~70 ms including CDP
round-trips. The historical "needs a second click" was the `bfsb://switchTab`
dispatch bug (URL-normalization + case sensitivity) — already fixed and verified
single-click now.

**Only measurable waste:** `_sync_chrome_tabs` rebuilds the whole native tab bar
(clear + re-add every widget) on every switch, growing with tab count. At 15 ms it
is not user-visible by itself, but it is O(tabs) work per switch and per URL change.

## C. GUI-thread sync I/O violations (ground rule 6 — flagged, not fixed)

| location | what | cost |
|---|---|---|
| `webengine.py:927` (`_rewrite_youtube_url`) | sync `urllib.request.urlopen` probe (0.15 s timeout, 60 s cache) **on GUI thread** inside `acceptNavigationRequest` | up to 150 ms stall per navigation on cache miss |
| `webengine.py:2033` (`_record_visit`) | sync SQLite write (`HistoryStore().record`) **on GUI thread** in `loadFinished` — every external page load | ~10-60 ms stall after each page load (unmeasured; same store as the 26-63 ms server-side measurement) |
| `main_window.py:240` (`_update_chrome_for_current`) | sync SQLite read (`BookmarkStore().get`) **on GUI thread** on every URL change on an external site | ~ms-level, but on every navigation |
| `server.py:496` | sync SQLite write on the **aiohttp event loop** | **measured 26-63 ms** event-loop stall per query — also delays any concurrent browser request (page assets, favicons, API calls) |

## D. Not reproducible here (needs user machine / running SearXNG)

- Real SearXNG upstream latency + keep-alive behavior to it (sandbox: connection refused).
- Whether SearXNG itself is the dominant upstream cost on the user's machine
  (aggregator watchdog is 8 s; SearXNG cold start inside docker can be seconds).
- GPU/paint behavior on real hardware (offscreen here).
