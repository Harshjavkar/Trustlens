"""Legitimacy pipeline: extract_claim -> gather_evidence -> judge -> red_flags. Evidence-based cross-checking only."""
import hashlib, json, os, re, time
from concurrent.futures import ThreadPoolExecutor
import httpx
from dotenv import load_dotenv
import db, llm

load_dotenv()
VERDICTS = {"supported", "mixed", "contradicted", "insufficient"}


def extract_claim(text):
    out = llm.chat('Extract the single main checkable claim from this transcript excerpt. Return JSON only: '
                   '{"claim": "<one sentence>", "type": "fact|opinion|prediction|advice"}', text, as_json=True)
    t = out.get("type") if out.get("type") in {"fact", "opinion", "prediction", "advice"} else "opinion"
    return {"claim": str(out.get("claim", "")).strip(), "type": t}


def web_search(query):
    key, prov = os.getenv("SEARCH_API_KEY"), os.getenv("SEARCH_PROVIDER", "tavily")
    if not key:
        return []
    try:
        if prov == "brave":
            r = httpx.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": 5},
                          headers={"X-Subscription-Token": key}, timeout=20).json()
            return [{"url": i["url"], "snippet": i.get("description", "")} for i in r.get("web", {}).get("results", [])]
        r = httpx.post("https://api.tavily.com/search", json={"api_key": key, "query": query, "max_results": 5},
                       timeout=20).json()
        return [{"url": i["url"], "snippet": i.get("content", "")} for i in r.get("results", [])]
    except Exception:
        return []


def gather_evidence(claim, creator_id):
    ev = []
    qvec = llm.embed([claim])[0]
    for sim, c in db.top_chunks(qvec, 5, exclude_creator=creator_id):
        ev.append({"id": f"chunk:{c['id']}", "source_type": "creator", "text": c["text"][:400],
                   "meta": f"{c['creator']} - {c['title']}"})
    try:
        queries = llm.chat('Write 3 diverse web search queries to fact-check this claim. Return JSON only: '
                           '{"queries": ["...", "...", "..."]}', claim, as_json=True)["queries"][:3]
    except Exception:
        queries = [claim]
    seen = set()
    with ThreadPoolExecutor(3) as ex:
        for results in ex.map(web_search, queries):
            for r in results:
                if r["url"] not in seen and len(seen) < 5:
                    seen.add(r["url"])
                    ev.append({"id": r["url"], "source_type": "web", "text": r["snippet"][:400]})
    return ev


def judge(claim, ev):
    if not ev:
        return {"verdict": "insufficient", "confidence": 0.2, "reason": "No evidence retrieved.", "evidence": []}
    listing = json.dumps([{"id": e["id"], "text": e["text"]} for e in ev])
    out = llm.chat(
        "You cross-check a claim ONLY against the evidence provided. Never use outside knowledge or invent sources. "
        "If evidence is thin or off-topic answer insufficient. Return JSON only: "
        '{"verdict":"supported|mixed|contradicted|insufficient","confidence":0-1,"reason":"<=25 words",'
        '"evidence":[{"url_or_chunk_id":"<id from evidence>","stance":"supports|contradicts|neutral",'
        '"source_type":"primary|news|creator|blog"}]}',
        f"Claim: {claim}\nEvidence: {listing}", as_json=True)
    by_id = {e["id"]: e for e in ev}
    kept = []
    for e in out.get("evidence", []):  # drop anything not actually retrieved
        src = by_id.get(e.get("url_or_chunk_id"))
        if src:
            st = "creator" if src["source_type"] == "creator" else (
                e.get("source_type") if e.get("source_type") in {"primary", "news", "blog"} else "blog")
            kept.append({"url_or_chunk_id": src["id"], "stance": e.get("stance", "neutral"), "source_type": st,
                         "text": src["text"], "meta": src.get("meta", "")})
    verdict = out.get("verdict") if out.get("verdict") in VERDICTS else "insufficient"
    if not kept:
        verdict = "insufficient"
    try:
        conf = max(0.0, min(1.0, float(out.get("confidence", 0.3))))
    except (TypeError, ValueError):
        conf = 0.3
    return {"verdict": verdict, "confidence": conf, "reason": " ".join(str(out.get("reason", "")).split()[:25]),
            "evidence": kept}


def red_flags(text):
    flags = []
    if re.search(r"sponsor|affiliate|use code|link in (the )?description|discount|promo|paid partnership", text, re.I):
        flags.append("sponsorship/affiliate language")
    if not re.search(r"according to|study|studies|research|report|paper|source|data from|published", text, re.I):
        flags.append("no cited source")
    if re.search(r"\b(always|never|everyone|nobody|guaranteed|100%|proven|impossible)\b", text, re.I):
        flags.append("absolute claim")
    return flags


def consensus(ev):
    c = [e["stance"] for e in ev if e["source_type"] == "creator"]
    s, k = c.count("supports"), c.count("contradicts")
    return s / (s + k) if (s + k) else 0.5


def report(chunk):
    """Full Legitimacy Report for one chunk row. Cached by claim hash."""
    ext = extract_claim(chunk["text"])
    flags = red_flags(chunk["text"])
    if ext["type"] != "fact" or not ext["claim"]:
        return {"claim": ext["claim"], "type": ext["type"], "verdict": "opinion", "label": "Opinion/Unverifiable",
                "confidence": 1.0, "reason": "Not a checkable factual claim.", "evidence": [], "consensus": 0.5,
                "red_flags": flags}
    h = hashlib.sha256(ext["claim"].lower().encode()).hexdigest()
    cached = db.q("SELECT data FROM reports WHERE claim_hash=?", (h,))
    if cached:
        core = json.loads(cached[0]["data"])
    else:
        core = judge(ext["claim"], gather_evidence(ext["claim"], chunk["creator_id"]))
        db.x("INSERT OR REPLACE INTO reports(claim_hash,data,ts) VALUES(?,?,?)", (h, json.dumps(core), time.time()))
    return {"claim": ext["claim"], "type": ext["type"], **core, "label": core["verdict"].capitalize(),
            "consensus": consensus(core["evidence"]), "red_flags": flags}


def verify_chunk(chunk_id):
    c = db.get_chunk(chunk_id)
    if not c:
        return None
    return {"chunk_id": chunk_id, "title": c["title"], "creator": c["creator"], "report": report(c)}
