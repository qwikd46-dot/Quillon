# BFSB — Browser for Safe Browsing

A privacy-focused desktop browser built on PyQt6 / Qt WebEngine. It ships its own
entire interface — home page, search results, bookmarks, history, downloads,
privacy and password panels — from a local `aiohttp` server, and pairs that with
local metasearch and an ad-blocking proxy so browsing and searching do not have
to go through a third party.

```
BFSB  (PyQt6 / Qt WebEngine)
 |
 +-- UI server        127.0.0.1:8889   aiohttp + Jinja
 +-- Ad-block proxy   127.0.0.1:8228   mitmdump + addon
 |   `-- engine       ephemeral        Ghostery, Node
 +-- Search backend   127.0.0.1:8888   SearXNG, Podman

Secrets: AES-GCM vault, root key in the OS keyring, scoped per install
```

---

## Table of contents

- [Highlights](#highlights)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running](#running)
- [Running in a container](#running-in-a-container)
- [Testing](#testing)
- [Performance](#performance)
- [Security model](#security-model)
- [Known limitations](#known-limitations)
- [Project layout](#project-layout)
- [Contributing](#contributing)
- [Copyright and licence](#copyright-and-licence)
- [Acknowledgements](#acknowledgements)

---

## Highlights

**The whole UI is local.** No page is fetched from a remote server to render the
browser's own interface. Everything is served from `127.0.0.1:8889` and rendered
inside the WebEngine view, behind a CORS guard that checks the caller's address
against the server's real bind address rather than the client-supplied `Host`
header.

**Search goes through SearXNG, not a search engine.** Queries hit a self-hosted
SearXNG instance on `127.0.0.1:8888`. Results pass through a host-normalisation
policy, an adult-content blocklist, and a rate-aware progress path. The blocklist
is consulted by tokenised set lookup, so its cost follows the length of the query
rather than the size of the list.

**Ad blocking happens in two places.** A `mitmdump` proxy on `127.0.0.1:8228`
applies the Ghostery filter engine (120k+ network filters, 36k+ cosmetic
filters) and a YouTube-specific filter, with a domain/DNS blocklist as fallback
when the engine is unavailable. The Node backend is started with
`PR_SET_PDEATHSIG`, so the kernel reaps it if the browser dies — including
`SIGKILL`.

**Secrets are encrypted at rest.** Passwords and cookie material live in a
versioned AES-GCM vault whose root key is held by the OS keyring. The key is
scoped per installation, so two checkouts on one machine cannot clobber each
other. A corrupt vault is quarantined and named, never silently emptied.

**No telemetry, no background phone-home.** There are no analytics, no update
checker, and no crash reporter. Network egress is limited to what you navigate
to, the local SearXNG instance, and whatever the proxy has to see to filter a
request.

---

## Architecture

| Component | Where it runs | Default port | Source |
|---|---|---|---|
| UI server (aiohttp + Jinja) | localhost | `8889` | `bfsb/core/server.py` |
| Ad-block proxy (mitmdump) | localhost | `8228` | `bfsb/core/proxy_addon.py` |
| Ad-block engine (Ghostery, Node) | localhost | ephemeral | `ghostery-adblocker/server.js` |
| Search backend (SearXNG) | Podman container | `8888` | `docker-compose.yml` |
| Encrypted vault | `~/.bfsb/` | — | `bfsb/core/secure_vault.py` |

Everything binds to `127.0.0.1`. The proxy and the UI server are both started
before Qt initialises, so Chromium can be configured against them at launch
rather than restarted when they come up.

The in-page interface lives in `bfsb/templates/bfsb_combined.html`; the native
tab strip, address bar and sidebar are Qt widgets in `bfsb/ui/` that drive that
page rather than reimplementing it.

---

## Requirements

- **Python 3.14** with `PyQt6`, `PyQt6-WebEngine`, `cryptography`, `aiohttp`,
  `jinja2`, `httpx`, `keyring`, `mitmproxy`, `lxml`
- **Node.js** (for the Ghostery ad-block engine)
- **npm** (only to install the engine's dependencies on first run)
- **Podman** (only for the SearXNG container)

> Use the host interpreter. `python3.14` is the environment that has PyQt6
> installed; a virtualenv without it will silently skip the Qt tests and still
> report success.

---

## Installation

```bash
git clone git@github.com:qwikd46-dot/BFSB.git
cd BFSB

