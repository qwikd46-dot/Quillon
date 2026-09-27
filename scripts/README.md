# BFSB headless test harness

Drive the BFSB Qt browser under Xvfb, click/hover/eval/inspect via CDP, capture
screenshots + console errors. No Playwright required.

## Quick start

```bash
./scripts/headless-test.sh
```

This will:
1. Kill any old BFSB / Xvfb process on our display port.
2. Start Xvfb on `:99` (1400x900x24).
3. Launch BFSB via `launch.sh` under `DISPLAY=:99`.
4. Wait until CDP (`localhost:9222`) and the local search server
   (`localhost:8889`) are both up.
5. Run `scripts/run-checks.py`.
6. Tear everything down on exit (even on Ctrl-C / signal).

Screenshots are dropped in `scripts/screenshots/`.

## Two surfaces, two drivers

| Surface                                 | Driver         | How                              |
| --------------------------------------- | -------------- | -------------------------------- |
| Web content (embedded webview, side panel, home template, search results) | CDP | `cdp.click("#searchInput")`, `cdp.eval("...")`, `cdp.screenshot(...)` |
| Native Qt chrome (tab bar, URL bar, nav buttons) | ydotool | `cdp.ydotool_click(120, 20)` at known Qt geometry |

The tab bar is visible (rendered by Qt) but is **not** part of the
Chromium DOM — that's why the embedded webview tests don't see
`#newTabBtn`. CDP queries against the DOM only ever see HTML content.

## Available CDP commands (`scripts/bfsb_cdp.py`)

- `await cdp.eval(js_expr)` — synchronous JS eval, returns the value
- `await cdp.query_box(selector)` — returns `(x, y, w, h)` or `None`
- `await cdp.click(selector)` — hover-center + left click
- `await cdp.hover(selector)` — move mouse over
- `await cdp.click_at(x, y)`, `await cdp.hover_at(x, y)` — raw coords
- `await cdp.keypress("Enter")` — single key (modifier chords work, e.g. `"ctrl+d"`)
- `await cdp.ydotool_click(x, y)` — falls back to ydotool for the Qt chrome
- `await cdp.screenshot("/tmp/x.png")` — PNG via `Page.captureScreenshot`
- `await cdp.wait_for("predicate as JS", timeout=5)` — poll until truthy
- `cdp.errors`, `cdp.warnings`, `cdp.page_logs` — accumulated per-page console activity
- `await cdp.switch_page(idx)` — switch to a different CDP page target (e.g. a new window)

## Adding a new check

Open `scripts/run-checks.py`, append to `CHECKS`:

```python
CHECKS.append({
    "name": "side panel opens on Ctrl+B",
    "action": lambda c: c.keypress("ctrl+b"),
    "assert": lambda c: c.eval("document.querySelector('.bfsb-side-panel.visible') !== null") or False,
})
```

`action` and `assert` are async callables that receive the `CDP`
instance. The `assert` step returns truthy for pass.

## Troubleshooting

- BFSB hangs on launch — see `/tmp/bfsb-headless.log` and `/tmp/xvfb.log`.
- CDP never comes up — `launch.sh` might be flagging the port. Try
  `lsof -i :9222`.
- ydotool enters wrong coordinates — check the Xvfb geometry (we use
  1400x900). If BFSB resizes, update `headless-test.sh` to match.
- The check runner can't see `#newTabBtn` — that's correct, it's Qt chrome.
  Use a coordinate-based `ydotool_click(...)` for native-only assertions.

## Files

| File | Role |
|------|------|
| `headless-test.sh` | Entrypoint (Xvfb, BFSB lifecycle, teardown) |
| `bfsb_cdp.py`       | Async CDP client (no Playwright dep) |
| `run-checks.py`     | Declarative check definitions |
| `screenshots/`      | PNGs written during runs |
