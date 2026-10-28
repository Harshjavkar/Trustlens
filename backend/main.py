"""TrustLens API. Run: uvicorn main:app --reload (from backend/)."""
import json, time
from typing import Literal
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import ask as ask_mod, db, ingest, llm, verify

db.init()
app = FastAPI(title="TrustLens")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class CreatorIn(BaseModel):
    channel_url: str
    trust: int = Field(3, ge=1, le=5)


class IngestIn(BaseModel):
    n: int = 5


class AskIn(BaseModel):
    q: str


class VerifyIn(BaseModel):
    chunk_id: int


class FeedbackIn(BaseModel):
    chunk_id: int
    vote: Literal["up", "down"]


@app.post("/creators")
def post_creator(b: CreatorIn):
    try:
        return ingest.add_creator(b.channel_url, b.trust)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/ingest")
def post_ingest(b: IngestIn = IngestIn()):
    return ingest.ingest_all(b.n)


@app.post("/ask")
def post_ask(b: AskIn):
    return {"results": ask_mod.ask(b.q)}


@app.post("/verify")
def post_verify(b: VerifyIn):
    r = verify.verify_chunk(b.chunk_id)
    if not r:
        raise HTTPException(404, "chunk not found")
    return r


@app.get("/digest")
def get_digest():
    rows = db.q(db.CHUNK_SQL.replace("c.emb,", "") + " WHERE c.created_at > ? ORDER BY c.created_at DESC LIMIT 60",
                (time.time() - 7 * 86400,))
    if not rows:
        return {"topics": []}
    by_id = {r["id"]: r for r in rows}
    try:
        groups = llm.chat('Group these chunks by topic. Return JSON only: {"topics":[{"topic":"...","chunk_ids":[...]}]}',
                          json.dumps([{"id": r["id"], "text": r["text"][:200]} for r in rows]), as_json=True)["topics"]
    except Exception:
        groups = [{"topic": "New this week", "chunk_ids": list(by_id)}]
    return {"topics": [{"topic": g["topic"], "items": [
        {"chunk_id": i, "title": by_id[i]["title"], "creator": by_id[i]["creator"], "quote": ask_mod.quote(by_id[i]["text"]),
          "url": (f"https://www.instagram.com/p/{by_id[i]['video_id'].split(':',1)[1]}/" if by_id[i]['video_id'].startswith('ig:') else f"https://www.youtube.com/watch?v={by_id[i]['video_id']}&t={int(by_id[i]['start'])}s")}
        for i in g["chunk_ids"] if i in by_id]} for g in groups]}


@app.post("/feedback")
def post_feedback(b: FeedbackIn):
    c = db.get_chunk(b.chunk_id)
    if not c:
        raise HTTPException(404, "chunk not found")
    db.remember("feedback", str(b.chunk_id), b.vote)
    trust = min(5.0, max(1.0, c["trust"] + (0.1 if b.vote == "up" else -0.1)))
    db.x("UPDATE creators SET trust=? WHERE id=?", (trust, c["creator_id"]))
    return {"creator": c["creator"], "trust": round(trust, 2)}
