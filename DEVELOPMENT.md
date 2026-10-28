# DEVELOPMENT (changelog, newest at bottom)

## 2026-10-28 - Step 1: Backend skeleton + ingest + ask
Files: backend/{db,llm,ingest,ask,main}.py, requirements.txt, .env.example, .gitignore, LICENSE.
Why: base API, local SQLite, OpenAI-compatible interface, RSS + transcript ingest with 60s chunks, cosine top-20 + LLM rerank.
Follow-up: live run against YouTube and Nebius.

## 2026-10-28 - Step 2: Extension UI
Files: extension/{manifest.json,sidepanel.html,sidepanel.js,background.js}.
Why: side panel with add creator, trust slider, ask box, cards, badges, red-flag chips, evidence list, feedback, backend URL setting. DOM built with textContent (no innerHTML).
Follow-up: load unpacked and test in Chrome.

## 2026-10-28 - Step 3: Legitimacy pipeline
Files: backend/verify.py, backend/ask.py.
Why: extract_claim, gather_evidence, judge (filtered to retrieved evidence), red_flags, claim-hash cache, score breakdown, contradicted-last ordering.
Follow-up: tune prompts against the real model.

## 2026-10-28 - Step 4: Memory and feedback
Files: backend/db.py, backend/main.py.
Why: `memory` table (questions, feedback), `/feedback` nudges creator trust ±0.1.

## 2026-10-28 - Step 5: Skills, digest, alarm
Files: skills/*.md, GET /digest in main.py, 6h alarm in background.js, scripts/seed.py.

## 2026-10-28 - Step 6: NemoClaw/Hermes wiring
Files: backend/llm.py, .env.example.
Why: chat goes to AGENT_URL when set, falls back to direct LLM on failure; embeddings always direct.
Follow-up: not tested against a real sandbox.

## 2026-10-28 - Step 7: Docs and cleanup
Files: README.md, docs/architecture.md, CLAUDE.md, STATUS.md.
Verification: py_compile on all Python, JSON check on manifest; logic test (not shipped) with stubbed LLM/search/HTTP confirmed ranking, contradicted-last, invented-URL filtering, 25-word reason cap, claim cache, memory writes.

## 2026-10-29 - InstaNT standalone review prototype

Added `instant/` as an isolated Python web app with creator-scoped evidence records/search, natural OpenRouter/Nemotron conversation, source citations, a shared creator-job lifecycle and optional yt-dlp/speech/OCR processing. A bundled text-only caption seed and first-run bootstrap support a fresh checkout; private keys, downloaded media, environments, model files and local history are excluded.

The original extension/backend remains unchanged. Three limited live model checks previously succeeded; one follow-up omitted its citation, so answer quality remains under review. Portable build and final test results are recorded in `instant/docs/validation.md`. Publishing this branch does not deploy a judging website.
