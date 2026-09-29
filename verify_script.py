#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/binwalk/Downloads/bfsb')

from quillon.core.tampermonkey_scripts import YOUTUBE_NUCLEAR_SCRIPT

# Check for template literals (backticks)
if '`' in YOUTUBE_NUCLEAR_SCRIPT:
    print('ERROR: Backticks found in script')
else:
    print('OK: No backticks (template literals) in script')

# Check for balanced braces
open_braces = YOUTUBE_NUCLEAR_SCRIPT.count('{')
close_braces = YOUTUBE_NUCLEAR_SCRIPT.count('}')
print(f'Braces: {open_braces} open, {close_braces} close, balanced: {open_braces == close_braces}')

# Check for balanced parentheses
open_paren = YOUTUBE_NUCLEAR_SCRIPT.count('(')
close_paren = YOUTUBE_NUCLEAR_SCRIPT.count(')')
print(f'Parens: {open_paren} open, {close_paren} close, balanced: {open_paren == close_paren}')

# Check for balanced brackets
open_brack = YOUTUBE_NUCLEAR_SCRIPT.count('[')
close_brack = YOUTUBE_NUCLEAR_SCRIPT.count(']')
print(f'Brackets: {open_brack} open, {close_brack} close, balanced: {open_brack == close_brack}')

# Check for balanced double quotes
open_dq = YOUTUBE_NUCLEAR_SCRIPT.count('"')
print(f'Double quotes: {open_dq} (even: {open_dq % 2 == 0})')

# Check for balanced single quotes
open_sq = YOUTUBE_NUCLEAR_SCRIPT.count("'")
print(f'Single quotes: {open_sq} (even: {open_sq % 2 == 0})')

print(f'Script length: {len(YOUTUBE_NUCLEAR_SCRIPT)} chars')
print('First 200 chars:', repr(YOUTUBE_NUCLEAR_SCRIPT[:200]))
print('Last 200 chars:', repr(YOUTUBE_NUCLEAR_SCRIPT[-200:]))

# Search for any stray Python comment that leaked
if '# ' in YOUTUBE_NUCLEAR_SCRIPT:
    print('WARNING: Python comment (#) found in JS')
    for i, line in enumerate(YOUTUBE_NUCLEAR_SCRIPT.split('\n')):
        if '# ' in line and not line.strip().startswith('//'):
            print(f'  Line {i}: {line[:100]}')