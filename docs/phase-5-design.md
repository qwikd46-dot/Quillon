# docs/phase-5-design.md — Proxy: spinner fix + interception latency

Status: **implemented 2026-09-24; final live proxy/YouTube verification pending**.
The host allowlist is narrowed to `youtube.com`, `www.youtube.com`, and
`*.googlevideo.com`; the generated list still requires final user review.
Direct YouTube navigation is the default so the reviewed proxy scope remains
effective; set `QUILLON_USE_PRIVACY_FRONTENDS=1` to opt into the legacy
Invidious/Piped redirect, which is outside this TLS scope.

## 5.0 TLS interception scope

The implementation uses the reviewed hybrid scope:

- TLS-decrypt only `youtube.com`, `www.youtube.com`, and `*.googlevideo.com`.
- Everything else remains outside the mitmproxy response addon.
- The final generated allowlist must be shown for user review before acceptance.

The proxy cannot forge a meaningful response for an encrypted request it cannot
decrypt. The empty-video and JSON cases therefore apply only to the reviewed
hosts and matching URLs.

## 5a. Detecting ad requests beyond endpoint/domain (no content inspection)

Within the allowlisted (decrypted) hosts only:
- URL-pattern classes (path + query signatures, maintained in the existing
  `HARD_BLOCK_SUBSTRINGS`/pattern lists): `/pagead/`, `/api/stats/ads`, `/ptracking`,
  `googlevideo.com/...&adformat=`, ad-only `ctier=SA|SR|L`, `get_midroll_info`,
  `/youtubei/v1/log_event`. `ctier=SH` is preserved for normal Shorts streams.
- Player-request classification: `/youtubei/v1/player` responses get
  `adPlacements`/`adSlots` and server backoff fields stripped; matching player
  requests are annotated with `contentPlaybackContext.isInlinePlaybackNoAd` and
  the current `params: "yAEB"` no-ad signal before they leave the proxy. Shorts
  `/youtubei/v1/reel/reel_watch_sequence` requests and streaming URLs are left
  intact; only explicit ad entries are removed from their responses. This prevents
  YouTube from scheduling the ad/backoff path without corrupting Shorts playback.

## 5b. Fake success responses (the spinner fix)

For matched ad/tracker requests, respond with a real HTTP 200 and a well-formed
empty body of the correct Content-Type (mitmproxy `http.Response.make`):

| request type | response |
|---|---|
| `.js` / `application/javascript` | 200, empty body, `Content-Type: application/javascript` |
| image (`.gif/.png/.jpg`) | 200, 1×1 transparent image, correct type |
| `.mp4` / video segments | 200, empty body, `Content-Type: video/mp4`, explicit zero `Content-Length` (fallback only; the no-ad player signal should prevent this path) |
| JSON telemetry | 200, `{}`, `application/json` |
| anything else matched | 200, empty body, `text/plain` |

YouTube sees a successful, typed response for any residual ad/telemetry request;
the primary anti-fallback mechanism is the no-ad player-request context above.
Playback requests without an ad discriminator are never matched.

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
- `quillon/core/proxy_addon.py` (fake-response map, streaming rule, redacted rule metadata)
- `quillon/core/proxy_bootstrap.py` (generated `--allow-hosts` list)
- `quillon/core/webengine.py` (allow reviewed proxy-owned ad requests to reach the proxy)
- `tests/test_proxy_addon.py`, `tests/test_proxy_allowlist.py`, `scripts/bench_proxy.py`

## Risks
- Fake 200s must never match real content requests (watchlist: googlevideo
  `videoplayback` without ad params — patterns must include ad discriminators).
- Fake responses are intentionally minimal and may not satisfy every provider-specific schema.
- TLS interception remains limited to the reviewed host list; unrelated traffic is not decrypted.
- `mitmproxy` streaming + the pinned CA still exposes the usual local-account trust risk.

## Verification plan
- Unit: replay known ad/tracker URLs through the addon hook with synthetic flows →
  assert status/content-type/body for each class; assert `/youtubei/v1/player`
  receives `isInlinePlaybackNoAd`/`yAEB`; non-ad URL → untouched + streaming.
- YouTube e2e (user machine, real network): video plays; ad skipped; **no spinner**
  (assert via CDP: `.ytp-ad-spinner` absent during ad slot); playback unaffected.
- Latency: bench numbers proxy vs direct in PERF_NOTES.md; assert p50 overhead < 5 ms
  for passthrough, no first-byte penalty on streaming.
- All existing tests + tab suite still pass.
