# Roadmap

The first product goal is a useful, inspectable conversation over one creator’s published knowledge. Archive scale and comparison across creators follow evidence quality and reliable source attribution.

Statuses describe the prototype’s observed behavior. They do not imply that every platform, creator, or generated answer has been validated. See [validation](docs/validation.md) for exact evidence.

| Milestone | Current state | Completion gate |
| --- | --- | --- |
| **M0 — Evidence foundation** | Source records, derived Markdown, creator-scoped keyword search, metadata, and modality separation implemented. The public seed contains 11 captions. | Rebuild the index and notes from records; preserve source identity and real timestamps; verify portable seed initialization without altering an existing library. |
| **M1 — Local interface and shared preparation** | Responsive source browser, citation dialogs, conversation interface, persistent creator jobs, deduplication, and explicit retry implemented. Discovery remains limited. | Browser acceptance checks pass; concurrent requests reuse one job/library; failures and partial coverage remain visible; public installation checks pass. |
| **M2 — Real speech and visual evidence** | Optional transcription and sampled OCR code exists. Reviewed real extraction is not established. | Process saved videos, review all sample outputs against originals, record speakers and errors, preserve provenance, and display measured speech/OCR coverage. |
| **M3 — Evaluated conversation** | Hosted conversation connected; three-request smoke test exposed a missing follow-up citation. Prompt and labeling improved, with live verification still pending. | Complete a documented 20-question evaluation for relevance, citation support, follow-ups, missing evidence, guests, and changed positions. Reject or clearly expose unsupported responses. |
| **M4 — Portable single-creator MVP** | Windows setup, installed-wheel bootstrap and the caption-only sample are verified. Full evidence/answer acceptance remains open. This is a local app, not a hosted product. | Verify macOS/Linux separately; complete evidence/answer review; keep documentation accurate and private data out of source. |
| **M5 — Trustlens review branch** | Separate `instant/` project prepared for branch review. Legacy root application remains independent. | Publish a source-only review branch with reproducible setup, accurate validation, clear limitations, and an inspectable diff. Incorporate review before integration into the legacy application. |
| **M6 — Incremental collection and measured scale** | Bounded collection and cache reuse exist; full profile history and throughput targets are unproven. | Improve permitted discovery, process new/changed evidence without unnecessary repeats, measure 20-post runs before 100-post runs, and report cold/cache timing, failures, quality, and resource use. |
| **M7 — Comparison across selected creators** | Future work. | Compare equivalent specific claims across selected authorities, count distinct creators, preserve conditional or changing positions, and distinguish disagreement from missing evidence. |

## Next work

1. Review the published source branch and reproduce the recorded Windows setup; verify macOS/Linux separately.
2. Recheck the known conversational follow-up limitation with an explicitly configured operator-owned account and inspect source support.
3. Produce and review speech and OCR from existing source material before expanding collection.
4. Run the question evaluation and use its failures to guide retrieval, context selection, and response validation.

## Later deployment work

Production hosting needs authenticated accounts, private chat storage, permissions, queue/resource controls, operator secret management, monitoring, and explicit provider data handling. The shared local registry is useful groundwork; it does not provide those deployment features by itself.

Full archive completeness, guaranteed processing time, creator endorsement, automatic fact-checking, and group consensus as proof of truth are not current product claims.
