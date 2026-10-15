# Repository scan — Quillon Browser

Scope: full read-only pass over the checkout at `74d54aa`
(`fix(ui): tab reordering and sidebar state now reach the window`).
Method: static inspection, `pyflakes`/`ruff`, a dependency-complete test run,
and targeted greps for security- and hygiene-sensitive patterns. No source
files were modified by this scan.

## 1. What this repo is

A Linux-only, privacy-focused desktop browser (PyQt6 / Qt WebEngine) whose
entire UI is served locally from an `aiohttp`+Jinja server, backed by a
self-hosted SearXNG metasearch instance and a `mitmdump` ad-block proxy.

| Component | Where | Port | Source |
|---|---|---|---|
| UI server (aiohttp + Jinja) | localhost | 8889 | `quillon/core/server.py` |
| Ad-block proxy (mitmdump + addon) | localhost | 8228 | `quillon/core/proxy_addon.py` |
| Ghostery engine (Node) | localhost | ephemeral | `ghostery-adblocker/server.js` |
| Search backend (SearXNG) | Podman | 8888 | `docker-compose.yml` |
| Encrypted vault (AES-GCM) | `~/.quillon/` | — | `quillon/core/secure_vault.py` |

Composition: ~23.9k lines of Python in `quillon/`, ~4.4k lines of tests,
~2.6k lines of Jinja/HTML (the in-page UI is `quillon/templates/quillon_combined.html`,
2,416 lines). A vendored copy of the Ghostery ad-blocker lives under
`ghostery-adblocker/` (~18 MB).

## 2. Health: the test suite is green

The suite is dependency-gated; on a bare interpreter most tests *skip* and the
run still exits 0, which the project itself warns about (`tests/README.md`).
With the project dependencies plus the system GUI libraries present:

```
Ran 227 tests in 15.229s
OK
```

All 227 tests pass (0 failures, 0 errors). Reproduce in a clean sandbox:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  'PyQt6>=6.6.0' 'PyQt6-WebEngine>=6.6.0' 'aiohttp' 'jinja2' 'requests' \
  'python-dotenv' 'httpx' 'lxml' 'cssselect' 'cryptography' 'keyring' \
  'adblock' 'mitmproxy>=12.2.3' 'websockets'
