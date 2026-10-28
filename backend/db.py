"""Local SQLite storage. All user data stays on this machine."""
import os, sqlite3, time
import numpy as np
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "trustlens.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS creators(id INTEGER PRIMARY KEY, channel_id TEXT UNIQUE, name TEXT, url TEXT, trust REAL);
CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY, creator_id INTEGER, title TEXT, published TEXT);
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, video_id TEXT, creator_id INTEGER, start REAL, text TEXT, emb BLOB, created_at REAL);
CREATE TABLE IF NOT EXISTS memory(id INTEGER PRIMARY KEY, kind TEXT, key TEXT, value TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS reports(claim_hash TEXT PRIMARY KEY, data TEXT, ts REAL);
"""


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.executescript(SCHEMA)


def q(sql, args=()):
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def x(sql, args=()):
    with _conn() as c:
        return c.execute(sql, args).lastrowid


def remember(kind, key, value):
    x("INSERT INTO memory(kind,key,value,ts) VALUES(?,?,?,?)", (kind, key, str(value), time.time()))


def pack(vec):
    return np.asarray(vec, dtype=np.float32).tobytes()


def unpack(blob):
    return np.frombuffer(blob, dtype=np.float32)


CHUNK_SQL = """SELECT c.id, c.video_id, c.creator_id, c.start, c.text, c.emb, c.created_at,
 v.title, v.published, cr.name AS creator, cr.trust
 FROM chunks c JOIN videos v ON v.id=c.video_id JOIN creators cr ON cr.id=c.creator_id"""


def top_chunks(qvec, k, exclude_creator=None):
    """Cosine similarity over stored embeddings -> [(similarity, chunk_row)]."""
    rows = q(CHUNK_SQL + (" WHERE c.creator_id != ?" if exclude_creator else ""),
             (exclude_creator,) if exclude_creator else ())
    if not rows:
        return []
    m = np.stack([unpack(r["emb"]) for r in rows])
    qv = np.asarray(qvec, dtype=np.float32)
    sims = (m @ qv) / (np.linalg.norm(m, axis=1) * np.linalg.norm(qv) + 1e-9)
    order = np.argsort(-sims)[:k]
    return [(float(sims[i]), rows[i]) for i in order]


def get_chunk(chunk_id):
    r = q(CHUNK_SQL + " WHERE c.id=?", (chunk_id,))
    return r[0] if r else None
