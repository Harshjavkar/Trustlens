# TrustLens Architecture

## Components
- **Extension** (`extension/`): side panel UI (vanilla JS) + service worker. Backend URL in `chrome.storage.local`; `chrome.alarms` posts `/ingest` every 360 minutes.
- **Backend** (`backend/`): FastAPI. `main.py` routes; `ingest.py` creators/transcripts/chunks; `ask.py` retrieval, rerank, scoring; `verify.py` Legitimacy pipeline; `llm.py` the only model interface; `db.py` SQLite.
- **Agent**: optional Hermes in a NemoClaw/OpenShell sandbox (`AGENT_URL`). `llm.chat` tries the agent first, then the LLM directly. Embeddings always go directly to the embedding model.
- **Model**: Nemotron on Nebius via the OpenAI-compatible API.

## Schema (SQLite)
| Table | Purpose |
|---|---|
| creators | channel, name, trust (1-5, nudged by feedback) |
| videos | id, title, published; also marks videos already tried (no captions = no chunks) |
| chunks | ~60s transcript text, start seconds, float32 embedding BLOB |
| memory | kind (`question`, `feedback`), key, value, timestamp |
| reports | cached judge output keyed by SHA-256 of the normalized claim |

## Endpoints
`GET /health`, `GET /creators`, `POST /creators`, `POST /ingest`, `POST /ask`, `POST /verify`, `GET /digest`, `POST /feedback`.

## Ask flow
1. Embed question; cosine over all chunks; top 20.
2. LLM rerank to top 5 (falls back to cosine order on failure).
3. `verify.report` for each in parallel (5 threads).
4. Score, build breakdown, sort: non-contradicted by score, then contradicted with a warning.
5. Quote = first 15 words of the chunk.

## Legitimacy pipeline
1. `extract_claim` -> `{claim, type}`. Non-fact types return `Opinion/Unverifiable` without any search.
2. `gather_evidence`: top-5 chunks from other creators (embedding of the claim) + 3 LLM-written web queries, deduped to top 5 results (url + snippet). Missing search key = web evidence skipped.
3. `judge`: strict JSON. Post-processing keeps only evidence ids that were actually retrieved, forces `insufficient` when none remain, clamps confidence to 0-1, truncates reason to 25 words.
4. `red_flags`: regex heuristics (sponsorship/affiliate, no cited source, absolute claims).
5. `consensus` = supports / (supports + contradicts) among creator evidence, 0.5 if none.

## Scoring
`0.35*relevance + 0.2*trust/5 + 0.3*legitimacy + 0.1*consensus + 0.05*recency`
- relevance: cosine similarity clipped to [0,1]
- legitimacy: verdict weight x confidence
- recency: `1 / (1 + age_days/90)`

## Memory and feedback
Every question and vote is stored in `memory`. A vote moves the creator's trust by ±0.1 (clamped 1-5), which feeds the trust term of the score.

## Security and egress
Intended allowlist for the sandboxed agent: `youtube.com`, the LLM host, the search host. The backend binds locally; CORS is open because it is meant for localhost use only.

## Known limitations
Brute-force similarity; claim cache is keyed on claim text only (evidence is not refreshed); red flags are heuristic.
