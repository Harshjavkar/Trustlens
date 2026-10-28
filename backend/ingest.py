"""Creators + transcript ingestion: RSS -> transcripts -> ~60s chunks -> embeddings -> SQLite."""
import re, time
import xml.etree.ElementTree as ET
import httpx
from youtube_transcript_api import YouTubeTranscriptApi
import db, llm

UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en"}
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


def add_creator(channel_url, trust):
    m = re.search(r"/channel/(UC[\w-]{22})", channel_url)
    html = ""
    if not m:
        html = httpx.get(channel_url, headers=UA, cookies={"CONSENT": "YES+1"}, follow_redirects=True, timeout=20).text
        m = re.search(r'"(?:externalId|channelId)":"(UC[\w-]{22})"', html)
    if not m:
        raise ValueError("Could not resolve channel id from URL")
    cid = m.group(1)
    n = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    name = n.group(1) if n else cid
    if db.q("SELECT id FROM creators WHERE channel_id=?", (cid,)):
        db.x("UPDATE creators SET trust=? WHERE channel_id=?", (trust, cid))
    else:
        db.x("INSERT INTO creators(channel_id,name,url,trust) VALUES(?,?,?,?)", (cid, name, channel_url, float(trust)))
    return db.q("SELECT * FROM creators WHERE channel_id=?", (cid,))[0]


def latest_videos(channel_id, n):
    r = httpx.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}", headers=UA, timeout=20)
    root = ET.fromstring(r.text)
    return [{"id": e.find("yt:videoId", NS).text, "title": e.find("a:title", NS).text,
             "published": e.find("a:published", NS).text} for e in root.findall("a:entry", NS)[:n]]


def transcript(video_id):
    """[(text, start_seconds)] or None if no captions."""
    try:
        api = YouTubeTranscriptApi()
        snips = api.fetch(video_id) if hasattr(api, "fetch") else YouTubeTranscriptApi.get_transcript(video_id)
        return [(s.text, s.start) if hasattr(s, "text") else (s["text"], s["start"]) for s in snips]
    except Exception:
        return None


def chunk(snips, window=60):
    out, cur, start = [], [], None
    for text, t in snips:
        if start is None:
            start = t
        if t - start >= window and cur:
            out.append((start, " ".join(cur)))
            cur, start = [], t
        cur.append(text.replace("\n", " "))
    if cur:
        out.append((start, " ".join(cur)))
    return out


def ingest_all(n=5):
    stats = {"videos": 0, "chunks": 0, "skipped_no_captions": 0}
    for cr in db.q("SELECT * FROM creators"):
        for v in latest_videos(cr["channel_id"], n):
            if db.q("SELECT id FROM videos WHERE id=?", (v["id"],)):
                continue
            db.x("INSERT INTO videos(id,creator_id,title,published) VALUES(?,?,?,?)",
                 (v["id"], cr["id"], v["title"], v["published"]))
            snips = transcript(v["id"])
            if not snips:
                stats["skipped_no_captions"] += 1
                continue
            parts = chunk(snips)
            vecs = llm.embed([p[1] for p in parts])
            for (start, text), vec in zip(parts, vecs):
                db.x("INSERT INTO chunks(video_id,creator_id,start,text,emb,created_at) VALUES(?,?,?,?,?,?)",
                     (v["id"], cr["id"], start, text, db.pack(vec), time.time()))
            stats["videos"] += 1
            stats["chunks"] += len(parts)
    return stats
