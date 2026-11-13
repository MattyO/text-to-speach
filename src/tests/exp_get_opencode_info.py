import requests
import json
from pprint import pprint

BASE_URL = "http://127.0.0.1:49374"
auth = ('opencode', 'fqUNPvqO_waOJrho0uBNBblChK1ERBiUhvTp2KHn_js')

# Get sessions
sessions = requests.get(f"{BASE_URL}/api/info", auth=auth).json()

# Get sessions
sessions = requests.get(f"{BASE_URL}/api/session", auth=auth).json()

# Filter for currently active (open) windows/sessions
active_sessions = [s for s in sessions['data'] if s.get('time', {}).get('busy')]

from datetime import datetime

active_sessions.sort(key=lambda s: s['time']['updated'], reverse=True)

print(f"Open opencode windows: {len(active_sessions)}")
for s in active_sessions:
    pprint(active_sessions)
    updated = datetime.fromtimestamp(s['time']['updated'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
    print(f"  Updated {updated}: {s['title']}")
    print(f"    ID: {s['id']}")
    print(f"    Agent: {s['agent']}")
    print(f"    Project: {s['projectID']}")
    print()
