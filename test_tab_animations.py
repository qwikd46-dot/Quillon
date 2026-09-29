"""
Playwright test to verify tab animations work correctly:
- Open browser
- Add tabs
- Remove tabs
- Verify no JS errors in console
- Verify tab count updates correctly
"""
import sys
from playwright.sync_api import sync_playwright

APP_URL = "http://localhost:8889"

CHECKS = [
    ("page loads", 
     lambda p: p.goto(APP_URL, timeout=10000),
     lambda p: p.locator(".quillon-tab-bar").count() > 0),
    
    ("initial tab count is 1",
     lambda p: None,
     lambda p: p.locator(".quillon-tab").count() == 1),
    
    ("add first new tab",
     lambda p: p.click("#newTabBtn"),
     lambda p: p.locator(".quillon-tab").count() == 2),
    
    ("add second new tab",
     lambda p: p.click("#newTabBtn"),
     lambda p: p.locator(".quillon-tab").count() == 3),
    
    ("switch to tab 1",
     lambda p: p.locator(".quillon-tab").nth(1).click(),
     lambda p: p.locator(".quillon-tab.active").count() == 1),
    
    ("close tab 2 (middle tab)",
     lambda p: p.locator(".quillon-tab").nth(2).locator(".quillon-tab-close").click(),
     lambda p: p.locator(".quillon-tab").count() == 2),
    
    ("close tab 1 (now last tab)",
     lambda p: p.locator(".quillon-tab").nth(1).locator(".quillon-tab-close").click(),
     lambda p: p.locator(".quillon-tab").count() == 1),
    
    ("add tab after closing all but one",
     lambda p: p.click("#newTabBtn"),
     lambda p: p.locator(".quillon-tab").count() == 2),
]

def run_checks():
    results = []
    console_errors = []
    
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: console_errors.append(f"PAGE ERROR: {err}"))
        
        page.goto(APP_URL, timeout=10000)
        page.wait_for_timeout(500)
        
        for desc, action, check in CHECKS:
            try:
                if action:
                    action(page)
                    page.wait_for_timeout(300)  # let animations settle
                passed = check(page)
                results.append((desc, passed, None))
            except Exception as e:
                results.append((desc, False, str(e)))
        
        browser.close()
    
    print("=== UI CHECK RESULTS ===")
    all_passed = True
    for desc, passed, err in results:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False
        print(f"[{status}] {desc}" + (f" — {err}" if err else ""))
    
    if console_errors:
        print("\n=== CONSOLE ERRORS DETECTED ===")
        for e in console_errors:
            print(e)
        all_passed = False
    else:
        print("\n=== NO CONSOLE ERRORS ===")
    
    sys.exit(0 if all_passed else 1)

if __name__ == "__main__":
    run_checks()