# InstaNT

A local app for exploring a chosen creator’s published knowledge through conversation and source evidence. Select a creator, ask a question, and open the supporting post or passage beside the reply.

This directory is a separate prototype within Trustlens. The legacy `backend/` and `extension/` project at the repository root remains a separate application.

## What you can try

- Browse and search a bundled sample of **11 real post captions** attributed to `@financewithsharan`, each linked to its original Instagram reel.
- Connect your own hosted conversation account for natural questions and follow-ups. Source citations open the relevant evidence.
- Request another creator through a persistent preparation queue. Requests for the same creator reuse the existing job or stored library.
- Inspect separate caption, speech, and on-screen text coverage. Local video previews appear when media is available in your own library.

The included sample contains **captions and canonical source links only**. It contains no videos, speech transcripts, OCR, model weights, or credentials. It is a partial snapshot, not the creator’s full archive. Attributing a caption to a source does not fact-check its claims; generated replies remain unreviewed.

## Requirements

- Python **3.11 or later**, with `venv`, `pip`, and SQLite FTS5 support.
- Internet access to install dependencies and, optionally, use a hosted conversation model or collect accessible new posts.
- An ordinary CPU is sufficient for the interface, caption search, and remote model requests. A local GPU is not required for this path. Optional speech/OCR processing has separate dependencies and resource needs.

This is a Python app you run locally, not an already hosted website or a standalone executable for every computer. Its app server binds to the local machine.

## Windows setup

Open PowerShell in the `instant/` directory:

```powershell
.\setup-local.ps1
```

Setup detects a supported installed Python and creates `.venv-local`. If detection fails, pass `-PythonExe` with the full path to your Python 3.11+ interpreter.

For conversation, copy the example settings once:

```powershell
Copy-Item .env.example .env.local
```

Edit `.env.local` privately and set `INSTANT_CHAT_API_KEY` to your own [OpenRouter API key](https://openrouter.ai/settings/keys). The example uses:

```dotenv
INSTANT_CHAT_BASE_URL=https://openrouter.ai/api/v1
INSTANT_CHAT_MODEL=nvidia/nemotron-3.5-lightning:free
INSTANT_CHAT_API_KEY=
```

The `:free` model is the example configuration. Availability, quotas, and account requirements are controlled by the provider; there is no automatic switch to a paid model. The conversation sends your question, recent chat context, and selected creator evidence to the configured provider. Keep the key on the server in `.env.local`; never commit it or put it in browser code. Existing environment variables take precedence over this file.

You can leave the key unset and browse the sample sources. Chat will show that setup is pending rather than invent a reply or substitute retrieved excerpts.

Start the app:

```powershell
.\start-instant.ps1
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The launcher imports an existing `data/live-first-test/manifest.json` when present; otherwise it initializes the bundled caption sample. It then serves the interface. Stop it with `Ctrl+C`.

## macOS and Linux setup

From `instant/`, use a normal Python environment:

```bash
python3 -m venv .venv-local
source .venv-local/bin/activate
python -m pip install -e .
cp .env.example .env.local
```

Edit `.env.local` as described above, or leave the key empty to browse sources only. Then run:

```bash
python -m reel_lab bootstrap-demo
python -m reel_lab serve --library data/library --port 8765
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). These commands do not require PowerShell. Fresh installation and platform verification are tracked in [validation](docs/validation.md).

## Using the prototype

1. Select `@financewithsharan` and explore the saved posts, or search the source library directly.
2. With conversation configured, try “What does Sharan say about emergency funds?” and ask a follow-up.
3. Open a numbered citation to read the source. Captions have no video timestamps; timestamp links appear only for available speech or OCR observations.
4. Check the coverage counts before interpreting an answer. No evidence in this sample does not mean the creator never discussed the subject.

Replies are AI-generated, and citation formatting does not establish that a source supports a claim. The creator is not participating in or endorsing the conversation.

### Adding a creator

Enter a handle or Instagram profile link in **Add a creator**. A new request queues one background preparation job; concurrent and later requests reuse that job or its ready library within the same running installation.

The worker currently tries bounded public link discovery, saves accessible reels with yt-dlp, and indexes available captions. Instagram may expose no reel links, require authentication, or limit access. The app reports the actual preparation state instead of claiming complete coverage.

If it reports **Public discovery needs source links**, supply an explicitly saved browser-observed link snapshot at `data/creators/<handle>/sources.json`, then choose **Retry preparation**. Automatic complete profile discovery is unfinished. The worker does not currently perform speech transcription or OCR automatically.

Existing valid source files and completed extraction outputs are reused when available. The bundled sample needs no Instagram download. Media files, user data, cookies, local databases, and private settings are excluded from the public source package.

## Development and optional processing

Run offline tests from this directory with the environment active:

```bash
python -B -m unittest discover -s tests -v
```

Inspect available commands without starting collection:

```bash
python -m reel_lab --help
python -m reel_lab doctor
```

Speech transcription and sampled OCR are optional CLI capabilities, separate from the conversation model. On Windows, `setup-local.ps1 -WithSpeech -WithOcr` installs their optional dependencies. Other platforms can install the extras with `python -m pip install -e '.[speech,ocr]'`. Installing dependencies does not establish extraction accuracy or download a complete creator archive. Review extraction output against the original material before relying on it.

See [architecture](docs/architecture.md), [validation and limitations](docs/validation.md), and the [roadmap](ROADMAP.md) for the implementation and remaining work.

## License and source attribution

The application code is covered by the repository’s [MIT license](../LICENSE). The bundled captions are attributed third-party source material linked to the original creator’s posts; the code license does not claim to relicense those captions. Their inclusion does not imply creator endorsement or independent verification of their claims.