# System packages
python3.14 -m pip install -e ".[test]"

# Ad-block engine dependencies (node_modules is not committed; the launcher
# will also do this for you on first run)
npm install --no-audit --no-fund
```

Install the desktop entry, editing the paths if you did not clone to
`~/Downloads/bfsb`:

```bash
install -Dm644 bfsb.desktop ~/.local/share/applications/bfsb.desktop
```

Start the search backend on its own if you want it outside the launcher:

```bash
podman compose up -d
```

---

## Running

```bash
./bfsb_launcher.sh
```

The launcher selects Wayland or X11 appropriately, configures the WebEngine
process and resource paths (auto-detecting a pip-bundled Qt), starts SearXNG and
the ad-block proxy, and tears the whole tree down on exit — including on
`SIGTERM` — so no orphaned `mitmdump` is left holding port 8228.

First launch installs the ad-block engine's Node dependencies in the background
and writes its log to `~/.bfsb/npm-install.log`. Until that finishes, blocking
falls back to the DNS blocklist rather than failing to start.

Useful runtime state:

| Path | Contents |
|---|---|
| `~/.bfsb/bfsb.db` | bookmarks and history |
| `~/.bfsb/passwords.enc`, `cookies.enc`, `vault.enc` | encrypted stores |
| `~/.bfsb/adblock_cache/` | cached filter lists |
| `~/.bfsb/logs/ghostery-engine.log` | ad-block engine log |
| `~/.bfsb/proxy-startup.log` | proxy startup errors (were previously `/dev/null`) |

---

## Running in a container

BFSB ships as a single image containing everything it needs: the Qt/WebEngine
UI, the mitmdump ad-block proxy, the Node Ghostery engine, and SearXNG. Nothing
is left to install on the host.

```bash
podman build -t bfsb .          # or: docker build -t bfsb .
podman compose up -d bfsb
podman compose logs -f bfsb
podman compose down
```

Plain podman/docker works too:

```bash
podman run --rm -it \
  -e BFSB_HEADLESS=1 \
  -v bfsb-data:/home/binwalk/.bfsb \
  bfsb
```

### What "one process" means here, precisely

The container has **one entry point**: `bfsb_supervisor.py` is PID 1 and owns
the whole stack. The children are necessarily separate OS processes — SearXNG is
its own WSGI application, mitmdump is a proxy server with its own event loop, and
the ad-block engine is a Node program that cannot be a Python thread. What you
get from the supervisor is that you start and stop **one thing**, and that
nothing survives it:

```
supervisor (pid 1)
  +-- Xvfb
  +-- SearXNG            127.0.0.1:8888
  +-- bfsb.main          127.0.0.1:8889   (the Qt app)
  |    +-- mitmdump      127.0.0.1:8228
  |    +-- node engine   ephemeral
  +-- expose-ui          optional, see below
  +-- expose-proxy
```

The supervisor starts the display, SearXNG and the browser. It deliberately does
**not** start the mitmdump proxy or the Node engine, because the application
already owns both — `proxy_bootstrap` starts mitmdump with a pid file keyed to
the port and its owning process, and the Ghostery engine runs with
`PR_SET_PDEATHSIG`. Starting a second copy from the supervisor would fight that
lifecycle and reintroduce the "closing one instance kills another's proxy" class
of bug.

On `SIGTERM` the supervisor stops the tree in reverse order, children before
parents, escalating to `SIGKILL` after a grace period, and reaps everything.
Verified: a running container goes from 13 processes to none, leaving no
orphaned `mitmdump` or `node` behind.

### Seeing the window

`BFSB_HEADLESS=1` (the default) runs the GUI against an in-container Xvfb, which
is what you want on a server or in CI. To draw on the host's display instead:

```bash
xhost +si:localuser:$(id -un)          # on the host, once per session
BFSB_HEADLESS=0 DISPLAY=:0 podman run --rm -it \
  -v /tmp/.X11-unix:/tmp/.X11-unix:ro bfsb
