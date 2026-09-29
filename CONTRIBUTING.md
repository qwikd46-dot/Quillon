# Contributing to Quillon

Thanks for considering it. Quillon is a privacy browser: local metasearch,
an ad-blocking proxy, and an encrypted vault, all on your own machine.

## Licence

Quillon is **MPL-2.0** (see [LICENSE](LICENSE)). In practice:

- Contributions are accepted under MPL-2.0. You keep your copyright; you
  grant the project a licence to use your work.
- Only files you *modify* are affected. A change you make in your own
  file stays as permissive as you want it to be.
- Larger works may combine Quillon with proprietary code.
- Keep the copyright notice intact in any copy you redistribute.

## Before you open a pull request

**Run the tests on the host interpreter, not in a virtualenv.**

```bash
python3.14 -m unittest \
  tests.test_phase_contracts tests.test_proxy_addon tests.test_proxy_allowlist \
  tests.test_search_aggregator tests.test_search_policy tests.test_search_progress \
  tests.test_secure_vault tests.test_chromium_runtime tests.test_server_new_tab \
  tests.test_youtube_filter tests.test_search_blocklist tests.test_vault_notice \
  tests.test_qt_view_loading tests.test_cors_guard tests.test_ghostery_lifecycle \
  tests.test_qt_virtual_guards tests.test_lazy_imports tests.test_platform_support \
  tests.test_chromium_crypt
```

Or `./scripts/run_unit_tests.py`, which discovers the modules itself and
reports which optional dependencies were importable.

**Read the `Ran N tests` / `OK` line, not the exit code.** The WebEngine
teardown can segfault *after* reporting `OK`; that is a known artefact, not
a failure. A virtualenv without PyQt6 silently skips the Qt tests and still
says OK, which is why the host interpreter matters.

## What makes a good test here

Most of this suite historically asserted on *strings in source files*, which
is how a two-day-old tab-switching bug survived a green run. Prefer tests
that build real things:

- build a real `BFSBWindow` and actually switch tabs
- spawn a real Node process and check it dies with its parent
- start a real proxy and make a real request through it

If your fix is behavioural, **mutation-prove the test**: revert the fix,
confirm the new test fails, restore it. A test that never failed is not
evidence that it works.

Be careful with tests that touch live state. Two have bitten before — a
test that killed the user's running proxy, and one that migrated their real
vault key. Sandbox anything that reaches the keyring, the port, or `~/.bfsb`.

## Style

Match the surrounding code rather than your defaults. Comments should
explain a constraint the code cannot show — why a value is what it is, not
what the next line does. No background refresh tasks, no telemetry, no
debug prints left in.

## What the project will not take

- CAPTCHA solving, credential injection, or auth-redirect bypass
- Hardware fingerprint spoofing
- Anything that phones home, or that reintroduces a telemetry path
- UI redesigns that fight the existing look

## Reporting a security problem

Please do not open a public issue for a vulnerability that would let
someone read another user's vault, cookies or browsing history. Open a
private security advisory on the repository instead, and you will get a
response.

## Reporting a bug

What you did, what you expected, what happened, and the relevant log
lines. `~/.bfsb/logs/ghostery-engine.log` and
`~/.bfsb/proxy-startup.log` are the two that usually matter. Note that
the data directory is now `~/.quillon` — the old `~/.bfsb` is legacy.
