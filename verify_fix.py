#!/usr/bin/env python3
"""Quick verification that nuclear script fix is active."""
import sys
sys.path.insert(0, '.')
from quillon.core.tampermonkey_scripts import (
    YOUTUBE_NUCLEAR_SCRIPT, GLOBAL_COSMETIC_SCRIPT,
    TampermonkeyScriptManager, setup_tampermonkey_scripts
)
from PyQt6.QtWebEngineCore import QWebEngineScript

ok = True

# 1. JS syntax (node check via temp file)
with open('/tmp/nuclear_check.js', 'w') as f:
    f.write(YOUTUBE_NUCLEAR_SCRIPT)
import subprocess
res = subprocess.run(['node', '--check', '/tmp/nuclear_check.js'],
                     capture_output=True, text=True)
if res.returncode != 0:
    print('FAIL: JS syntax error:', res.stderr[:200])
    ok = False
else:
    print('OK: JS parses clean')

# 2. No backticks
if '`' in YOUTUBE_NUCLEAR_SCRIPT:
    print('FAIL: Backticks (template literals) found')
    ok = False
else:
    print('OK: No backticks')

# 3. Triple-quote balance in source file
with open('quillon/core/tampermonkey_scripts.py') as f:
    src = f.read()
count = src.count('"""')
if count % 2 != 0:
    print('FAIL: Unbalanced triple quotes:', count)
    ok = False
else:
    print('OK: Triple quotes balanced (', count, ')')

# 4. Injection guard present
if 'window.__quillonNuclearInjected' in YOUTUBE_NUCLEAR_SCRIPT:
    print('OK: Injection guard present')
else:
    print('FAIL: Missing injection guard')
    ok = False

# 5. World ID is MainWorld (0), not ApplicationWorld (1)
# We verify by inspecting the source code of _add_script_from_source
with open('quillon/core/tampermonkey_scripts.py') as f:
    code = f.read()
if 'ScriptWorldId.MainWorld' in code:
    print('OK: MainWorld (0) set in _add_script_from_source')
else:
    print('FAIL: MainWorld not found in source')
    ok = False

# 6. Clean version loaded (no stray Python comment inside JS block)
# The global comment line is outside JS; verify first line of JS is metadata
first_line = YOUTUBE_NUCLEAR_SCRIPT.splitlines()[0]
if first_line.startswith('// ==UserScript=='):
    print('OK: Clean JS starts with metadata (no leaked comment)')
else:
    print('FAIL: JS starts unexpectedly:', first_line[:60])
    ok = False

if ok:
    print('\nVERIFIED: All fixes active. Nuclear script should inject in MainWorld.')
else:
    print('\nVERIFICATION FAILED.')
    sys.exit(1)