```

Note this needs `--device` or a privileged-ish setup on some hosts, and that
exposing an X socket to a container is a real trust decision — the container can
read your keystrokes. Prefer headless for anything you do not trust.

### Ports

Published ports **do not work by default**, and the compose file says so. The app
binds `127.0.0.1` deliberately, and inside a container loopback is the
container's own namespace, not the host's — so `-p 8889:8889` forwards to
nothing.

Set `BFSB_EXPOSE=1` to have the supervisor start a `socat` forwarder per port,
bound to the container's own address (not the wildcard, which podman's publisher
already holds). The container is fully self-contained either way; this only
buys you the ability to reach the UI and proxy from the host.

```bash
BFSB_EXPOSE=1 podman run --rm -p 127.0.0.1:8889:8889 -p 127.0.0.1:8228:8228 bfsb
```

### The vault loses the keyring in a container

This is the one real security cost, and it is not a bug:

```
keyring backend : Keyring        (no Secret Service / D-Bus to talk to)
storage mode    : file-degraded
degraded        : True
```

The OS keyring needs a session bus that a container does not have, so the vault
falls back to a `0600` key file at `~/.bfsb/vault.key`. Your records are still
AES-GCM encrypted, but **the key is now on disk** rather than protected by the
platform's credential store. Two consequences:

- The `bfsb-data` volume holds the key as well as the ciphertext. Treat it as
  secret material, not as a cache.
- If you care about key-at-rest, run BFSB on the host (where the keyring works)
  rather than in a container.

Chromium's own cookie store is a separate matter and is still plaintext wherever
it runs — see [Known limitations](#known-limitations).

---

## Testing

```bash
./scripts/run_unit_tests.py
```

That script discovers `tests/test_*.py` itself rather than taking a hand-kept
list, and reports which optional dependencies were importable, so a run that
quietly skipped the Qt half of the suite is visible instead of inferred from the
word `OK`.

To run the suite explicitly on the host interpreter (list the modules —
`tests/` is not a package, so discovery will not work from the repo root):

```bash
python3.14 -m unittest \
  tests.test_phase_contracts tests.test_proxy_addon tests.test_proxy_allowlist \
  tests.test_search_aggregator tests.test_search_policy tests.test_search_progress \
  tests.test_secure_vault tests.test_chromium_runtime tests.test_server_new_tab \
  tests.test_youtube_filter tests.test_search_blocklist tests.test_vault_notice \
  tests.test_qt_view_loading tests.test_cors_guard tests.test_ghostery_lifecycle \
  tests.test_qt_virtual_guards tests.test_lazy_imports
