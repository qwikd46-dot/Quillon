# AGENT_PLAN.md — BFSB working plan

> Repo is the memory. Chat context is disposable. Each session: read this file,
> `PERF_NOTES.md`, and `docs/phase-N-design.md` before doing anything.

## Project

Qt6 desktop browser (home page, results page, tabs, sidebar) with:
- Integrated **SearXNG search backend** (local, `127.0.0.1:8889` UI server → aggregator → SearXNG/engines)
- **Local filtering proxy** on `127.0.0.1:8228` (endpoint/SNI/DNS-level ad+tracker blocking).
  Browser and proxy code both live in this repo:
  - proxy: `bfsb/core/proxy_addon.py`, `bfsb/core/proxy_bootstrap.py`, `bfsb/core/proxy_manager.py`
  - browser: `bfsb/` (PyQt6 + QtWebEngine), GUI pages: `bfsb/templates/bfsb_combined.html`

> NOTE (flagged, see PERF_NOTES.md): the brief says "NO HTTPS decryption", but the
> current proxy code DOES TLS-intercept via a pinned mitmproxy CA
> (`--ignore-certificate-errors-spki-list`, `_install_mitmproxy_ca`).
> Phase 5 design must reconcile this with the user before any change.

## Ground rules (every phase)

1. **Repo is the memory.** Persist measurements → `PERF_NOTES.md`; designs → `docs/phase-N-design.md`.
2. **Diagnose before changing.** Temporary logging → reproduce → show actual numbers BEFORE proposing fixes. If not reproducible, ask for repro steps.
3. **Design doc before code** (root cause, proposed change, files touched, risks, verification plan) → wait for user OK.
4. **Small commits, one logical change each.** Run app + tests after each; nothing regresses.
5. **Don't rewrite working code.** Out-of-phase problems: report, don't touch.
6. **GUI thread must NEVER block.** All network/proxy/query work off the main thread. Sync network/file I/O on the GUI thread: flag immediately even if out of scope.
7. **Subagents:** read-only explore/diagnose only; fixture drafting only after design approval; main agent owns ALL implementation; never two write-capable subagents on the same tree.

## Phases

### Phase 1 — Performance (query intake + tab switching) — IN PROGRESS
Symptoms: query submit slow + slows overall page loading; Home↔Results tab switch lags, needs a second click.
Hypothesis to VERIFY (not assume): GUI thread blocked by sync SearXNG/proxy round-trips, or tab switch rebuilding widgets.
Deliverables: measured table (a) submit→request sent (b) request→first byte (c) first byte→rendered (d) tab-switch time + main-thread activity during lag. Fix top measured offenders (keep-alive, DNS cache, parallel upstream, persistent views). Single-click instant switch, no state loss. Before/after numbers; revert changes that didn't help.

### Phase 2 — Progress bar (Home + Results)
Symptom: bar finishes before results load (fake timer). Replace with event-driven progress: submitted→10%, waiting upstream→indeterminate/pulsing, per-engine response increments, rendered→100%. Real-time, thread-safe (signal/slot only).

### Phase 3 — Tab logic
(a) Clicking a result that redirects spawns an extra mystery tab — log every tab-creation call site, reproduce search→click result, fix so redirects reuse the tab. (b) Restore tab drag-and-drop reordering (regressed earlier) + test coverage. (c) Verify close / middle-click-close / switching across home, results, external sites.

### Phase 4 — Visual unification
Qt top search bar identical to home page search bar (styling + bookmark icon/behavior). Remove menu from Qt search bar. Sidebar becomes universal (Home, Results, ideally while browsing). Before/after screenshots for sign-off.

### Phase 5 — Proxy: spinner fix + interception latency
Design doc first: (a) detect ad requests beyond endpoint/domain without decrypting HTTPS; (b) real HTTP 200 with correct per-type empty bodies (empty JS, 1x1 GIF, empty mp4) so YouTube sees success and doesn't show the 5s retry spinner; (c) zero buffering of chunked/SSE/WebSocket — pass through byte-for-byte. Cut proxy per-request overhead (reuse, no unnecessary buffering, intercept only rule-matched flows). Tests: replay ad URLs assert fake responses; YouTube ad-skip w/o spinner; latency benchmark N requests proxy vs direct.

## Status log

- 2026-09-23: Plan created. Phase 1 diagnosis started (instrumentation + measurement, no fix code until design approved).
- 2026-09-24: Phase 1 measured (PERF_NOTES.md). **WHOLE-PLAN design docs written for approval** — `docs/phase-1-design.md` … `docs/phase-5-design.md`. No fix code yet.

## Execution order (as designed — needs user OK)

1. **Phase 1** perf: workers for SQLite off event-loop/GUI-thread; GUI-thread sync-probe removal; paint deferral; incremental chrome tab sync. *(Decision in design: SPA-search "step 2" is folded into Phase 2, not done twice.)*
2. **Phase 2** progress bar: requires SPA search (in-page fetch of `/search?format=json` + SSE per-engine events) — real event-driven bar, no timers.
3. **Phase 3** tab logic: instrument → fix mystery-tab (default `openInNewTab=false`, popups-become-tabs policy), restore drag-reorder (`bfsb://reorderTabs`), middle-click close, full switch/close matrix tests.
4. **Phase 4** visual unification: Qt pill == HTML pill (icons inside, same metrics), remove ⋮ from Qt bar, native universal sidebar overlay while browsing, hamburger entry point. Before/after screenshots for sign-off.
5. **Phase 5** proxy: fake-200 responses per content type (spinner fix), streaming passthrough for everything else, explicit minimal TLS-interception allowlist (**user decision** — see phase-5-design.md §5.0), latency benchmark.

Commits: small, one logical change each, app+tests after every commit.
Instrumentation: all `# PERF-DEBUG(phase1)` marks removed (or moved behind `BFSB_PERF=1`) at Phase 1 close.
