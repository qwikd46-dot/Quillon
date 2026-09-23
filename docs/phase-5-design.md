# docs/phase-5-design.md — Proxy: spinner fix + interception latency

Status: awaiting approval (part of whole-plan approval). **Contains one decision the
user must make (see 5.0).**

## 5.0 Decision required: TLS interception scope (brief vs reality)

The brief says "NO HTTPS decryption". Today the proxy **does** TLS-intercept a
narrow host set (mitmproxy + pinned CA; `--allow-hosts` currently only youtube.com)
— that is exactly what makes the existing youtubei JSON ad-stripping possible, and
what Phase 5(b) needs (you cannot forge a 200 for an encrypted request you don't
decrypt).

**Recommendation — keep the hybrid, make it explicit and minimal:**
- TLS-decrypt **only** hosts on an explicit allowlist needed for response faking
  (`youtube.com`, `www.youtube.com`, `youtubei.googleapis.com`, `*.googlevideo.com`)
  — this is the current state, made intentional.
- Everything else: **never decrypted**. DNS/hosts-level blocking for blocked domains
  (already in place via hosts.txt + DNS blocklist), CONNECT passthrough for the rest
  (byte-for-byte tunnel).
- `--allow-hosts` becomes a generated exact host list (no regex drift — the `$`-anchor
  bug from history is the cautionary tale).

If you'd rather have strictly zero decryption, the spinner fix is impossible for
HTTPS ads (we'd only see SNI) — say so and Phase 5(b) shrinks to DNS/endpoint
blocking + client-side scriptlet only.

## 5a. Detecting ad requests beyond endpoint/domain (no content inspection)

Within the allowlisted (decrypted) hosts only:
- URL-pattern classes (path + query signatures, maintained in the existing
  `HARD_BLOCK_SUBSTRINGS`/pattern lists): `/pagead/`, `/api/stats/ads`, `/ptracking`,
  `googlevideo.com/...&adformat=`, `get_midroll_info`, `/youtubei/v1/log_event`.
- Player-request classification: `/youtubei/v1/player` responses already get
  `adPlacements`/`adSlots` stripped (existing response hook) — keep, but move the
  buffering scope to exactly these API paths.

## 5b. Fake success responses (the spinner fix)

For matched ad/tracker requests, respond with a real HTTP 200 and a well-formed
empty body of the correct Content-Type (mitmproxy `HTTPResponse.make`):

| request type | response |
|---|---|
| `.js` / `application/javascript` | 200, empty body, `Content-Type: application/javascript` |
| image (`.gif/.png/.jpg`) | 200, **1×1 transparent GIF** (43 bytes) / 1×1 PNG, correct type |
| `.mp4` / video segments | 200, empty body, `Content-Type: video/mp4` (player treats segment as ended — no retry) |
| JSON telemetry | 200, `{}`, `application/json` |
| anything else matched | 204 No Content |

YouTube sees "success", never an error → no 5 s retry spinner. Playback requests are
never matched (pattern lists are ad-specific; verify against a real video).

## 5c. Zero buffering for everything else

- In the `responseheaders` hook: enable mitmproxy response **streaming** for every
  flow not in the rewrite set (i.e. all CONNECT-passthrough hosts are never touched
  anyway; allowlisted hosts stream unless path ∈ {`/youtubei/v1/player`, …}).
- WebSockets: untouched (mitmproxy passes WS through unless explicitly intercepted;
  keep it that way — add an assert/test that no WS flow is buffered).
- Chunked/SSE: streamed (no `response.read()` on non-rewrite paths).

## 5d. Proxy overhead (uses Phase 1 PERF_NOTES methodology)

- Interception is already host-scoped; keep rule matching O(1)-ish (precompiled
  tuple/prefix sets, no regex recompiles per request).
- No body reads except rewrite paths; no logging I/O on the hot path.
- Benchmark (`scripts/bench_proxy.py`): N=200 requests to a local test server through
  proxy vs direct (p50/p95), plus a streaming download to prove zero-buffering
  (first-byte latency + memory flat). Results into PERF_NOTES.md.

## Files touched
- `bfsb/core/proxy_addon.py` (fake-response map, streaming rule, host allowlist)
- `bfsb/core/proxy_bootstrap.py` (generated `--allow-hosts` list)
- `bfsb/core/blocker.py` (share the same pattern lists — single source)
- `tests/test_proxy_fake_responses.py` (new), `scripts/bench_proxy.py` (new)

## Risks
- Fake 200s must never match real content requests (watchlist: googlevideo
  `videoplayback` without ad params — patterns must include ad discriminators).
- Anti-adblock detection: faked responses must look ordinary (correct headers/order
  via `HTTPResponse.make`; no extra headers).
-mitmproxy streaming + our CA: no change to trust model (pinning unchanged).

## Verification plan
- Unit: replay known ad/tracker URLs through the addon hook with synthetic flows →
  assert status/content-type/body for each class; non-ad URL → untouched + streaming.
- YouTube e2e (user machine, real network): video plays; ad skipped; **no spinner**
  (assert via CDP: `.ytp-ad-spinner` absent during ad slot); playback unaffected.
- Latency: bench numbers proxy vs direct in PERF_NOTES.md; assert p50 overhead < 5 ms
  for passthrough, no first-byte penalty on streaming.
- All existing tests + tab suite still pass.
