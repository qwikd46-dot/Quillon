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

### Phase 1 — Performance (query intake + tab switching) — IN PROGRESS (verification open)
Symptoms: query submit slow + slows overall page loading; Home↔Results tab switch lags, needs a second click.
Hypothesis to VERIFY (not assume): GUI thread blocked by sync SearXNG/proxy round-trips, or tab switch rebuilding widgets.
Deliverables: measured table (a) submit→request sent (b) request→first byte (c) first byte→rendered (d) tab-switch time + main-thread activity during lag. Fix top measured offenders (keep-alive, DNS cache, parallel upstream, persistent views). Single-click instant switch, no state loss. Before/after numbers; revert changes that didn't help.

### Phase 2 — Progress bar (Home + Results) — IMPLEMENTED; FINAL BROWSER VERIFICATION PENDING
Symptom: bar finishes before results load (fake timer). Replace with event-driven progress: submitted→10%, waiting upstream→indeterminate/pulsing, per-engine response increments, rendered→100%. Real-time, thread-safe (signal/slot only).

### Phase 3 — Tab logic — IMPLEMENTED; OAUTH/REORDER VERIFICATION PENDING
(a) Clicking a result that redirects spawns an extra mystery tab — log every tab-creation call site, reproduce search→click result, fix so redirects reuse the tab. (b) Restore tab drag-and-drop reordering (regressed earlier) + test coverage. (c) Verify close / middle-click-close / switching across home, results, external sites.

### Phase 4 — Visual unification — IMPLEMENTED; SCREENSHOT VERIFICATION PENDING
Qt top search bar identical to home page search bar (styling + bookmark icon/behavior). Remove menu from Qt search bar. Sidebar becomes universal (Home, Results, ideally while browsing). Before/after screenshots for sign-off.

### Phase 5 — Proxy: spinner fix + interception latency — IMPLEMENTED; LIVE PROXY VERIFICATION PENDING
Design doc first: (a) detect ad requests beyond endpoint/domain within the explicitly reviewed TLS-interception allowlist; (b) real HTTP 200 with correct per-type empty bodies (empty JS, 1x1 GIF, empty mp4) so YouTube sees success and doesn't show the 5s retry spinner; (c) zero buffering of chunked/SSE/WebSocket — pass through byte-for-byte. Cut proxy per-request overhead (reuse, no unnecessary buffering, intercept only rule-matched flows). Tests: replay ad URLs assert fake responses; YouTube ad-skip w/o spinner; latency benchmark N requests proxy vs direct.

## Status log

- 2026-09-23: Plan created. Phase 1 diagnosis started (instrumentation + measurement, no fix code until design approved).
- 2026-09-24: Whole-plan design docs written for approval. Phase 1 was approved conditionally; its measured fixes are committed.
- 2026-09-24: Phase 1 follow-up measurements and corrected GUI regression evidence recorded in `PERF_NOTES.md`. The tab-switch target and clean fresh-process verification remain open; Phase 1 is not finally accepted.
- 2026-09-24: `THREAT_MODEL.md` corrected against the actual CA files, permissions, certificate metadata, and the narrowed allowlist.
- 2026-09-24: Phases 2–5 implemented in the working tree: JSON/SSE progress, managed tabs/reorder, native chrome overlay, and typed proxy responses/streaming. Static contracts, proxy unit tests, and a real mitmproxy benchmark pass; full Qt/browser and YouTube verification remain pending.
- 2026-09-24: User authorized continuing through all phases and requested a single final approval checkpoint.
- 2026-09-24: Read-only audit follow-up fixed confirmed startup, scheme-dispatch, CORS/URL-validation, SSE lifecycle, download, shutdown, proxy fail-closed, and typed-response issues; remaining limitations are runtime/browser sign-off items.
- 2026-09-24: Lifecycle follow-up added stale-instance cleanup, asynchronous SearXNG startup, proxy PID tracking, process-tree shutdown, and a persistent WebEngine cache path.
- 2026-09-24: UI follow-up aligned the external sidebar, native chrome, bookmark icon, tab switching, popup routing, and local navigation state; cached renderer titles remove synchronous title IPC from tab sync. External category actions now open a same-tab website overlay with close/backdrop dismissal instead of navigating the site. Popup hash routing, mini category rail, and title synchronization were added after live-state feedback. An unsafe native-tab event-filter workaround and local-page native-tab override were reverted after a Qt abort/crash. HTML tab switching now uses a same-origin Qt bridge, and HTML tabs/search geometry follows Qt metrics. The universal sidebar now auto-docks on the left with the home HTML category set. Proxy follow-up preserves `ctier=SH` Shorts streams, removes Shorts ad entries, and applies no-ad/backoff handling without rewriting Shorts playback requests; live WebEngine sign-off remains environment-blocked.

## Execution order (Phase 1 approved; later phase gates remain)

1. **Phase 1** perf: workers for SQLite off event-loop/GUI-thread; GUI-thread sync-probe removal; paint deferral; incremental chrome tab sync. *(Decision in design: SPA-search "step 2" is folded into Phase 2, not done twice.)*
2. **Phase 2** progress bar: requires SPA search (in-page fetch of `/search?format=json` + SSE per-engine events) — real event-driven bar, no timers.
3. **Phase 3** tab logic: instrument → fix mystery-tab (default `openInNewTab=false`, popups-become-tabs policy), restore drag-reorder (`bfsb://reorderTabs`), middle-click close, full switch/close matrix tests.
4. **Phase 4** visual unification: Qt pill == HTML pill (icons inside, same metrics), remove ⋮ from Qt bar, universal left sidebar with the home category set while browsing. Before/after screenshots for sign-off.
5. **Phase 5** proxy: fake-200 responses per content type (spinner fix), streaming passthrough for everything else, explicit minimal TLS-interception allowlist (**user decision** — see phase-5-design.md §5.0), latency benchmark.

Commits: small, one logical change each, app+tests after every commit.
Instrumentation: `[PERF]` output is gated by `BFSB_PERF=1`; timing calls and
`# PERF-DEBUG(phase1)` markers still require a final cleanup pass before Phase 1
can be marked complete.
