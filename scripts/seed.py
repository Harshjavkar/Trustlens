"""Seed 3 demo creators and ingest their latest videos. Backend must be running."""
import sys
import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
DEMO = ["https://www.youtube.com/@veritasium", "https://www.youtube.com/@kurzgesagt", "https://www.youtube.com/@3blue1brown"]

for url in DEMO:
    r = httpx.post(f"{BASE}/creators", json={"channel_url": url, "trust": 4}, timeout=60)
    print(url, r.status_code, r.json().get("name", r.text))
print(httpx.post(f"{BASE}/ingest", json={"n": 5}, timeout=900).json())
