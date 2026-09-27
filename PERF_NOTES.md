# PERF_NOTES.md — measured data (Phase 1)

Baseline numbers were captured 2026-09-23 with offscreen Qt (`QT_QPA_PLATFORM=offscreen`),
Python 3.14, and temporary Phase 1 instrumentation. Follow-up measurements were captured
2026-09-24 against the running host browser. The `[PERF]` output is gated by `BFSB_PERF=1`;
timing calls remain in the code until the next cleanup pass.

**Environment limits:** the baseline sandbox did not have SearXNG (127.0.0.1:8888) or
reliable outbound DNS. Baseline SearXNG rows are connection-refused timings; HackerNews
rows are real but variable. Follow-up HTTP and CDP measurements were taken against the
running host instance, so they are not a controlled cold/warm laboratory comparison.

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

## C. GUI-thread sync I/O violations (ground rule 6)

| location | baseline issue | Phase 1 status |
|---|---|---|
| `webengine.py:955` (`_rewrite_youtube_url`) | sync `urllib.request.urlopen` probe (0.15 s timeout, 60 s cache) on the GUI navigation path | **Fixed**: the navigation path reads a cache; a daemon refresher performs the probe off-thread. |
| `webengine.py:2039` (`_record_visit`) | sync SQLite write after each external page load | **Fixed**: the write is submitted to `_db_executor`. |
| `main_window.py:254` (`_refresh_bookmarked`) | sync SQLite read on every external URL change | **Fixed**: lookup is submitted to `_db_executor`; cached values apply immediately. |
| `server.py:502` (`handle_search`) | sync history write on the aiohttp event loop, measured 26–63 ms | **Fixed**: `HistoryStore().record` is awaited through `asyncio.to_thread`. |
| `server.py:175`, `server.py:459` | bookmark/history reads still execute synchronously in aiohttp handlers and template rendering | **Residual, not changed**: no material current cost was measured for the empty/small local store; keep as a follow-up rather than expanding this phase silently. |

## D. Not reproducible here (needs user machine / running SearXNG)

- Real SearXNG upstream latency + keep-alive behavior to it (sandbox: connection refused).
- Whether SearXNG itself is the dominant upstream cost on the user's machine
  (aggregator watchdog is 8 s; SearXNG cold start inside docker can be seconds).
- GPU/paint behavior on real hardware (offscreen here).

## E. Phase 1 follow-up measurements (2026-09-24)

### Search pipeline

The instrumented ZCode follow-up produced these server-stage samples after the
history write was moved off the event loop:

| run | submit→history | history→aggregate | aggregate→render | server total |
|---|---:|---:|---:|---:|
| 1 | 58.7 ms | 1148.6 ms | 13.6 ms | 1220.8 ms |
| 2 | 17.7 ms | 455.7 ms | 6.7 ms | 480.2 ms |

`submit→history` is the time until the worker completes, not a direct event-loop
occupancy measurement; therefore it does **not** demonstrate the design target of
`<2 ms`. A direct concurrent probe is the relevant loop-blocking check.

### Concurrent event-loop probe

The ZCode head-to-head capture measured `/health` requests issued concurrently
with a search:

| build | maximum `/health` latency | average `/health` latency | search total |
|---|---:|---:|---:|
| before Phase 1 | 320.6 ms | 26.5 ms | 1266 ms |
| after Phase 1 | 149.2 ms | 22.0 ms | 1194 ms |

A fresh HTTP-only rerun against the host instance measured search totals of
441.8–962.5 ms. Its concurrent `/health` maximum was 252.0 ms on the cold run and
13.1–18.1 ms on four warm runs; warm averages were 7.2–10.6 ms. This supports
removal of the measured event-loop stall but is not a substitute for a controlled
same-process A/B run.

### Renderer follow-up

| stage | cold | warm 1 | warm 2 |
|---|---:|---:|---:|
| submit→responseStart | 6221.6 ms | 1298.0 ms | 501.2 ms |
| responseStart→DCL | 2836.2 ms | 171.8 ms | 132.9 ms |
| DCL→load | 4662.4 ms | 117.0 ms | 215.3 ms |

The cold sample is an outlier. Warm `responseStart→DCL` improved from the
baseline 214.3–813.1 ms to 132.9–171.8 ms, but the result is not a controlled
comparison and the full-navigation cost remains. The larger SPA/JSON change is
implemented in Phase 2; these renderer samples predate that implementation.

### Tab switching follow-up

The incremental native tab update was measured at 5.56–24.54 ms total, with
`chrome_tabs` at 3.67–14.68 ms. The baseline was approximately 9–19 ms total and
7–15 ms for the rebuild. The change removes widget reconstruction, but the
`<1 ms` target was not met and the small sample is mixed; it is not claimed as a
performance win yet.

### Startup follow-up

`URLBlocker()` construction dropped from 26.1 s to 0.72 s in the isolated
post-change measurement. The full host startup remains dependent on proxy,
WebEngine profile, and renderer initialization.

### Verification status

- The corrected `scripts/test_gui_suite.py` completed with `SUMMARY: ALL PASS`
  against a one-tab host instance: GUI script alive, new-tab behavior, tab strip,
  three-tab state, query isolation, and exactly one results tab.
