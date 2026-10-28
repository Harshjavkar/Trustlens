# CLAUDE.md: TrustLens project memory
Read at the start of every session. Update on any change. Keep < 150 lines.

## Goal
Chrome MV3 extension + local FastAPI backend for the NVIDIA Personal AI Track hackathon. User picks trusted YouTube creators, asks a question, gets ranked video moments (title, creator, timestamp link, quote <=15 words), each with an automatic Legitimacy Report. Repo is public: docs must be professional. Keep code minimal, replies short, no unrequested features or tests.

## Stack
Vanilla JS extension (side panel, chrome.alarms 6h). Python FastAPI. SQLite + numpy cosine (no sqlite-vec). youtube-transcript-api + channel RSS. Tavily/Brave search. OpenAI-compatible LLM (Nemotron on Nebius); optional Hermes agent at AGENT_URL (falls back to direct).

## Folder map
extension/ (manifest, sidepanel.html/js, background.js) · backend/ (main, llm, ingest, ask, verify, db, requirements) · skills/ (4 md) · docs/architecture.md · scripts/seed.py · README/LICENSE/.env.example/.gitignore

## Architecture
main.py routes -> ingest.py (creators, RSS, transcripts, 60s chunks, embeddings) / ask.py (top-20 cosine, LLM rerank to 5, parallel verify.report, score) / verify.py (extract_claim, gather_evidence, judge, red_flags, claim-hash cache). llm.py is the only model interface. db.py is all SQL helpers.

## Conventions
Spec wording: "cross-checked", never "guaranteed true". Never invent URLs; judge output is filtered to retrieved evidence. Minimal code. Update CLAUDE.md, DEVELOPMENT.md, STATUS.md after every step. Never mark Done unless it runs.

## Env vars
LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, EMBED_MODEL, AGENT_URL, AGENT_API_KEY, SEARCH_PROVIDER (tavily|brave), SEARCH_API_KEY, DB_PATH

## Decisions
- Cosine similarity in numpy instead of sqlite-vec (simpler, allowed by spec).
- Videos without captions still get a `videos` row so they are not retried every ingest.
- `/feedback` body uses `vote: "up"|"down"`; trust nudge ±0.1.
- Consensus = supports/(supports+contradicts) among creator evidence, 0.5 if none.
- Preferences = creator trust weights; no separate preferences store.
- System prompt prefix "detailed thinking off" for Nemotron; `<think>` blocks stripped.

## Known issues
- Not yet run against live YouTube / Nebius / search / real FastAPI (no network or deps in authoring sandbox).
- Default model IDs in .env.example must be checked against the Nebius catalog.
- Extension host_permissions only cover localhost:8000.

## Status summary
All 7 steps written. Core logic (chunking, ranking, verdicts, URL filtering, caching, memory) verified with stubbed LLM/search. See STATUS.md.

## Next steps
1. Install deps, fill .env, run backend and `scripts/seed.py`.
2. Load extension and test end-to-end.
3. Wire real Hermes/NemoClaw sandbox and set AGENT_URL.
4. Fix issues found; mark steps Done in STATUS.md.
