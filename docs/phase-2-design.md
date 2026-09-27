# docs/phase-2-design.md — Progress bar (Home + Results), event-driven

Status: **implemented 2026-09-24; final browser verification pending**.
The JSON/SSE path is in place with request correlation and a persistent-page fallback.

## Root cause (verified)

The results page bar is a fixed CSS animation (`progressLoad` 1.2 s, template
`.search-progress`) that starts when the page loads and finishes on a timer — it has
no connection to the query lifecycle. The native `NativeProgressBar` (external sites)
is closer to real (loadStarted/loadFinished) but animates by random increments, not
by `loadProgress`.

## Proposed change

**Prerequisite (from Phase 1):** search becomes an in-page fetch:
`GET /search?q=…&format=json` returns `{query, results_html?, results[], count, infobox_html, engine_events}`.
The home/results page stays loaded; only the results view updates.

**Progress source (server):** while aggregating, the server publishes lifecycle events
per query to an in-memory channel; the page subscribes with SSE:

- `GET /api/search/progress?stream=1` (aiohttp `StreamResponse`, `text/event-stream`)
- events: `submitted` (10%), `engine_start {source}` (indeterminate/pulsing),
  `engine_done {source, ms, n}` (increments by 90/N engines), `done {total_ms}` (100%),
  `error {source}`.
- Aggregator already knows per-source start/end (instrumented in Phase 1) — the events
  are emitted from the existing `timed()` wrappers via a callback/queue passed through
  `aggregate_search(query, progress=…)`. No new threads; same event loop.

**Progress bar (GUI):**
- In-page bar (Home + Results): driven by the SSE events; pulsing animation class
  while waiting on engines; width jumps per `engine_done`; 100% + fade on `done`.
- Native chrome bar (external sites): driven by existing Qt signals only —
  `loadProgress(p)` → width, `loadStarted` → indeterminate pulsing, `loadFinished` →
  complete. No timers. (Already thread-safe: all Qt signals.)
- No `setTimeout`-driven fake progress anywhere; template's CSS `progressLoad`
  animation removed/replaced by the pulsing class.

## Files touched
- `bfsb/core/server.py` (SSE route, progress channel, `format=json` search route — shared with Phase 1)
- `bfsb/core/search/aggregator.py` (progress callback param)
- `bfsb/templates/bfsb_combined.html` (EventSource client, bar states, SPA search render)
- `bfsb/ui/browser_chrome.py` + `bfsb/ui/main_window.py` (native bar → loadProgress-driven)

## Risks
- SSE through the filtering proxy: the stream is localhost (browser → 127.0.0.1:8889
  directly, not via mitmproxy) — no proxy involvement. Confirm no buffering
  (`X-Accel-Buffering`-style: aiohttp doesn't buffer by default; flush per event).
- SPA search changes result-link/history semantics — mitigated in Phase 1 step 2
  design; history recording stays server-side on query.
- Long-lived SSE connections per tab must be closed on view switch/`done`.

## Verification plan
- Unit: fake aggregator with 3 slow sources → assert event sequence/order/timings.
- Manual/CDP: submit query → screenshot bar at 10% / pulsing / incremented / 100%;
  bar must visibly WAIT during a stall (add an artificial 3 s source in test mode).
- Regression: full tab suite + results render unchanged.
