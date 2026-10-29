"""Seed demo creators and ingest their latest videos.

Usage: python scripts/seed.py [backend_url]
The backend must be running (uvicorn main:app --port 8000 from backend/).
"""
import sys
import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

# Demo creators: a mix of YouTube and Instagram
DEMO = [
    ("https://www.youtube.com/@veritasium", 4, "Science explainers"),
    ("https://www.youtube.com/@kurzgesagt", 4, "Animated science"),
    ("https://www.instagram.com/natgeo", 3, "Nature photography"),
]

print(f"Seeding TrustLens at {BASE}\n")

# Check backend health
try:
    health = httpx.get(f"{BASE}/health", timeout=5).json()
    print(f"Backend online: {health['creators']} creators, {health['chunks']} chunks\n")
except Exception as e:
    print(f"⚠ Backend not reachable at {BASE}: {e}")
    print("  Start it first:  cd backend && uvicorn main:app --port 8000")
    sys.exit(1)

for url, trust, desc in DEMO:
    print(f"Adding {desc}: {url} (trust={trust})...")
    try:
        r = httpx.post(f"{BASE}/creators", json={"channel_url": url, "trust": trust}, timeout=60)
        r.raise_for_status()
        name = r.json().get("name", "?")
        print(f"  ✓ {name}")
    except Exception as e:
        print(f"  ✗ {e}")

print("\nIngesting latest videos (this may take a few minutes)...")
try:
    r = httpx.post(f"{BASE}/ingest", json={"n": 5}, timeout=900)
    r.raise_for_status()
    stats = r.json()
    print(f"  ✓ {stats['videos']} videos ingested, {stats['chunks']} chunks created, {stats['skipped_no_captions']} skipped")
except Exception as e:
    print(f"  ✗ {e}")

print("\nDone! Open Chrome and load the extension from extension/ to start asking questions.")
