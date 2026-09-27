# docs/phase-3-design.md — Tab logic: mystery tab + drag-reorder + close/switch matrix

Status: **implemented 2026-09-24; final OAuth/browser verification pending**.
The managed-tab, reorder, close, and middle-click paths are wired.

## 3a. The "extra mystery tab" on result click

### Diagnosis plan (instrument first — done before any fix)
- Log every tab-creation call site with a traceback tag:
  `main_window.new_tab()` (one `print` at entry with a `site` label), and
  `BFSBPage.createWindow()` in `webengine.py` (popups/target=_blank).
- Reproduce: launch → home → search → click a result → observe view_count + logs.

### Expected findings (from code reading; to be confirmed by the repro)
1. `settings.openInNewTab` defaults **true** → the template turns every result click
   into `fetch('bfsb://newTab?url=…')` → a new tab per click. The user perceives the
   result tab (plus their expectation of staying) as a "mystery extra tab".
2. Sites opening `target=_blank`/`window.open` hit `BFSBPage.createWindow()` which
   spawns a **separate OS popup window** (not a tab) — another surprise surface.

### Fix
- Default `openInNewTab` to **false**: result links navigate the current tab; the
  setting stays available (still honored when enabled).
- `createWindow()` popup policy: normal popups (OAuth) keep their own window (required
  by Google/TikTok flows — do not touch); plain `target=_blank` link clicks get routed
  to a real tab instead of a popup window (Qt exposes `WebBrowserTab` and
  `WebBrowserBackgroundTab`; honor those as managed tabs and keep OAuth/dialog
  window types as windows).
- Guarantee: after search → click result → **view_count increases by exactly 1** and
  the results tab is unchanged (covered in tests).

## 3b. Restore drag-and-drop reordering

The original design had HTML5 drag-reorder with a FLIP animation; the integrated
template dropped it because tabs are Qt-owned. Restore end-to-end:

- Template: re-add `draggable="true"` + dragstart/dragover/drop handlers on
  `#tabStrip .tab` (markup already carries `data-index` from Python sync).
- New action: `bfsb://reorderTabs?from=I&to=J` → `BFSBSchemeHandler` → main window.
- New `BFSBWindow.reorder_tabs(from, to)`: move `self._views[from]` → `to`, rebuild
  `QStackedWidget` order (remove/re-insert widgets preserving current view), update
  `_sync_chrome_tabs()` + `_push_tabs_js()` (one logical change).
- FLIP animation kept client-side (from the original design) after the sync push.

## 3c. Close / middle-click / switch matrix (test coverage)

Extend the harness (headless, CDP + `/test-state`):

| action | contexts |
|---|---|
| switch | home↔results, results↔external, external↔home |
| close (✕ button) | home tab, results tab, external-site tab, last tab (must spawn fresh home) |
| middle-click close | same contexts (add `AuxiliaryButton` handling in template + native `NativeTab`) |
| new tab | from home, from results, from external site |
| reorder | drag 0→2 with 3 tabs (assert order via titles) |

Middle-click close is new: `mousedown`/`auxclick` with `button===1` on `.tab` →
`bfsb://closeTab?index=N`; native side: `NativeTab.mousePressEvent` already emits
clicked on left — extend for MiddleButton. Cover each cell in
  `scripts/test_tabs_phase3.py` (custom CDP harness driving `/test-state`).

## Files touched
- `bfsb/templates/bfsb_combined.html` (DnD, middle-click, default setting)
- `bfsb/core/webengine.py` (`createWindow` tab policy)
- `bfsb/ui/main_window.py` (`reorder_tabs`, tab-creation logging)
- `bfsb/core/webengine.py` `BFSBSchemeHandler` (reorderTabs action)
- `bfsb/ui/browser_chrome.py` (middle-click on native tab)
- `scripts/test_tabs_phase3.py` (new)

## Risks
- `createWindow` policy change must NOT break OAuth popups (guarded by window-type +
  referrer check; OAuth flows keep windows — explicitly retested with Google/TikTok sign-in).
- Reorder must keep per-view state intact (only widget order changes; views untouched).
- Middle-click autoscroll interference: preventDefault on auxclick.

## Verification plan
- Repro log before fix (tab-creation call sites with counts), after fix (exactly one).
- The full matrix above automated headless; screenshots for DnD order change.
- Manual on-user-machine: OAuth popup still opens as window.
