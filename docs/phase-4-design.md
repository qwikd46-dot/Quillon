# docs/phase-4-design.md — Visual unification (search bar, menu, universal sidebar)

Status: awaiting approval (part of whole-plan approval).

## Current state (code-verified)
- **Qt chrome bar** (`browser_chrome.py`, shown only on external sites): `← → ⟳` ghost
  buttons, pill `QLineEdit` (`#141933`, border `#232a4a`, radius 20, 14 px), star
  `☆/★`, **⋮ menu button**.
- **HTML address bar** (Home/Results, `bfsb_combined.html`): same pill colors/radius
  but with a **search icon** on the left inside the pill, **bookmark icon** inside the
  pill (fills violet when bookmarked), **lock icon** on the right, and the nav buttons
  sit *outside* the pill on the left; the whole row is optically centered with a
  `-114px` shift.

Differences to fix: icon set (search/lock missing in Qt bar), icon placement (inside
vs outside the pill), menu button (Qt-only), bookmark behavior (HTML toggles +
fills; Qt toggles via star glyph — visually different), row alignment.

## Proposed change

### 4a. Qt search bar == HTML search bar (pixel parity)
- Rebuild `NativeNavBar` pill to the exact HTML spec: same radius (22 px), same
  paddings (11/18), same font size, same `#141933`/`#232a4a`/focus `#7b5cff`;
  **search icon inside-left**, **bookmark icon inside-right** (SVG, same path as the
  template's, fills `#7b5cff` when bookmarked — same behavior as HTML: toggles
  bookmark of the current page), **lock icon right** (switches to plain when http).
- `← → ⟳` remain outside the pill on the left (same ghost style as HTML's nav buttons).
- **Menu button removed from the Qt bar entirely** (per requirement). Its functions
  live in the universal sidebar (4b) — no functionality lost: new tab, panels, about,
  quit move there; quit also stays on Ctrl+Q.
- HTML side: keep as-is (it is the reference design; only alignment polish if the
  screenshots show drift).

### 4b. Universal sidebar (Home, Results, and while browsing)
- The HTML sidebar stays the single implementation for BFSB pages (Home/Results).
- While browsing external sites, a **native Qt sidebar overlay** slides over the
  webview: same 232 px collapsed→64 px behavior, same palette/tokens
  (`#0a0e1a`, `#7b5cff` accent, same items: Home, Bookmarks, History, Downloads,
  Safe Browsing, Passwords, Settings). It is a real QWidget (never injects into page
  DOM — no cross-origin styling conflicts), opened from a **hamburger button at the
  far left of the Qt search bar** (since the ⋮ is removed).
  - Panel items reuse the menu/panel routing that exists (`home_url#panel`,
    `_open_panel`), so Bookmarks/History/Downloads/Settings behave identically.
  - "Home" item navigates the current tab to the BFSB GUI.
  - Adblocker toggle in the overlay calls the same `/api/adblock` endpoint.
- One navigation model: same items, same order, same actions in HTML sidebar (own
  pages) and native overlay (external sites).

## Files touched
- `bfsb/ui/browser_chrome.py` (pill rebuild, icons, hamburger, remove menu button)
- `bfsb/ui/main_window.py` (wire hamburger → overlay; remove menu-button wiring;
  `_show_main_menu` kept only for the context-menu fallback)
- new `bfsb/ui/side_overlay.py` (native overlay sidebar)
- `bfsb/templates/bfsb_combined.html` (alignment polish only, if screenshots demand)

## Risks
- Overlay must not steal focus/keys from the page (non-modal, click-outside closes).
- Pixel parity across Qt styles: pin with explicit stylesheets + fixed metrics; verify
  via screenshots at 2 window sizes.
- Removing ⋮ could strand "Quit" — keep Ctrl+Q + Quit inside overlay footer.

## Verification plan
- Before/after screenshots (offscreen `QWidget.grab()` + CDP screenshots of HTML
  side) at default and collapsed sidebar states — **shown for sign-off** as required.
- Automated: hamburger opens overlay; overlay items navigate; adblock toggle hits
  `/api/adblock`; menu button absent from bar (object tree assertion).
- Regression: tab suite + bookmark star behavior on external sites.
