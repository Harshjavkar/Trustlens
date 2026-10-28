"""Ask: embed -> top-20 -> LLM rerank to 5 -> Legitimacy Report (parallel) -> score breakdown."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import db, llm, verify

LEG = {"supported": 1.0, "mixed": 0.5, "insufficient": 0.3, "opinion": 0.4, "contradicted": 0.0}


def quote(text, n=15):
    return " ".join(text.split()[:n])


def recency(published):
    try:
        d = (datetime.now(timezone.utc) - datetime.fromisoformat(published)).days
    except Exception:
        return 0.5
    return 1 / (1 + max(d, 0) / 90)


def rerank(question, cands):
    items = [{"id": c["id"], "text": c["text"][:300]} for _, c in cands]
    try:
        ids = llm.chat('Rank these transcript chunks by how well they answer the question. Return JSON only: '
                       '{"ranking":[ids best first]}', f"Question: {question}\nChunks: {json.dumps(items)}",
                       as_json=True)["ranking"]
        order = {i: n for n, i in enumerate(ids)}
        cands = sorted(cands, key=lambda sc: order.get(sc[1]["id"], 999))
    except Exception:
        pass  # keep cosine order
    return cands[:5]


def score(rel, trust, rep, rec):
    leg = LEG[rep["verdict"]] * rep["confidence"]
    parts = {"relevance": rel, "trust": trust / 5, "legitimacy": leg, "consensus": rep["consensus"], "recency": rec}
    total = 0.35 * rel + 0.2 * trust / 5 + 0.3 * leg + 0.1 * rep["consensus"] + 0.05 * rec
    return total, parts


def ask(question):
    db.remember("question", "ask", question)
    top = db.top_chunks(llm.embed([question])[0], 20)
    if not top:
        return []
    top5 = rerank(question, top)
    with ThreadPoolExecutor(5) as ex:
        reps = list(ex.map(lambda sc: verify.report(sc[1]), top5))
    out = []
    for (sim, c), rep in zip(top5, reps):
        total, parts = score(max(0.0, min(1.0, sim)), c["trust"], rep, recency(c["published"]))
        out.append({"chunk_id": c["id"], "video_id": c["video_id"], "title": c["title"], "creator": c["creator"],
                    "start": int(c["start"]),
                    "url": (f"https://www.instagram.com/p/{c['video_id'].split(':',1)[1]}/" if c['video_id'].startswith('ig:') else f"https://www.youtube.com/watch?v={c['video_id']}&t={int(c['start'])}s"),
                    "quote": quote(c["text"]), "score": round(total, 3),
                    "breakdown": {k: round(v, 3) for k, v in parts.items()}, "report": rep,
                    "warning": "Contradicted by cross-checked evidence" if rep["verdict"] == "contradicted" else None})
    out.sort(key=lambda r: (r["report"]["verdict"] == "contradicted", -r["score"]))
    return out