```

**199 tests.** A note on reading the result: read the `Ran N tests` / `OK` line,
not the exit code. The WebEngine teardown can segfault *after* reporting `OK`,
which is a pre-existing artefact and not a failure. Equally, `OK` proves less
than it looks like — a number of these tests assert on source text. Every
behavioural fix in this project was mutation-proven: reverted, confirmed to
fail, restored.

> Do not run the suite with BFSB open unless you have checked for tests that
> touch live process or credential state. Two such tests have existed and both
> bit.

---

## Performance

Measured on the development host, before and after the optimisation work:

| Measurement | Before | After |
|---|---|---|
| `import BFSBWindow` (cold process) | 2.00 s | **1.01 s** |
| Ad-block engine cold start | 2.11 s (with PyQt6 resident) | **~1.0 s** (no PyQt6) |
| Blocklist `classify()` per call | 212 µs | **36 µs** |
| Shipped blocklist one-time load | 280 ms | **114 ms** |

The import win came from making the `bfsb`, `bfsb.core` and `bfsb.core.search`
package exports resolve lazily (PEP 562). The public API is unchanged — every
advertised export resolves to the identical object it did before — but nothing
heavy loads until it is touched. This is what stops `mitmdump`, which loads the
proxy addon as a standalone script in a headless process, from dragging in a
GUI toolkit.

The filter win came from profiling rather than guessing: the 140k-entry blocklist
was never the cost. `matched_domain` was scanning 35 separate compiled regexes
per call, and `_fold` was running Unicode normalisation over plain-ASCII input.
Both are now single-pass.

---

## Security model

**What this protects.** Search queries and browsing history stay on this machine.
Bookmarks, history and secrets are stored encrypted. The UI server and proxy bind
to loopback only, and the API surface is guarded against cross-origin callers.

**What it does not protect.** This is a research and personal-use browser, not a
hardened anonymity tool. It does not defeat browser fingerprinting, does not
resist traffic analysis, and ships no hardened browser core — Qt WebEngine is
Chromium, and Chromium has a large, actively exploited vulnerability history.
Treat it as a privacy *convenience*, not a privacy *guarantee*.

**Vault design.** Root key in the OS keyring; per-record keys derived with HKDF
and an explicit purpose frame, so a cookie key and a password key can never
collide. Records are sealed with AES-GCM under per-record nonces. The provider
fails closed: if the keyring cannot be *read*, it will not mint a replacement
key over an existing vault. Promotion to a new keyring account is verified by
read-back before the old entry is removed, and a failed promotion leaves the
original key in place.

---

## Known limitations

**Cookies are still stored in plaintext.** Qt's own cookie store
(`~/.local/share/bfsb/cookie_storage/Cookies`) holds ~20 unencrypted session
cookies, and is not currently purged. Mirroring cookies into the encrypted vault
is the obvious fix and is *not* implemented: `QNetworkCookie.name()` returns a
`QByteArray`, which is neither `bytes` nor `bytearray`, so a naive port stores
every name as the literal `b'sid'`, private-profile detection needs
`isOffTheRecord()` (not the method that was originally used), deletions do not
propagate, and doing it properly needs a schema change storing name, value, flags
and expiry. That is a project, not a wiring fix. Until then, the store is mode
`0600` and readable only by you.

**Blocking falls back silently.** If the Ghostery engine fails to start, the
browser still runs on the DNS blocklist alone. The reason is logged to
`~/.bfsb/logs/ghostery-engine.log`, but nothing surfaces it in the UI yet.

**One machine, one user.** The proxy, the SearXNG container and the launcher all
assume a single-user desktop session.

---

## Project layout

```
bfsb/
  core/
    server.py            aiohttp UI server, routes, /api/ui/*
    webengine.py         WebEngine profiles, bfsb:// scheme, interception
    proxy_addon.py       mitmdump addon (ads, YouTube filter, streaming)
    proxy_bootstrap.py   proxy lifecycle, pid ownership, port 8228
    blocker.py           Ghostery + DNS backends, block caches
    secure_vault.py      AES-GCM + HKDF vault, keyring provider
    passwords.py         password vault on top of it
    search/              aggregator, policy, safety, local index
  ui/
    main_window.py       Qt window, tabs, native chrome
    browser_chrome.py    tab strip, address bar, sidebar
  templates/
    bfsb_combined.html   the entire in-page interface
tests/                   199 tests
scripts/                 test runners, benchmarks, headless harness
ghostery-adblocker/      vendored filter engine + HTTP server
```

---

## Contributing

Branch from `main`, keep commits topical, and add a test that fails for the
reason the bug happened. The most valuable tests in this repo are behavioural
ones that build real objects — a real `BFSBWindow` that actually switches tabs, a
real Node process that is checked in the process table, a real proxy that
answers a real request. Source-scanning tests pass against broken code and are
the reason a two-day-old tab bug survived a green suite.

Before pushing, confirm the suite is green on the **host** interpreter and that
the run did not touch live user state.

---

## Copyright and licence

```
Copyright (c) 2026 Binwalk
All rights reserved.
```

BFSB is the work of **Binwalk**, who owns this repository and holds the
copyright in the original material authored in this project's history, including
the vault, search, proxy and browser work.

This file is proprietary. No licence is granted to copy, modify, distribute or
sublicense it except under a separate written agreement with the copyright
holder. All other rights are reserved.

### Contributions and prior authors

Git history records contributions from more than one person, and those
contributions are not covered by the statement above. The repository's root
commit (`fb4a21f`, 2026-07-08) was authored by **DEV-COLLABOR
<fou16461@gmail.com>**, and nine commits carry GitHub Copilot as author.
Contributor tooling that omits an author from a commit's metadata does not
remove their copyright in that commit.

If you contributed to this repository and your work is not reflected in the
copyright notice above, open an issue and it will be corrected.

---

## Acknowledgements

- **Qt** — Qt WebEngine and the Qt Tools for Python (PyQt6), LGPLv3 / GPL
- **SearXNG** — the metasearch instance this browser queries
- **mitmproxy** — the intercepting proxy
- **Ghostery AdBlocker** — the filter engine, MPL-2.0
- **EasyList / Fanboy / Peter Lowe / uBlock Origin** — the filter lists loaded by
  that engine, under their respective licences
- **Chromium** — via Qt WebEngine
- **Fanboy, annoyance / easylist, Peter Lowe** — filter data redistributed under
  the terms recorded in `bfsb/core/search/data/SOURCES.md`

The vendored engine under `ghostery-adblocker/` is third-party and remains under
its own MPL-2.0 licence; the notice in that directory governs it, not this one.
