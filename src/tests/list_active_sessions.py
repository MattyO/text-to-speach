import requests
import json

BASE_URL = "http://127.0.0.1:49374"
auth = ('opencode', 'fqUNPvqO_waOJrho0uBNBblChK1ERBiUhvTp2KHn_js')

# Get sessions
sessions = requests.get(f"{BASE_URL}/api/session", auth=auth).json()

# Filter for active/busy sessions
active = [s for s in sessions['data'] if s.get('time', {}).get('busy')]
print(f"=== Active Sessions ({len(active)}) ===")
for s in active:
    print(f"  {s['id']}: {s['title']} (agent: {s['agent']}, project: {s['projectID']})")

if not active:
    print("  No active sessions")

print(f"\n=== All Sessions ({len(sessions['data'])}) ===")
for s in sessions['data']:
    busy = s.get('time', {}).get('busy', False)
    prefix = "[BUSY] " if busy else "       "
    print(f"  {prefix}{s['id']}: {s['title']}")
