"""Export a text-only, creator-scoped seed library for a hosted demo.

Only explicitly allowed evidence fields leave the source library. Media stays
in place; notes and the search index are generated from the exported records.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from .library import Library


_MARKER = ".instant-demo-export.json"
_FORMAT = "instant-text-demo"
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_HASH = re.compile(r"[0-9a-fA-F]{64}\Z")
_STATUSES = {"complete", "missing", "failed", "invalid", "incomplete", "stale",
             "partial", "empty", "pending", "reviewed", "not_requested", "not_processed"}


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _write(path, text):
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _sanitize(post, creator):
    code = post.get("id")
    if not isinstance(code, str) or not _ID.fullmatch(code):
        raise ValueError("Demo records require safe post shortcodes.")
    if post.get("creator") != creator:
        raise ValueError("Every exported post must belong to the selected creator.")
    title, caption = post.get("title"), post.get("caption")
    if not isinstance(title, str) or (caption is not None and not isinstance(caption, str)):
        raise ValueError("Demo post titles and captions must be source text.")
    date = post.get("published_at")
    if date is not None:
        if not isinstance(date, str):
            raise ValueError("Publication dates must be ISO text or unknown.")
        try:
            datetime.fromisoformat(date.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Publication dates must be ISO text or unknown.") from exc
    segments = []
    for item in post.get("segments", []):
        if not isinstance(item, dict) or item.get("modality") not in {"caption", "speech", "ocr"}:
            raise ValueError("Demo evidence must identify caption, speech, or OCR modality.")
        identity, text = item.get("id"), item.get("text")
        if (not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_:-]{1,300}", identity)
                or not identity.startswith(code + ":")):
            raise ValueError("Demo segments require their original safe post-scoped identity.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Demo segments require nonempty source text.")
        start, end = item.get("start_seconds"), item.get("end_seconds")
        if item["modality"] == "caption":
            if start is not None or end is not None:
                raise ValueError("Post captions must not acquire artificial timestamps.")
        elif _number(start) is None or _number(end) is None or end < start:
            raise ValueError("Speech and OCR must preserve valid source timestamps.")
        segment = {"id": identity, "modality": item["modality"], "text": text,
                   "start_seconds": start, "end_seconds": end}
        if item.get("speaker") is not None:
            if not isinstance(item["speaker"], str):
                raise ValueError("A source speaker must be text when present.")
            segment["speaker"] = item["speaker"]
        segments.append(segment)
    processing = {"media": "not_retained"}
    original_statuses = post.get("processing", {})
    for modality in ("caption", "speech", "ocr"):
        status = original_statuses.get(modality) if isinstance(original_statuses, dict) else None
        processing[modality] = status if isinstance(status, str) and status in _STATUSES else "unknown"
    result = {"schema_version": 1, "id": code, "creator": creator,
              "url": f"https://www.instagram.com/reel/{code}/", "title": title,
              "published_at": date, "duration_seconds": _number(post.get("duration_seconds")),
              "caption": caption, "segments": segments, "processing": processing, "media": []}
    fingerprints = []
    for item in list(post.get("media", [])) + list(post.get("source_media", [])):
        if not isinstance(item, dict):
            continue
        digest, size = item.get("sha256"), item.get("bytes")
        if isinstance(digest, str) and _HASH.fullmatch(digest) and isinstance(size, int) and not isinstance(size, bool) and size >= 0:
            fingerprint = {"sha256": digest.lower(), "bytes": size}
            if fingerprint not in fingerprints:
                fingerprints.append(fingerprint)
    if fingerprints:
        result["source_media"] = fingerprints
    return result


def _inventory(root):
    """Inspect only the fixed export layout; never traverse arbitrary folders."""
    result = {}
    for entry in root.iterdir():
        if _linked(entry):
            raise ValueError("Demo output must not contain symbolic links or junctions.")
        if entry.name in {"records", "notes"} and entry.is_dir():
            extension = ".json" if entry.name == "records" else ".md"
            for child in entry.iterdir():
                if (_linked(child) or not child.is_file() or child.suffix != extension
                        or not _ID.fullmatch(child.stem)):
                    raise ValueError("The output folder contains files not managed by this exporter.")
                result[f"{entry.name}/{child.name}"] = _hash(child)
        elif entry.name == "library.sqlite3" and entry.is_file():
            result[entry.name] = _hash(entry)
        elif entry.name != _MARKER or not entry.is_file():
            raise ValueError("The output folder contains files not managed by this exporter.")
    return result


def _existing(root, creator):
    if not root.exists():
        return None
    if not root.is_dir() or _linked(root):
        raise ValueError("Demo output must be an ordinary directory.")
    if not any(root.iterdir()):
        return None
    marker = root / _MARKER
    if not marker.is_file() or _linked(marker):
        raise ValueError("Choose an empty output folder; existing foreign libraries are never overwritten.")
    try:
        saved = json.loads(marker.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError("The existing output is not a recognized matching demo export.") from exc
    if (not isinstance(saved, dict) or set(saved) != {"format", "schema_version", "creator", "files"}
            or saved.get("format") != _FORMAT or saved.get("schema_version") != 1
            or saved.get("creator") != creator or not isinstance(saved.get("files"), dict)):
        raise ValueError("The existing output is not a recognized matching demo export.")
    actual = _inventory(root)
    if saved["files"] != actual:
        raise ValueError("The previous export contains edits or foreign files; choose an empty folder.")
    return actual


def export_demo(library: Library, creator: str, output: Path) -> dict:
    """Export one creator's allowlisted evidence without copying source media.

    A matching export can be refreshed only while all its generated files are
    unchanged. Existing foreign content is never removed or overwritten.
    """
    requested_output = Path(output)
    if _linked(requested_output):
        raise ValueError("Demo output must not be a symbolic link or junction.")
    output = requested_output.resolve()
    source = library.root.resolve()
    if output == source or source.is_relative_to(output):
        raise ValueError("Demo output must not replace the source library or its parent.")
    if any(output.is_relative_to(folder.resolve()) for folder in (library.records_dir, library.notes_dir)):
        raise ValueError("Demo output must stay outside the source records and notes folders.")
    posts = library.posts(creator)
    if not posts:
        raise ValueError("Choose a creator with indexed posts to export.")
    creator = posts[0]["creator"]
    sanitized = [_sanitize(post, creator) for post in posts]
    previous = _existing(output, creator)
    expected_records = {f"records/{post['id']}.json": _dump(post) for post in sanitized}
    unchanged = bool(previous) and {name for name in previous if name.startswith("records/")} == set(expected_records)
    if unchanged:
        unchanged = all((output / name).read_text(encoding="utf-8") == text for name, text in expected_records.items())
    if not unchanged:
        destination = Library(output)
        for name, text in expected_records.items():
            _write(output / name, text)
        # Only unchanged, marker-verified files from a prior export can be
        # removed. Source records and original media are outside this layout.
        for name in previous or {}:
            if name.startswith("records/") and name not in expected_records:
                (output / name).unlink()
            elif name.startswith("notes/") and f"records/{Path(name).stem}.json" not in expected_records:
                (output / name).unlink()
        destination.rebuild()
        marker = {"format": _FORMAT, "schema_version": 1, "creator": creator, "files": _inventory(output)}
        _write(output / _MARKER, _dump(marker))
    return {"creator": creator, "posts": len(sanitized),
            "segments": sum(len(post["segments"]) for post in sanitized),
            "captions": sum(any(item["modality"] == "caption" for item in post["segments"]) for post in sanitized),
            "speech": sum(any(item["modality"] == "speech" for item in post["segments"]) for post in sanitized),
            "ocr": sum(any(item["modality"] == "ocr" for item in post["segments"]) for post in sanitized),
            "media_retained": False, "unchanged": unchanged}
