"""Portable source records, readable exports, and creator-scoped local search.

JSON records are authoritative. Markdown and the SQLite FTS5 index are derived
and can be rebuilt without downloading anything or loading a language model.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 1
_POST_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_CREATOR = re.compile(r"[a-z0-9_.]{1,30}\Z")
_VIDEO_SUFFIXES = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi"}
_STOPWORDS = set("""a an and are as at be been being by can could did do does
    for from had has have how i if in into is it its me my of on or our please
    say says said should show tell than that the their them then there these
    they this those to us was we were what when where which who why will with
    would you your about explain creator video videos reel reels""".split())


def _creator(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("A creator handle is required.")
    value = value.strip().lstrip("@").lower()
    if not _CREATOR.fullmatch(value):
        raise ValueError("Use one valid Instagram creator handle.")
    return value


def _post_id(value: str) -> str:
    if not isinstance(value, str) or not _POST_ID.fullmatch(value):
        raise ValueError("Use a valid post shortcode.")
    return value


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _atomic_write(path: Path, text: str) -> bool:
    """Replace a complete file atomically; preserve unchanged files and mtimes."""
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
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
    return True


def _serialize(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _published_at(record):
    value = record.get("published_at")
    if isinstance(value, str):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        except ValueError:
            pass
    timestamp = _number(record.get("timestamp"))
    if timestamp is not None:
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError):
            pass
    return None


def _title(caption, code):
    if not caption:
        return f"Reel {code}"
    first = re.split(r"(?<=[.!?])\s+|\n", caption.strip(), maxsplit=1)[0]
    return first if len(first) <= 120 else first[:117].rstrip() + "..."


def _segment(code, modality, text, start=None, end=None, speaker=None):
    identity = json.dumps([code, modality, start, end, text, speaker], ensure_ascii=False)
    result = {"id": f"{code}:{modality}:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}",
              "modality": modality, "text": text, "start_seconds": start, "end_seconds": end}
    if speaker:
        result["speaker"] = speaker
    return result


def _markdown(post):
    # JSON string literals are also valid quoted YAML scalars.
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    lines = ["---", f"id: {quote(post['id'])}", f"creator: {quote(post['creator'])}",
             f"url: {quote(post['url'])}", f"published_at: {quote(post['published_at'])}",
             "---", "", f"# {post['title']}", "", f"Source: {post['url']}",
             f"Published: {post['published_at'] or 'Unknown'}", "",
             "Coverage: imported posts only; this library does not establish full profile coverage.", ""]
    for modality, heading in (("caption", "Post caption"), ("speech", "Speech transcript"), ("ocr", "On-screen OCR")):
        lines.extend([f"## {heading}", "", f"Status: {post['processing'][modality]}", ""])
        for segment in post["segments"]:
            if segment["modality"] != modality:
                continue
            lines.append(f"<a id=\"{segment['id']}\"></a>")
            if segment["start_seconds"] is not None:
                interval = f"{segment['start_seconds']:g}–{segment['end_seconds']:g} seconds"
                if modality == "ocr":
                    interval += " (sample observations; not exact text boundaries)"
                lines.append(f"[{interval}]")
            if segment.get("speaker"):
                lines.append(f"Speaker: {segment['speaker']}")
            lines.extend([segment["text"], ""])
        if not any(segment["modality"] == modality for segment in post["segments"]):
            lines.extend(["No text evidence available.", ""])
    return "\n".join(lines).rstrip() + "\n"


class Library:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.records_dir = self.root / "records"
        self.notes_dir = self.root / "notes"
        self.database_path = self.root / "library.sqlite3"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.notes_dir.mkdir(parents=True, exist_ok=True)

    def _relative(self, path):
        try:
            return Path(os.path.relpath(path, self.root)).as_posix()
        except ValueError as exc:
            raise ValueError("The library and referenced media must be on the same drive.") from exc

    def _records(self):
        records = []
        for path in sorted(self.records_dir.glob("*.json")):
            if path.is_symlink():
                raise ValueError("Library records must not be symbolic links.")
            record = _json(path)
            if not isinstance(record, dict) or _post_id(record.get("id")) != path.stem:
                raise ValueError(f"Invalid library record: {path.name}")
            _creator(record.get("creator"))
            records.append(record)
        return records

    def _signature(self):
        digest = hashlib.sha256()
        for path in sorted(self.records_dir.glob("*.json")):
            if path.is_symlink():
                raise ValueError("Library records must not be symbolic links.")
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def _verification(self, path):
        if not path.is_file():
            return {}
        data = _json(path)
        if not isinstance(data, list):
            return {}
        result = {}
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("file"), str):
                parts = item["file"].replace("\\", "/").split("/")
                if len(parts) >= 2 and _POST_ID.fullmatch(parts[-2]):
                    result[parts[-2]] = item
        return result

    def _media(self, record, data_root, code):
        expected = (data_root / "media" / code).resolve()
        lexical = data_root / "media" / code
        if lexical.is_symlink() or not expected.is_relative_to(data_root):
            raise ValueError(f"Media folder escapes the manifest data root for {code}.")
        candidates = []
        for item in record.get("files", []):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError(f"Invalid media record for {code}.")
            path = data_root / item["path"]
            if path.is_symlink() or path.resolve().parent != expected:
                raise ValueError(f"Media path must identify this post's folder for {code}.")
            if path.suffix.lower() in _VIDEO_SUFFIXES and path.is_file() and path.stat().st_size > 0:
                candidates.append(path.resolve())
        # Older manifests occasionally lack a files list; inspect only this post.
        if not record.get("files") and expected.is_dir():
            candidates = [path.resolve() for path in sorted(expected.iterdir())
                          if path.is_file() and not path.is_symlink()
                          and path.suffix.lower() in _VIDEO_SUFFIXES and path.stat().st_size > 0]
        return [{"path": self._relative(path), "bytes": path.stat().st_size, "sha256": _hash(path)}
                for path in sorted(set(candidates))]

    def _extracted(self, directory, code, modality, media):
        if directory is None:
            return [], "missing", {}, []
        root = Path(directory).resolve()
        names = [Path(item["path"]).name for item in media] or ["video.mp4"]
        candidates = [root / code / (name + ".json") for name in names]
        candidates.extend([root / code / "video.mp4.json", root / f"{code}.json"])
        path = next((path for path in dict.fromkeys(candidates) if path.is_file()), None)
        if path is None:
            return [], "missing", {}, []
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"{modality} evidence must stay in the supplied extraction folder.")
        try:
            payload = _json(path)
        except (ValueError, OSError):
            return [], "invalid", {}, [f"{code}: {modality} output is unreadable JSON."]
        if not isinstance(payload, dict) or not isinstance(payload.get("segments", []), list):
            return [], "invalid", {}, [f"{code}: {modality} output has an invalid schema."]
        if payload.get("status") != "complete":
            status = payload.get("status")
            return [], "failed" if status == "failed" else "incomplete", {}, []
        fingerprint = payload.get("source", {})
        if isinstance(fingerprint, dict) and media:
            source = (self.root / media[0]["path"]).resolve()
            stat = source.stat()
            if (("size" in fingerprint and fingerprint["size"] != stat.st_size)
                    or ("mtime_ns" in fingerprint and fingerprint["mtime_ns"] != stat.st_mtime_ns)
                    or ("sha256" in fingerprint and fingerprint["sha256"] != media[0]["sha256"])):
                return [], "stale", {}, [f"{code}: {modality} output does not match the current media fingerprint."]
        segments, warnings = [], []
        for item in payload.get("segments", []):
            if not isinstance(item, dict):
                warnings.append(f"{code}: ignored an invalid {modality} segment.")
                continue
            text = _text(item.get("text"))
            if not text:
                continue
            start = _number(item.get("start_seconds" if modality == "speech" else "first_seen_seconds"))
            end = _number(item.get("end_seconds" if modality == "speech" else "last_seen_seconds"))
            if start is None or end is None or end < start:
                warnings.append(f"{code}: ignored {modality} text without valid source timestamps.")
                continue
            segments.append(_segment(code, modality, text, start, end, _text(item.get("speaker"))))
        # Repeated identical source segments should not inflate retrieval results.
        segments = list({segment["id"]: segment for segment in segments}.values())
        details = {"output": self._relative(path.resolve())}
        if modality == "speech" and _text(payload.get("language")):
            details["language"] = payload["language"]
        if isinstance(payload.get("config"), dict):
            allowed = {"engine", "model", "model_name", "language", "device", "compute_type",
                       "fps", "schema_version", "beam_size", "vad_filter", "version"}
            details["config"] = {key: value for key, value in payload["config"].items()
                                 if key in allowed and isinstance(value, (str, int, float, bool, type(None)))
                                 and (not isinstance(value, float) or math.isfinite(value))}
        status = "partial" if warnings else ("complete" if segments else "empty")
        return segments, status, details, warnings

    def import_manifest(self, manifest_path: Path, creator: str, transcripts_dir: Path | None = None,
                        ocr_dir: Path | None = None) -> dict:
        creator = _creator(creator)
        manifest_path = Path(manifest_path).resolve()
        manifest = _json(manifest_path)
        if not isinstance(manifest, dict) or not isinstance(manifest.get("reels"), dict):
            raise ValueError("Manifest must contain a reels object.")
        data_root = manifest_path.parent
        verification = self._verification(data_root / "verification.json")
        summary = {"creator": creator, "posts": 0, "imported": 0, "updated": 0,
                   "unchanged": 0, "captions": 0, "speech": 0, "ocr": 0, "warnings": []}
        pending = []
        for code, record in sorted(manifest["reels"].items()):
            _post_id(code)
            if not isinstance(record, dict):
                raise ValueError(f"Invalid manifest record for {code}.")
            existing = self.get_post(code)
            if existing and existing["creator"] != creator:
                raise ValueError(f"Post {code} already belongs to another creator in this library.")
            media = self._media(record, data_root, code)
            caption = _text(record.get("caption"))
            if not caption:
                sidecar = data_root / "media" / code / "video.txt"
                if sidecar.is_file() and not sidecar.is_symlink() and sidecar.resolve().is_relative_to(data_root):
                    caption = _text(sidecar.read_text(encoding="utf-8-sig"))
            segments = [_segment(code, "caption", caption)] if caption else []
            processing = {"media": "available" if media else "missing", "caption": "complete" if caption else "missing"}
            extractions = {}
            for modality, directory in (("speech", transcripts_dir), ("ocr", ocr_dir)):
                extracted, status, details, warnings = self._extracted(directory, code, modality, media)
                # An omitted optional directory leaves earlier imported evidence intact.
                if directory is None and existing:
                    extracted = [item for item in existing["segments"] if item["modality"] == modality]
                    previous_media = [(item["path"], item["sha256"]) for item in existing.get("media", [])]
                    current_media = [(item["path"], item["sha256"]) for item in media]
                    if previous_media != current_media and extracted:
                        extracted, status = [], "stale"
                    else:
                        status = existing["processing"].get(modality, "missing")
                    details = existing.get("extractions", {}).get(modality, {})
                segments.extend(extracted)
                processing[modality] = status
                if details:
                    extractions[modality] = details
                summary["warnings"].extend(warnings)
            duration = _number(record.get("duration_seconds"))
            if duration is None:
                duration = _number(verification.get(code, {}).get("duration_seconds"))
            post = {"schema_version": SCHEMA_VERSION, "id": code, "creator": creator,
                    "url": f"https://www.instagram.com/reel/{code}/", "title": _title(caption, code),
                    "published_at": _published_at(record), "duration_seconds": duration,
                    "caption": caption, "segments": segments, "processing": processing,
                    "media": media, "extractions": extractions}
            pending.append((code, existing, post))
        # Validate all inputs before replacing any canonical record.
        for code, existing, post in pending:
            changed = _atomic_write(self.records_dir / f"{code}.json", _serialize(post))
            _atomic_write(self.notes_dir / f"{code}.md", _markdown(post))
            summary["posts"] += 1
            summary["imported" if existing is None else ("updated" if changed else "unchanged")] += 1
            for modality, field in (("caption", "captions"), ("speech", "speech"), ("ocr", "ocr")):
                summary[field] += int(any(segment["modality"] == modality for segment in post["segments"]))
        summary["index"] = self.rebuild() if (summary["imported"] or summary["updated"] or not self.database_path.is_file()) else self._ensure_index()
        return summary

    def rebuild(self) -> dict:
        records = self._records()
        signature = self._signature()
        with closing(sqlite3.connect(self.database_path, timeout=30)) as database, database:
            database.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            database.execute("""CREATE TABLE IF NOT EXISTS segments (
                id TEXT PRIMARY KEY, post_id TEXT NOT NULL, creator TEXT NOT NULL, url TEXT NOT NULL,
                modality TEXT NOT NULL, text TEXT NOT NULL, start_seconds REAL, end_seconds REAL,
                title TEXT NOT NULL, published_at TEXT)""")
            try:
                database.execute("CREATE VIRTUAL TABLE IF NOT EXISTS segment_fts USING fts5(text, tokenize='porter unicode61')")
            except sqlite3.OperationalError as exc:
                raise RuntimeError("This Python SQLite build requires FTS5 for the local search index.") from exc
            database.execute("DELETE FROM segments")
            database.execute("DELETE FROM segment_fts")
            count = 0
            for record in records:
                _atomic_write(self.notes_dir / f"{record['id']}.md", _markdown(record))
                for segment in record["segments"]:
                    cursor = database.execute("""INSERT INTO segments
                        (id,post_id,creator,url,modality,text,start_seconds,end_seconds,title,published_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""", (segment["id"], record["id"], record["creator"],
                        record["url"], segment["modality"], segment["text"], segment["start_seconds"],
                        segment["end_seconds"], record["title"], record["published_at"]))
                    database.execute("INSERT INTO segment_fts(rowid,text) VALUES (?,?)", (cursor.lastrowid, segment["text"]))
                    count += 1
            database.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES ('signature',?)", (signature,))
            database.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES ('schema_version',?)", (str(SCHEMA_VERSION),))
        return {"posts": len(records), "segments": count, "database": str(self.database_path)}

    def _ensure_index(self):
        signature = self._signature()
        if self.database_path.is_file():
            try:
                with closing(sqlite3.connect(self.database_path, timeout=30)) as database, database:
                    saved = database.execute("SELECT value FROM metadata WHERE key='signature'").fetchone()
                    if saved and saved[0] == signature:
                        return {"posts": len(list(self.records_dir.glob('*.json'))),
                                "segments": database.execute("SELECT count(*) FROM segments").fetchone()[0],
                                "database": str(self.database_path)}
            except sqlite3.DatabaseError as exc:
                # A malformed binary is an explicit repair task; do not delete user files.
                if "not a database" in str(exc) or "malformed" in str(exc):
                    raise RuntimeError("The local index is corrupt; move library.sqlite3 aside and rebuild it from records.") from exc
        return self.rebuild()

    def creators(self) -> list[dict]:
        result = {}
        for record in self._records():
            handle = record["creator"]
            value = result.setdefault(handle, {"handle": handle, "posts": 0, "captions": 0,
                "speech": 0, "ocr": 0, "duration_seconds": 0.0,
                "coverage_note": "Imported posts only; profile completeness and newest-first ordering are unverified."})
            value["posts"] += 1
            value["duration_seconds"] += record.get("duration_seconds") or 0
            for modality, key in (("caption", "captions"), ("speech", "speech"), ("ocr", "ocr")):
                value[key] += int(any(item["modality"] == modality for item in record["segments"]))
        for value in result.values():
            value["duration_seconds"] = round(value["duration_seconds"], 3)
        return [result[handle] for handle in sorted(result)]

    def posts(self, creator: str) -> list[dict]:
        creator = _creator(creator)
        return [record for record in self._records() if record["creator"] == creator]

    def get_post(self, post_id: str) -> dict | None:
        path = self.records_dir / f"{_post_id(post_id)}.json"
        if path.is_symlink():
            raise ValueError("Library records must not be symbolic links.")
        if not path.is_file():
            return None
        record = _json(path)
        if not isinstance(record, dict) or record.get("id") != post_id:
            raise ValueError("Post record identity does not match its filename.")
        return record

    def search(self, query: str, creator: str, limit: int = 6) -> list[dict]:
        creator = _creator(creator)
        if not isinstance(query, str):
            raise ValueError("Search query must be text.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Search limit must be between 1 and 100.")
        if len(query) > 4000:
            raise ValueError("Search query is too long.")
        terms = list(dict.fromkeys(term for term in re.findall(r"[^\W_]+", query.casefold())
                                  if term not in _STOPWORDS))[:32]
        if not terms:
            return []
        # Quote every token: user input cannot become FTS operators or SQL syntax.
        expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        self._ensure_index()
        with closing(sqlite3.connect(self.database_path, timeout=30)) as database, database:
            database.row_factory = sqlite3.Row
            rows = database.execute("""SELECT s.*, -bm25(segment_fts) AS score
                FROM segment_fts JOIN segments s ON s.rowid=segment_fts.rowid
                WHERE segment_fts MATCH ? AND s.creator=?
                ORDER BY bm25(segment_fts),s.id LIMIT ?""", (expression, creator, limit)).fetchall()
        return [dict(row) for row in rows]