- The repository harness uses the `test` extra for `websockets`; the sandbox verification used a temporary standard-library WebSocket shim because that extra is not installed in the sandbox interpreter.
- The harness's own fresh-process launch could not replace the host process from
  the sandbox, so the test was run after resetting the live instance to one home
  tab. This limitation is recorded rather than treating the run as a clean
  cold-start benchmark.
- `[PERF]` output is gated by `BFSB_PERF=1`; the underlying timing calls and
  `# PERF-DEBUG(phase1)` markers remain for a later cleanup pass.

### Phase 1 disposition

The measured event-loop and startup offenders were addressed, and the tab/isolation
regression harness now passes. The tab-switch latency target and clean fresh-process
verification remain open. Phase 1 is therefore **implemented but not finally accepted**.

### Phases 2–5 implementation evidence (2026-09-24)

- JSON/SSE search path, request correlation, persistent-page progress state, and native `loadProgress` wiring are implemented. The template fixture loaded in Chromium with `bfsbAction`, `runSearch`, and `setProgress` defined and no fixture errors.
- Managed result-tab defaults, reorder actions, middle-click close, popup routing, incremental tab sync, and the native overlay/chrome changes are implemented. Full Qt runtime verification is still pending because the host Qt process could not be restarted from the sandbox.
- The final local suite has 25 passing tests plus one dependency-gated skip (26 discovered). The latest real mitmproxy benchmark measured direct p50 5.07 ms / p95 18.51 ms and proxied p50 3.85 ms / p95 7.65 ms over 200 local requests; the proxy run had no measured first-byte penalty in this sample. The benchmark used the temporary runtime wrapper and isolated proxy configuration.
- A real mitmproxy flow check verified typed 200 responses for JavaScript, GIF, JPEG, WebP, video, and JSON matches, including semantic `/api/stats/*` JSON and ad-tagged googlevideo video classification. The final allowlist is `youtube.com`, `www.youtube.com`, and `*.googlevideo.com`; the generated list still needs user review. Direct YouTube navigation is now the default; `BFSB_USE_PRIVACY_FRONTENDS=1` opts into out-of-scope frontend redirects.
- YouTube player and Shorts sequence handling now preserves `ctier=SH` content streams, removes `REEL_VIDEO_TYPE_AD` entries, and carries `contentPlaybackContext.isInlinePlaybackNoAd`/`yAEB` only on normal player requests. Shorts sequence requests and SABR streaming URLs are left intact; player/embedded responses still remove ad backoff fields and the server ABR fallback URL. This targets fake-buffering/fallback without corrupting Shorts media.
- Isolated server checks verified local-origin enforcement, rejection of external CORS origins, and filtering of script-scheme search/bookmark URLs. The native browser chrome was constructed successfully under an offscreen Qt widget runtime.
- Full YouTube playback/no-spinner and screenshot sign-off remain open. No claim of final phase acceptance is made until those checks run.

### Lifecycle/cache follow-up (2026-09-24)

- The desktop launcher now derives its own checkout path, terminates stale BFSB/proxy processes before launch, starts SearXNG asynchronously, and kills the BFSB process tree, renderer children, recorded proxy, and SearXNG container on exit.
- `QWebEngineProfile` uses the persistent disk-cache path `~/.local/share/bfsb/webengine-cache`; the browser no longer waits for SearXNG readiness before showing the local home page.
- The proxy records its PID and removes it during normal shutdown; a dummy proxy-process teardown test passed.
- A headless launcher harness with a mocked Qt child passed process-tree cleanup. Full offscreen QtWebEngine startup remains unavailable in this sandbox because `libgssapi_krb5.so.2` is missing.

### UI parity follow-up (2026-09-24)

- External-site sidebar/logo, native tab/address controls, and the HTML bookmark icon use the shared dark palette and bookmark path. The sidebar smoke check covers parented visibility and 232↔64 collapse geometry.
- Tab synchronization skips external renderers, popup tabs begin at `about:blank`, and renderer titles are cached from `titleChanged`; no synchronous `page().title()` call remains in the tab-sync path.
- Navigation state is bridged to `window.__bfsbSetNavState`; local history state handles search/home SPA entries and direct-page loads separately.
- External sidebar categories now open a separate local category view over the active website tab; the underlying site URL is unchanged. The popup has a top-right close button, Escape handling, backdrop-click dismissal, a compact category rail, explicit hash-change routing, and title synchronization from the active category.
- The supplied KDE coredump showed `QMessageLogger::fatal` reached through a PyQt slot during `QWidget::eventFilter` mouse dispatch. The newly added tab viewport event filter was removed; local pages retain their original HTML tab strip/search bar and native chrome is only shown for external sites. Tab add/remove/click smoke passes without that filter.
- The HTML tab strip now uses a same-origin `POST /api/ui/switchTab` bridge with the custom scheme only as fallback; this removes the fragile fetch/custom-scheme dependency when switching from local home pages.
- HTML tab/search geometry now follows the Qt chrome metrics: full-width 50px tab row, 52px address row, 42px pill, and no negative transform.
- Verification: 18 unit tests discovered (17 pass, one dependency-gated skip), Python syntax, Jinja rendering, Node JavaScript parsing, launcher syntax, and offscreen Qt widget/sidebar/navigation smoke checks pass. Live WebEngine and mitmproxy execution remain blocked by missing `libgssapi_krb5.so.2` and `_cffi_backend` in this sandbox.
