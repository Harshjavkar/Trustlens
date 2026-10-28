"""Sample local videos and recognize text with optional, local-only OCR."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
WARNING = "Sampled OCR can miss fast text, small text, and unreadable frames."


def normalize_text(text: str) -> str:
    """Compare words without treating capitalization/whitespace as changes."""
    return " ".join(text.split()).casefold()


def should_sample(frame_index: int, video_fps: float, sample_fps: float) -> bool:
    """Select the first available frame at or after each sampling interval."""
    if frame_index < 0 or not all(math.isfinite(v) and v > 0 for v in (video_fps, sample_fps)):
        raise ValueError("Frame index must be nonnegative and frame rates positive and finite.")
    if frame_index == 0 or sample_fps >= video_fps:
        return True
    return math.floor(frame_index * sample_fps / video_fps + 1e-9) > math.floor(
        (frame_index - 1) * sample_fps / video_fps + 1e-9
    )


def merge_samples(samples: list[tuple[float, str]]) -> list[dict]:
    """Merge adjacent matching samples; a blank sample breaks continuity."""
    segments: list[dict] = []
    previous = ""
    for timestamp, text in samples:
        normalized = normalize_text(text)
        if normalized and normalized == previous:
            segments[-1]["last_seen_seconds"] = round(timestamp, 3)
        elif normalized:
            segments.append({
                "first_seen_seconds": round(timestamp, 3),
                "last_seen_seconds": round(timestamp, 3),
                "text": text.strip(),
            })
        previous = normalized
    return segments


def _dependencies():
    try:
        import cv2
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "Local OCR requires optional packages. Run: python -m pip install -r requirements-ocr.txt"
        ) from exc
    try:
        # RapidOCR uses the ONNX models bundled with its installed package.
        return cv2, RapidOCR()
    except Exception as exc:
        raise RuntimeError(f"Could not initialize the local OCR engine: {exc}") from exc


def _read_video(source: Path, cv2, engine, fps: float) -> dict:
    capture = cv2.VideoCapture(str(source))
    samples: list[tuple[float, str]] = []
    frame_index = 0
    try:
        if not capture.isOpened():
            raise ValueError("Video could not be opened; it may be corrupt or unsupported.")
        video_fps = float(capture.get(cv2.CAP_PROP_FPS))
        if not math.isfinite(video_fps) or video_fps <= 0:
            raise ValueError("Video has no usable frame-rate metadata.")
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if should_sample(frame_index, video_fps, fps):
                recognized, _ = engine(frame)
                text = "\n".join(str(line[1]) for line in (recognized or []) if line[1])
                samples.append((frame_index / video_fps, text))
            frame_index += 1
        if not frame_index:
            raise ValueError("Video opened but no frames could be decoded.")
        return {
            "video_fps": video_fps,
            "frames_read": frame_index,
            "sampled_frames": len(samples),
            "samples_with_text": sum(bool(normalize_text(text)) for _, text in samples),
            "duration_seconds": round(frame_index / video_fps, 3),
            "segments": merge_samples(samples),
        }
    finally:
        capture.release()


def run_ocr(input_dir: Path, output_dir: Path, fps: float) -> dict:
    """Write one JSON per video, preserving relative folders; return run counts.

    Matching source path/size/mtime and sampling configuration skip completed
    outputs. Recognized timestamps mark observations, not exact text boundaries.
    """
    started = time.perf_counter()
    input_dir, output_dir = Path(input_dir).resolve(), Path(output_dir).resolve()
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("Sampling fps must be positive and finite.")
    if not input_dir.is_dir():
        raise ValueError(f"Input folder does not exist: {input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    config = {"fps": float(fps), "engine": "rapidocr_onnxruntime", "schema_version": 1}
    summary = {"processed": 0, "skipped": 0, "failed": 0, "errors": [], "warning": WARNING}
    pending = []
    for source in sorted(input_dir.rglob("*")):
        if not source.is_file() or source.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        stat = source.stat()
        fingerprint = {"path": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        # Keep the extension to distinguish e.g. clip.mp4 from clip.webm.
        target = output_dir / source.relative_to(input_dir).with_name(source.name + ".json")
        try:
            cached = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = {}
        if cached.get("status") == "complete" and cached.get("source") == fingerprint and cached.get("config") == config:
            summary["skipped"] += 1
        else:
            pending.append((source, target, fingerprint))
    if pending:
        cv2, engine = _dependencies()
        for source, target, fingerprint in pending:
            video_started = time.perf_counter()
            try:
                result = _read_video(source, cv2, engine, fps)
                result.update({"status": "complete", "source": fingerprint, "config": config, "warning": WARNING})
                result["elapsed_seconds"] = round(time.perf_counter() - video_started, 3)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(target.suffix + ".tmp")
                temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(target)
                summary["processed"] += 1
            except Exception as exc:
                summary["failed"] += 1
                summary["errors"].append({"file": str(source), "error": str(exc)})
    summary["total"] = summary["processed"] + summary["skipped"] + summary["failed"]
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return summary
