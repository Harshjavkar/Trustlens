# Validation

This page separates observed behavior from work that still needs verification. The project is a local prototype, not a validated complete creator archive or a production service.

## Recorded checks

| Check | Evidence and limits |
| --- | --- |
| Offline source baseline | **131 tests passed** before the portable public package was prepared. Coverage includes evidence records, creator scoping, extraction/cache behavior, shared jobs and recovery, HTTP routes, configuration, and conversation/citation handling. Provider and Instagram failures are exercised with mocks; this is not a live integration benchmark. |
| Browser source flow | Creator selection, caption search, source-backed questions, source dialogs, error states, known-creator reuse, Enter submission, and narrow layouts were inspected in the local prototype. |
| Local media preview | A locally available source video decoded and sought to a real position during development. The public caption sample includes no video; this result is not a promise of media playback after a fresh demo install. |
| Hosted conversation smoke test | Three live requests completed against the configured hosted account. The first answer included the correct source citation; the short follow-up omitted it, and the unrelated-topic answer had no citation. This is a small connectivity and behavior check, not an answer-quality evaluation. |
| Follow-up limitation response | The prompt and response labeling were subsequently improved to avoid presenting uncited generation as verified evidence. No additional live request has yet established that this change fixes the omission. |
| JavaScript syntax | `node --check reel_lab/web/app.js` passed for the conversation interface. Node.js is a development check, not a runtime requirement for the app. |

Source attribution, valid citation numbers, and passing tests do not establish factual correctness or source entailment. The included captions have not been independently fact-checked. Generated replies have not passed a systematic human grounding evaluation.

## Public-package checks

The following checks were run against this public copy on Windows with Python 3.12.14 on 2026-10-29:

| Check | Status |
| --- | --- |
| Full offline suite after public-package changes | **140 tests passed in 8.554 seconds**, including nine bootstrap tests. |
| Fresh Python environment installation and caption bootstrap | `setup-local.ps1 -PythonExe <working-python>` created a new environment, installed the app, yt-dlp and imageio-ffmpeg, and passed doctor. No speech/OCR packages, models or media were installed. |
| Repeated bootstrap preserves an existing library | Passed in bootstrap tests and the installed-wheel check; canonical and derived files remained byte-identical on the second invocation. Foreign/edited libraries are preserved. |
| Wheel contents and installed-package static assets/seed | Built an 80,165-byte wheel; installed it into a separate clean environment with `--no-deps`. Isolated Python verified 11 packaged records, source search, static HTML, health/posts APIs and explicit chat-unavailable HTTP 503 without any private key or media. |
| Fresh Windows launcher and Python selection | `start-instant.ps1 -Port 8766` selected the fresh project environment, initialized 11 captions without a local manifest, and served health/posts/search correctly with chat unconfigured. Both PowerShell scripts parse without errors. System-Python detection paths are implemented; setup here used an explicit interpreter path. |
| macOS/Linux execution | Not yet verified on those platforms. |
| Hosted conversation after packaging and prompt changes | Not repeated; requires the operator’s private provider configuration. |
| Live preparation of an unknown creator | Not established by offline worker tests. Public discovery can legitimately require supplied source links. |

The checks used disposable libraries and fresh environments. The first wheel-check attempt detected that its working directory was shadowing the installed package; rerunning with isolated Python (`-I`) ensured the verified module came from the clean wheel installation. No new model inference request was used for release validation. Do not infer macOS/Linux execution from these Windows results.

## Reproducible offline checks

From `instant/`, with the project installed in the active Python environment:

```bash
python -B -m unittest discover -s tests -v
python -m reel_lab doctor
python -m reel_lab bootstrap-demo
```

Run bootstrap against a disposable empty library when checking seed initialization without touching an existing corpus:

```bash
python -m reel_lab bootstrap-demo --library data/validation-demo
```

Use a new temporary directory for independent runs. A second bootstrap should preserve existing source records. Doctor checks local readiness; it does not establish Instagram access, a successful provider call, or extraction accuracy.

## Manual acceptance checks

1. Launch without a chat key. Confirm the source browser works, the sample shows 11 captions and no speech/OCR/media, and chat reports setup pending without fabricated replies.
2. Configure an operator-owned chat key privately. Confirm health reports configuration without revealing credentials, then verify a greeting, a source question, and a follow-up.
3. Open each cited passage and assess whether it actually supports the associated statement. Check that citation numbers refer to the current reply.
4. Ask something absent from the sample. Confirm the answer preserves the distinction between missing evidence and a claim that the creator never discussed it.
5. Exercise a provider error. Confirm the question can be retried and that source excerpts are not presented as a successful conversation response.
6. Request the same creator concurrently and after readiness. Confirm a single active job and reuse of the existing library.
7. Review a terminal preparation error and an explicit retry. Confirm progress is truthful and no automatic access bypass is attempted.

## Remaining quality gates

- Produce and review speech/OCR outputs against the saved source material. Optional processing code and mocked tests do not establish successful real extraction.
- Evaluate at least 20 questions, including paraphrases, missing evidence, guest speakers, outdated positions, misleading captions, and conflicting advice.
- Measure citation support and answer omissions, not just citation formatting or response success.
- Measure fresh discovery, download, extraction, indexing, and total time separately, with sample size and cache reuse disclosed. The target of 100 reels plus extraction in about ten minutes is unproven.
- Verify incremental updates, workload limits, and recovery before production hosting or wider creator collection.
