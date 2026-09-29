# THREAT_MODEL.md — Quillon filtering proxy

**Last reviewed:** 2026-09-24
**Applies to:** Phase 5 proxy design and all TLS-interception behavior.

---

## 1. TLS-interception scope (explicit)

The reviewed Phase 5 scope is:

| Host scope | TLS decrypted? | Reason |
|---|---|---|
| `youtube.com` | Yes | YouTube response rewriting |
| `www.youtube.com` | Yes | YouTube response rewriting |
| `*.googlevideo.com` | Yes | Ad-segment classification and response faking |
| Everything else | No | CONNECT/SNI/DNS handling; no mitmproxy response addon |

The generated `--allow-hosts` expression is built from
`quillon/core/proxy_bootstrap.py:ALLOW_HOSTS` and is port-anchored. It rejects
suffix-confusion hosts and unreviewed YouTube-family domains. The final list
must be shown for review before this scope is treated as final.

## 2. What the proxy can see on decrypted hosts

- **Full HTTP request/response headers** (including `Cookie`, `Authorization`,
  session tokens for youtube.com and googlevideo.com).
- **Full request/response bodies** (JSON, video segments, etc.).
- **URL paths and query parameters.**

## 3. What is logged/stored

| Data | Logged? | Stored where? | Retention |
|---|---|---|---|
| Rules-hit metadata | Yes | `~/.local/share/quillon/proxy_events.log`; mitmproxy stdout is normally suppressed | No rotation is configured |
| Request/response bodies | No | Never written by the proxy addon | N/A |
| Decrypted content | No | Processed in memory only | N/A |
| Cookies/session tokens | Not by the proxy addon | Browser profile/cookie vault and normal browser history may retain them | Browser-profile policy |

**Ground rule:** decrypted payload bodies are never logged. Proxy URLs are
redacted to scheme/host/path before rule logging; the browser and history
subsystems may still retain ordinary visited URLs and profile data.

## 4. CA certificate & private key

| Item | Location | Permissions | In git repo? |
|---|---|---:|---|
| CA certificate (PEM) | `~/.quillon/mitmproxy/mitmproxy-ca-cert.pem` | 644 | No |
| CA certificate (CER) | `~/.quillon/mitmproxy/mitmproxy-ca-cert.cer` | 644 | No |
| Certificate-only P12 | `~/.quillon/mitmproxy/mitmproxy-ca-cert.p12` | 600 | No |
| CA private-key P12 | `~/.quillon/mitmproxy/mitmproxy-ca.p12` | 600 | No |
| CA private key (PEM) | `~/.quillon/mitmproxy/mitmproxy-ca.pem` | 600 | No |

The key-bearing files are mode 600. The CA private key is not present in the
repository or its Git history; no CA regeneration is required.

**CA subject:** `CN=mitmproxy, O=mitmproxy`
**Validity:** 2026-09-17 → 2036-09-14
**Pin:** SPKI hash is computed at proxy start and passed to Chromium via
`--ignore-certificate-errors-spki-list=<hash>`.

## 5. Mitigations

1. **Restrict decryption to the reviewed host list.** The port-anchored
   `--allow-hosts` expression is explicit; nonmatching traffic is not sent through
   the response-rewriting addon and remains CONNECT/SNI/DNS handled.
2. **No body logging.** The addon logs only redacted rules-hit metadata.
3. **Upstream certificate verification.** The proxy bootstrap no longer sets
   `ssl_insecure=true`.
4. **CA key permissions.** Key-bearing files are mode 600.
5. **Session-scoped proxy process.** The mitmdump process lives and dies with
   the browser; the generated CA and any browser-profile trust entry can
   persist after shutdown and must be removed separately if persistent trust
   is not wanted.
6. **Local-origin API boundary.** CORS and state-changing internal routes
   accept only the Quillon loopback origins; external origins are rejected.
7. **Trusted URL validation.** Search results and bookmarks are restricted to
   credential-free HTTP(S) URLs before rendering or storage.
8. **Remote debugging gate.** Chromium remote debugging is enabled only with
   `QUILLON_TEST=1`.

## 6. Residual risks

- The CA is trusted by the browser profile. Any process that can read the
  key-bearing files can impersonate TLS for the allowed hosts; mode 600 limits
  this to the owning account.
- The final generated host list still requires explicit user review before
  Phase 5 acceptance.
- `QUILLON_USE_PRIVACY_FRONTENDS=1` opts into legacy Invidious/Piped redirects;
  those frontend hosts are outside the reviewed TLS-interception scope.
- If the user changes the TLS-interception allowlist, this document must be
  updated to reflect the expanded scope.
