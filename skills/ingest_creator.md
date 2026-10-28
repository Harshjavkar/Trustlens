# Skill: ingest_creator
**When to use:** the user adds a trusted YouTube creator, or the 6-hour alarm fires.

**Steps**
1. `POST /creators` with the channel URL and trust (1-5); the channel id is resolved and stored.
2. `POST /ingest`: read the latest N videos from the channel RSS feed, fetch transcripts (skip videos without captions).
3. Chunk transcripts into ~60s segments with start times, embed them, store in SQLite.

**Inputs:** `channel_url`, `trust`, optional `n`. **Outputs:** creator row; `{videos, chunks, skipped_no_captions}`.
