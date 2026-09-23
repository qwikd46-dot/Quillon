# THREAT_MODEL.md — BFSB filtering proxy

**Last reviewed:** 2026-09-24
**Applies to:** Phase 5 proxy design and all TLS-interception behavior.

---

## 1. TLS-interception scope (explicit)

| Host(s) | TLS decrypted? | Why |
|---|---|---|
| `youtube.com`, `www.youtube.com` | ✅ Yes | Response rewriting (ad-key stripping, youtubei JSON) |
| `*.googlevideo.com` | ✅ Yes | Ad-segment blocking (fake 200 empty mp4) |
| Everything else | ❌ **Never** | SNI/DNS-level endpoint blocking only; bytes pass through untouched |

**Decision (user-approved 2026-09-24):** hybrid, made explicit — TLS-decrypt ONLY the
above hosts. All other traffic is never decrypted.

## 2. What the proxy can see on decrypted hosts

- **Full HTTP request/response headers** (including `Cookie`, `Authorization`,
  session tokens for youtube.com and googlevideo.com).
- **Full request/response bodies** (JSON, video segments, etc.).
- **URL paths and query parameters.**

## 3. What is logged/stored

| Data | Logged? | Stored where? | Retention |
|---|---|---|---|
| Rules-hit metadata (URL, rule matched, action taken) | Yes (stdout) | Console / log file only | Session lifetime |
| Request/response bodies | **No** | Never written | N/A |
| Cookies / session tokens | **No** | Never logged or stored | N/A |
| Decrypted content | **No** | Processed in-memory only, discarded | N/A |

**Ground rule:** no logging of decrypted payload bodies — rules-hit metadata only.

## 4. CA certificate & private key

| Item | Location | Permissions | In git repo? |
|---|---|---|---|
| CA certificate (PEM) | `~/.bfsb/mitmproxy/mitmproxy-ca-cert.pem` | 644 (owner rw) | ❌ No |
| CA certificate (CER) | `~/.bfsb/mitmproxy/mitmproxy-ca-cert.cer` | 644 | ❌ No |
| CA certificate + key (P12) | `~/.bfsb/mitmproxy/mitmproxy-ca-cert.p12` | 644 ⚠️ | ❌ No |
| CA private key | Inside the `.p12` (PKCS#12 bundle) | ⚠️ see below | ❌ **Never committed** (verified: `git log --all --diff-filter=A` clean) |

**⚠️ Key exposure risk:** the `.p12` contains the CA private key and is currently
mode 644 (world-readable). Recommended: `chmod 600 ~/.bfsb/mitmproxy/mitmproxy-ca-cert.p12`.

**CA subject:** `C=US, O=mitmproxy, CN=mitmproxy`
**Validity:** 2026-09-19 → 2028-09-19
**Pin:** SPKI hash is computed at proxy start and passed to Chromium via
`--ignore-certificate-errors-spki-list=<hash>`.

## 5. Mitigations

1. **Restrict decryption to the explicit host list above.** The `--allow-hosts`
   regex enforces this; anything not matching gets a TCP RST or DNS NXDOMAIN,
   never a TLS handshake.
2. **No body logging.** The addon (`proxy_addon.py`) only emits rules-hit metadata.
3. **CA key permissions.** Should be `chmod 600`.
4. **Session-scoped.** The proxy process lives and dies with the browser;
   no persistent daemon.

## 6. Residual risks

- The CA is trusted by the system trust store (mitmproxy installs it into NSS).
  Any process that can read the `.p12` can impersonate TLS for the allowed hosts.
  Mitigated by filesystem permissions (should be 600).
- If the user adds more hosts to the TLS-interception allowlist, this document
  must be updated to reflect the expanded scope.