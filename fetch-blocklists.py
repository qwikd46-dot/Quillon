#!/usr/bin/env python3
"""Fetch and update blocklists for BFSB."""
import requests
from pathlib import Path
from datetime import datetime

BLOCKLIST_DIR = Path.home() / ".bfsb" / "blocklists"

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")

urls = {
    "stevenblack": "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
    "urlhaus": "https://urlhaus.abuse.ch/downloads/hostfile/",
    "phishtank": "https://raw.githubusercontent.com/mitchellkrogza/Phishing.Database/master/phishing-domains-ACTIVE.txt",
}

all_domains = set()

for name, url in urls.items():
    log(f"Fetching {name}...")
    try:
        resp = requests.get(url, timeout=60)
        count = 0
        for line in resp.text.split("\n"):
            line = line.strip().lower()
            if name == "phishtank":
                if line and not line.startswith("#") and "." in line:
                    all_domains.add(line)
                    count += 1
            elif line.startswith(("0.0.0.0 ", "0.0.1 ", "0.0.0.0\t", "127.0.0.1\t")):
                parts = line.split()
                if len(parts) >= 2 and parts[1] != "localhost":
                    all_domains.add(parts[1])
                    count += 1
        log(f"  {name}: {count:,} domains")
    except Exception as e:
        log(f"  {name}: FAILED - {e}")

combined = BLOCKLIST_DIR / "combined.txt"
with open(combined, "w") as f:
    f.write("\n".join(sorted(all_domains)))

log(f"Total {len(all_domains):,} blocked domains saved to {combined}")
log("Done.")
