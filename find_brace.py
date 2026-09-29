#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/binwalk/Downloads/bfsb')

from quillon.core.tampermonkey_scripts import YOUTUBE_NUCLEAR_SCRIPT

# Find the brace mismatch by tracking balance line by line
balance = 0
for i, line in enumerate(YOUTUBE_NUCLEAR_SCRIPT.split('\n'), 1):
    balance += line.count('{')
    balance -= line.count('}')
    if balance < 0:
        print(f'Line {i}: NEGATIVE balance ({balance}) - {line[:80]}')
    elif balance > 10:
        print(f'Line {i}: High balance ({balance}) - {line[:80]}')

print(f'Final balance: {balance}')

# Also check for the specific area around the mismatch
# Let's look at the last part of the script more carefully
lines = YOUTUBE_NUCLEAR_SCRIPT.split('\n')
for i in range(max(0, len(lines)-30), len(lines)):
    bal = 0
    for l in lines[:i+1]:
        bal += l.count('{') - l.count('}')
    print(f'Line {i+1} (bal={bal}): {lines[i]}')