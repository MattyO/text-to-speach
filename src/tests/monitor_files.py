#!/usr/bin/env python3
"""Monitor ~/recordings for file creation."""
import os
import time
import sys

RECORD_DIR = os.path.expanduser("~/recordings")
seen = set()

def list_files():
    try:
        return set(f for f in os.listdir(RECORD_DIR) if f.startswith("recording_") and f.endswith(".wav"))
    except FileNotFoundError:
        return set()

print(f"Monitoring {RECORD_DIR}...")
sys.stdout.flush()

while True:
    current = list_files()
    new = current - seen
    if new:
        for f in sorted(new):
            path = os.path.join(RECORD_DIR, f)
            size = os.path.getsize(path)
            print(f"NEW FILE: {f} ({size} bytes) @ {time.strftime('%H:%M:%S')}")
            sys.stdout.flush()
    seen = current
    time.sleep(0.05)
