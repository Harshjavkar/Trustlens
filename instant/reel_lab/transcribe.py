"""Optional local speech recognition; importing this module downloads nothing.

Installing requirements-speech.txt installs the engine, not model weights.
An actual run with a named, uncached model can download weights through
faster-whisper. Supply model_path for a strictly local model directory.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

from .collector import clean_error, save_json

MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
}
SCHEMA_VERSION = 1
WARNING = (
    "Speech is automatic speech recognition (ASR), unreviewed, and may contain errors. "
    "It is separate from captions and OCR; speaker identity has not been determined."
)
MODEL_NOTE = (
    "Setup does not download speech models. An actual transcription with an uncached "
    "named model may download model weights; model_path uses local files only."
)


def _fingerprint(source: Path) -> dict:
    before = source.stat()
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Source changed while its fingerprint was being read; retry the file.")
    return {
        "path": str(source), "size": after.st_size,
        "mtime_ns": after.st_mtime_ns, "sha256": digest.hexdigest(),
    }


def _load_model(model: str, device: str, compute_type: str, model_path: Path | None):
    if model_path is not None:
        # Upstream can fetch a tokenizer when it is absent, even with a local
        # model directory. Require it explicitly to keep this route offline.
        required = ("config.json", "model.bin", "tokenizer.json")
        if any(not (model_path / name).is_file() for name in required):
            raise ValueError(
                "Local model folder must contain config.json, model.bin and tokenizer.json; "
                "remote tokenizer fallback is disabled."
            )
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Local speech recognition requires optional packages: "
            "python -m pip install -r requirements-speech.txt"
        ) from exc
    return WhisperModel(
        str(model_path) if model_path is not None else model,
        device=device, compute_type=compute_type,
        local_files_only=model_path is not None,
    )


def _number(value, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Speech recognition returned an invalid {name}.")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"Speech recognition returned an invalid {name}.")
    return number


def _segment(start, end, text, duration: float) -> dict:
    start = _number(start, "segment start")
    end = _number(end, "segment end")
    if end <= start or end > duration:
        raise ValueError("Speech recognition returned timestamps outside the media duration.")
    return {"start_seconds": start, "end_seconds": end, "text": text, "speaker": None}


def _transcribe(source: Path, engine, language: str | None) -> dict:
    raw_segments, info = engine.transcribe(str(source), language=language)
    duration = _number(info.duration, "duration")
    probability = getattr(info, "language_probability", None)
    if probability is not None:
        probability = _number(probability, "language probability")
        if probability > 1:
            raise ValueError("Speech recognition returned an invalid language probability.")
    segments = []
    previous_start = -1.0
    # faster-whisper performs inference while this generator is consumed.
    for item in raw_segments:
        if item.text is None:
            continue
        if not isinstance(item.text, str):
            raise ValueError("Speech recognition returned invalid segment text.")
        text = item.text.strip()
        if not text:
            continue
        segment = _segment(item.start, item.end, text, duration)
        if segment["start_seconds"] < previous_start:
            raise ValueError("Speech recognition returned segments out of chronological order.")
        previous_start = segment["start_seconds"]
        segments.append(segment)
    return {
        "status": "complete" if segments else "no_speech",
        "language": getattr(info, "language", None),
        "language_probability": probability,
        "duration_seconds": duration,
        "segments": segments,
    }


def _cache_matches(target: Path, fingerprint: dict, config: dict) -> bool:
    try:
        cached = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(cached, dict) or not (
            cached.get("schema_version") == SCHEMA_VERSION
            and cached.get("status") == "complete"
            and cached.get("source") == fingerprint and cached.get("config") == config
            and isinstance(cached.get("segments"), list) and cached["segments"]
        ):
            return False
        duration = _number(cached["duration_seconds"], "duration")
        previous_start = -1.0
        for item in cached["segments"]:
            if not isinstance(item["text"], str) or not item["text"].strip():
                return False
            segment = _segment(item["start_seconds"], item["end_seconds"], item["text"], duration)
            if segment["start_seconds"] < previous_start:
                return False
            previous_start = segment["start_seconds"]
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def run_transcribe(
    input_dir: Path, output_dir: Path, model: str = "base", device: str = "cpu",
    compute_type: str = "int8", language: str | None = None,
    model_path: Path | None = None,
) -> dict:
    """Transcribe local media recursively, retaining independent source-linked outputs.

    Successful matching source/config outputs are cached. Empty speech and failures
    are explicit, retryable states. Atomic JSON replacement ensures an interrupted
    write cannot become a successful cache entry. Model initialization is deferred
    until at least one file needs work; this function never installs dependencies.
    """
    started = time.perf_counter()
    input_dir, output_dir = Path(input_dir).resolve(), Path(output_dir).resolve()
    if not input_dir.is_dir():
        raise ValueError(f"Input folder does not exist: {input_dir}")
    if not all(isinstance(value, str) and value.strip() for value in (model, device, compute_type)):
        raise ValueError("Model, device and compute type must be nonempty strings.")
    if language is not None and (not isinstance(language, str) or not language.strip()):
        raise ValueError("Language must be a language code or None for automatic detection.")
    if model_path is not None:
        model_path = Path(model_path).resolve()
        if not model_path.is_dir():
            raise ValueError(f"Local model folder does not exist: {model_path}")
    config = {
        "engine": "faster-whisper", "model": model, "device": device,
        "compute_type": compute_type, "language": language,
        "model_path": str(model_path) if model_path is not None else None,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": SCHEMA_VERSION, "processed": 0, "skipped": 0,
        "no_speech": 0, "failed": 0, "errors": [], "config": config,
        "warning": WARNING, "model_note": MODEL_NOTE,
        "input_dir": str(input_dir), "output_dir": str(output_dir),
    }
    pending = []

    def fail(source: Path, target: Path, fingerprint: dict, exc: Exception, elapsed: float):
        error = clean_error(exc)
        record = {
            "schema_version": SCHEMA_VERSION, "status": "failed",
            "source": fingerprint, "config": config, "segments": [],
            "error": error, "error_type": type(exc).__name__,
            "elapsed_seconds": round(elapsed, 3), "warning": WARNING,
        }
        try:
            save_json(target, record)
        except OSError as write_error:
            error += "; Could not save failure record: " + clean_error(write_error)
        summary["failed"] += 1
        summary["errors"].append({"file": str(source), "error": error})

    for source in sorted(input_dir.rglob("*")):
        if not source.is_file() or source.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        target = output_dir / source.relative_to(input_dir).with_name(source.name + ".json")
        try:
            fingerprint = _fingerprint(source)
        except OSError as exc:
            fail(source, target, {"path": str(source)}, exc, 0)
            continue
        except ValueError as exc:
            fail(source, target, {"path": str(source)}, exc, 0)
            continue
        if _cache_matches(target, fingerprint, config):
            summary["skipped"] += 1
        else:
            pending.append((source, target, fingerprint))
    engine = None
    model_error = None
    if pending:
        try:
            engine = _load_model(model, device, compute_type, model_path)
        except Exception as exc:
            model_error = exc
    for source, target, fingerprint in pending:
        file_started = time.perf_counter()
        try:
            if model_error is not None:
                raise model_error
            result = _transcribe(source, engine, language)
            if _fingerprint(source) != fingerprint:
                raise ValueError("Source changed during transcription; retry the file.")
            result.update({
                "schema_version": SCHEMA_VERSION, "source": fingerprint,
                "config": config, "warning": WARNING,
                "elapsed_seconds": round(time.perf_counter() - file_started, 3),
            })
            save_json(target, result)
            summary["processed" if result["status"] == "complete" else "no_speech"] += 1
        except Exception as exc:
            fail(source, target, fingerprint, exc, time.perf_counter() - file_started)
    summary["total"] = sum(summary[key] for key in ("processed", "skipped", "failed", "no_speech"))
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    save_json(output_dir / "summary.json", summary)
    return summary