npm install                       # Ghostery engine (node_modules/@ghostery/adblocker)
# system libs PyQt6/QtWebEngine link against (libGL, libglib, libxkb, xcb, nss, ...)
export LD_LIBRARY_PATH=<dir with those .so files>
export QT_QPA_PLATFORM=offscreen QT_OPENGL=software
export QTWEBENGINE_CHROMIUM_FLAGS='--no-sandbox --disable-gpu --disable-dev-shm-usage'
python -m unittest tests.test_*   # 227 tests, OK
```

Caveat: in a headless sandbox the process **segfaults during interpreter
teardown** (`Release of profile requested but WebEnginePage still not
deleted`) *after* unittest prints `OK`. The tests themselves pass; the crash
is QtWebEngine profile cleanup with no display, not a code defect.

## 3. Static analysis

`ruff` with default rules reports 831 findings in `quillon/` (418 auto-fixable),
almost entirely style: unsorted import blocks, unused imports, and typing
modernisation (`Optional[X]` → `X | None`). There is **no `ruff.toml` /
`[tool.ruff]` config**, so these are default-rule noise rather than project
policy. `pyflakes` filtered to non-style findings:

| Finding | Assessment |
|---|---|
| `ui/side_panel.py:922` `DownloadManager` undefined | **False positive** — used only in a postponed annotation (`from __future__ import annotations`). |
| `core/search/aggregator.py:66,70` `httpx` undefined | **False positive** — same (module has the future import). |
| `ui/components.py:40` and `:89` — `__init__` defined twice | Real duplicate; the second definition (with `_anim.finished.connect`) wins, the first is dead. |
| `core/server.py:1282` and `:1356` — `handle_about_page` defined twice | Real duplicate; the templated version (1356) wins, the stub (1282) is dead. |
| `core/search/providers.py:8/33` `re`, `:19/466` `get_engine_configs` | Local re-imports shadowing module-level ones; harmless. |
| `core/webengine.py:23/1292` `APP_CONFIG` | Local re-import inside `create_web_profile`; harmless. |
| `core/storage/importer.py:24,49` `folder_id` unused | See §4 — the helper is dead code. |
| `core/youtube_filter.py:115` `nonlocal removed` unused | Harmless (the counter is updated in the nested `consider`). |
| `ui/side_panel.py:655` `all_count` unused | Minor: a `list_all(limit=10000)` is computed and discarded. |

## 4. Confirmed defects (all minor)

1. **Dead, internally-inconsistent helpers** — `core/storage/importer.py`
   `_chrome_walk` / `_firefox_walk` (lines 13 and 35) are never called; only
   the public `import_chrome_json` / `import_firefox_json` are used, and they
   contain their own inline traversal. The helpers also compute `folder_id`
   and then discard it (the tuple slot typed `folder_id` actually receives the
   bookmark *name*), so they are both unreachable and wrong. Removing them is
   safe.
2. **Duplicate definitions that silently shadow** — `handle_about_page`
   (`server.py`) and `Toggle.__init__` (`ui/components.py`) each defined twice.
   Behaviour is currently correct only because the *later* definition happens
   to be the intended one; the earlier copies are dead and should be deleted.
3. **Unused computation** — `ui/side_panel.py:655` builds a full bookmark list
   only to drop it.

## 5. Repository hygiene

- Tracked backup/scratch files: `quillon.py.backup`, `quillon/ui/styles.py.backup`,
  `quillon/ui/tabs.py.backup`, `quillon/ui/main_window.py.bak2` (11 bytes),
  `quillon/core/search/{crawl_runner,crawler,ranker}.py.bak-2026-07-29`,
  and root-level `test_quillon.py`, `test_browser.py`, `test_browser2.py`.
  `.gitignore` already excludes similar throwaway scripts; these slipped in.
- The tree carries its own history as prose (`AGENT_PLAN.md`, `PERF_NOTES.md`,
  `docs/phase-N-design.md`, `THREAT_MODEL.md`, `problem_analysis.txt`,
  `AdblockSummary.md`, `YouTubeAdBlockingSolution.md`) — useful, but several
  are stale (see §7).

## 6. Security review

Positives found:

- No `shell=True`, no `eval`/`exec`, no `pickle.load`, no `yaml.load` on
  untrusted input in `quillon/`.
- No hard-coded passwords / API keys / tokens in the source.
- The UI server has a CORS guard that validates the caller against the real
  bind address rather than the client-supplied `Host` header
  (`tests/test_cors_guard.py` covers spoofing).
- Secrets are AES-GCM encrypted with the root key in the OS keyring, scoped
  per installation (`quillon/core/secure_vault.py`).

Known, already-documented caveat: the proxy **does TLS-intercept** via a
pinned mitmproxy CA, which contradicts the original "no HTTPS decryption"
brief. This is recorded in `AGENT_PLAN.md` and `THREAT_MODEL.md` and flagged
as needing an explicit user decision — worth confirming it is still intended.

## 7. Documentation drift

`problem_analysis.txt` claims `RequestInterceptor.HARD_BLOCK_SUBSTRINGS` in
`webengine.py` holds only ~28 ad domains and must be expanded to match
`_BUILTIN_FALLBACK_RULES`. That is stale: the tuple now has **174** entries
(the fallback rule list has 209 `||…^` lines). The ad-block fast path is
already far broader than the document describes; the document should be
updated or removed to avoid mis-triaging future ad-block reports.

The same drift appears in `README.md` (~line 346), which still states
"199 tests" over a hand-written 17-module list; the suite is now 20 files and
**227** tests. The hand-maintained count is exactly the failure mode
`tests/README.md` warns about, and it should point at
`scripts/run_unit_tests.py` instead of a number.

## 8. Recommendations (priority order)

1. Delete the dead duplicate definitions and the unused `_chrome_walk` /
   `_firefox_walk` helpers (§4).
2. Remove the tracked backup/scratch files (§5) and extend `.gitignore`.
3. Add a minimal `[tool.ruff]` config (or a `pyproject.toml` lint section) so
   the 800+ default-rule findings stop drowning real signal; then run
   `ruff check --fix` for imports/typing.
4. Refresh or delete `problem_analysis.txt` (§7).
5. Confirm the TLS-interception decision (§6) with the project owner.
