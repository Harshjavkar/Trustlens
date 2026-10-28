"""Creators + post/caption ingestion: Instagram/YouTube posts -> captions/transcripts -> ~60s/text chunks -> embeddings -> SQLite."""
import re, time
import xml.etree.ElementTree as ET
import httpx
try:
    from youtube_transcript_api import YouTubeTranscriptApi
except Exception:
    YouTubeTranscriptApi = None
try:
    import instaloader
except Exception:
    instaloader = None
import db, llm

UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en"}
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


def add_creator(channel_url, trust):
    url = channel_url.strip()
    # Instagram
    m = re.search(r"instagram\.com/([A-Za-z0-9._]+)", url)
    if m and m.group(1) not in ("p", "reels", "tv"):
        username = m.group(1)
        cid = f"ig:{username}"
        name = username
        if db.q("SELECT id FROM creators WHERE channel_id=?", (cid,)):
            db.x("UPDATE creators SET trust=? WHERE channel_id=?", (trust, cid))
        else:
            db.x("INSERT INTO creators(channel_id,name,url,trust) VALUES(?,?,?,?)", (cid, name, url, float(trust)))
        return db.q("SELECT * FROM creators WHERE channel_id=?", (cid,))[0]
    # YouTube
    m = re.search(r"/channel/(UC[\w-]{22})", url)
    html = ""
    if not m:
        try:
            html = httpx.get(url, headers=UA, cookies={"CONSENT": "YES+1"}, follow_redirects=True, timeout=20).text
        except Exception:
            html = ""
        m = re.search(r'"(?:externalId|channelId)":"(UC[\w-]{22})"', html)
    if not m:
        raise ValueError("Could not resolve channel id from URL")
    cid = m.group(1)
    n = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    name = n.group(1) if n else cid
    if db.q("SELECT id FROM creators WHERE channel_id=?", (cid,)):
        db.x("UPDATE creators SET trust=? WHERE channel_id=?", (trust, cid))
    else:
        db.x("INSERT INTO creators(channel_id,name,url,trust) VALUES(?,?,?,?)", (cid, name, url, float(trust)))
    return db.q("SELECT * FROM creators WHERE channel_id=?", (cid,))[0]


def latest_videos(channel_id, n):
    if channel_id.startswith("ig:"):
        username = channel_id.split(":", 1)[1]
        items = []
        try:
            if instaloader:
                L = instaloader.Instaloader(download_pictures=False, download_videos=False, download_video_thumbnails=False, save_metadata=False, quiet=True)
                profile = instaloader.Profile.from_username(L.context, username)
                count = 0
                for post in profile.get_posts():
                    if count >= n:
                        break
                    pid = post.shortcode
                    items.append({"id": f"ig:{pid}", "title": (post.caption or "")[:200].replace("\n", " "), "published": post.date_utc.isoformat() if post.date_utc else ""})
                    count += 1
        except Exception:
            pass
        return items
    try:
        r = httpx.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}", headers=UA, timeout=20)
        root = ET.fromstring(r.text)
        return [{"id": e.find("yt:videoId", NS).text, "title": e.find("a:title", NS).text,
                 "published": e.find("a:published", NS).text} for e in root.findall("a:entry", NS)[:n]]
    except Exception:
        return []


def transcript(video_id):
    """[(text, start_seconds)] or None if no captions."""
    if video_id.startswith("ig:"):
        return None
    if not YouTubeTranscriptApi:
        return None
    try:
        api = YouTubeTranscriptApi()
        snips = api.fetch(video_id) if hasattr(api, "fetch") else YouTubeTranscriptApi.get_transcript(video_id)
        return [(s.text, s.start) if hasattr(s, "text") else (s["text"], s["start"]) for s in snips]
    except Exception:
        return None


def caption_ig(video_id):
    if not video_id.startswith("ig:"):
        return None
    shortcode = video_id.split(":", 1)[1]
    try:
        if instaloader:
            L = instaloader.Instaloader(download_pictures=False, download_videos=False, download_video_thumbnails=False, save_metadata=False, quiet=True)
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            cap = post.caption or ""
            if cap.strip():
                return cap
    except Exception:
        pass
    return None


def chunk(snips, window=60):
    if not snips:
        return []
    out, cur, start = [], [], None
    for text, t in snips:
        if start is None:
            start = t
        if t - start >= window and cur:
            out.append((start, " ".join(cur)))
            cur, start = [], t
        cur.append(str(text).replace("\n", " "))
    if cur:
        out.append((start, " ".join(cur)))
    return out


def chunk_text(text, window_chars=1000):
    if not text:
        return []
    text = text.replace("\n", " ")
    parts = []
    start = 0
    i = 0
    while i < len(text):
        # find approx window end
        end = min(len(text), start + window_chars)
        # try to break on space
        if end < len(text):
            j = text.rfind(' ', start, end)
            if j > start:
                end = j
        parts.append((float(start / 40.0), text[start:end].strip()))  # fake start seconds
        start = end + 1
        i = start
        if start >= len(text):
            break
    return [(s, t) for s, t in parts if t]


def ingest_all(n=5):
    stats = {"videos": 0, "chunks": 0, "skipped_no_captions": 0}
    for cr in db.q("SELECT * FROM creators"):
        for v in latest_videos(cr["channel_id"], n):
            if db.q("SELECT id FROM videos WHERE id=?", (v["id"],)):
                continue
            db.x("INSERT INTO videos(id,creator_id,title,published) VALUES(?,?,?,?)",
                 (v["id"], cr["id"], v["title"], v["published"]))
            snips = transcript(v["id"])
            if snips:
                parts = chunk(snips)
            else:
                cap = caption_ig(v["id"])
                if not cap:
                    stats["skipped_no_captions"] += 1
                    continue
                parts = chunk_text(cap)
            if not parts:
                stats["skipped_no_captions"] += 1
                continue
            vecs = llm.embed([p[1] for p in parts])
            for (start, text), vec in zip(parts, vecs):
                db.x("INSERT INTO chunks(video_id,creator_id,start,text,emb,created_at) VALUES(?,?,?,?,?,?)",
                     (v["id"], cr["id"], float(start), text, db.pack(vec), time.time()))
            stats["videos"] += 1
            stats["chunks"] += len(parts)
    return stats
