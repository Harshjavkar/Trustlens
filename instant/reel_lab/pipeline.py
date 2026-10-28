"""Resumable yt-dlp collection from an explicitly saved Instagram URL list.

Each uncached URL gets one attempt per run. Failed URLs remain in the saved
queue for a later explicit retry. No credentials are requested.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .collector import (cache_valid, canonical_reel_url, clean_error,
                        download_ytdlp, read_urls, save_json, utc_now)


VIDEO_SUFFIXES = {".mp4", ".webm", ".mkv", ".mov", ".m4v"}


def classify_error(message: str) -> str:
    """Distinguish real throttling/protection from ambiguous extractor errors."""
    value = message.lower()
    if re.search(r"\b429\b", value) or "too many requests" in value:
        return "rate_limit"
    if re.search(r"\b(?:401|403)\b", value) or any(term in value for term in
            ("checkpoint", "challenge_required", "challenge required", "private account",
             "private profile", "not authorized", "permission denied")):
        return "access_restricted"
    # yt-dlp sometimes reports "rate-limit reached or login required" without
    # a status code. That is an ambiguous extractor failure, not proof of 429.
    if "please wait" in value and "or login" not in value:
        return "rate_limit"
    return "download_error"


def _files(destination: Path, root: Path):
    # Only direct, nonempty video files in this shortcode's folder count.
    return [{"path": str(path.relative_to(root)), "bytes": path.stat().st_size}
            for path in sorted(destination.iterdir())
            if path.is_file() and not path.is_symlink()
            and path.suffix.lower() in VIDEO_SUFFIXES and path.stat().st_size > 0]


def _valid(record, root: Path, code: str) -> bool:
    if record.get("status") not in {"downloaded", "cached"}:
        return False
    expected = (root / "media" / code).resolve()
    if expected.is_symlink():
        return False
    for item in record.get("files", []):
        candidate = root / item["path"]
        if candidate.is_symlink() or candidate.resolve().parent != expected or candidate.suffix.lower() not in VIDEO_SUFFIXES:
            return False
    return cache_valid({**record, "status": "downloaded"}, root)


def _quarantine_incomplete(destination: Path, root: Path, code: str):
    """A stale nonempty output must not satisfy a new no-overwrite attempt."""
    for path in list(destination.iterdir()):
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES:
            rejected = root / "rejected" / code / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            rejected.mkdir(parents=True, exist_ok=True)
            path.replace(rejected / path.name)


def _write_queue(path: Path, urls):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(url + "\n" for url in urls), encoding="utf-8")
    temporary.replace(path)


def run_pipeline(args):
    """Return (summary, report_path); persist state after every item/transition.

    `remaining` and unresolved.txt cover the entire supplied URL list, including
    URLs beyond the run limit. Counts downloaded/cached concern this run's
    selected, unique URLs. A partial run is explicitly marked complete=False.
    """
    started = time.monotonic()
    if not args.urls:
        raise ValueError("A saved URL list is required (--urls).")
    if args.limit <= 0 or args.max_minutes <= 0 or args.delay < 0:
        raise ValueError("Limit and max minutes must be positive; delay cannot be negative.")
    urls = read_urls(Path(args.urls))  # Validate all URLs before any request.
    if not urls:
        raise ValueError("The URL list is empty; nothing was downloaded.")
    selected = urls[:args.limit]
    root = Path(args.data_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"schema_version": 2, "reels": {}}
    manifest["schema_version"] = 2
    records = manifest.setdefault("reels", {})
    failures = []
    downloaded, cached = set(), set()
    summary = {"started_at": utc_now(), "source_urls": str(Path(args.urls).resolve()),
               "requested": args.limit, "total_urls": len(urls), "selected": len(selected),
               "backend": "yt-dlp", "downloaded": 0, "cached": 0,
               "attempted": 0, "stop_reason": None,
               "timings": [], "errors": [], "scope": "collection only; OCR excluded"}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for url in urls:
        _, code = canonical_reel_url(url)
        record = records.setdefault(code, {"url": url, "status": "pending", "attempts": []})
        record.setdefault("attempts", [])
        record["url"] = url
        if not _valid(record, root, code) and record.get("status") in {"downloaded", "cached"}:
            record["status"] = "pending"
            record["cache_invalidated_at"] = utc_now()

    def persist():
        manifest["updated_at"] = utc_now()
        save_json(manifest_path, manifest)
        unresolved = [url for url in urls if not _valid(records[canonical_reel_url(url)[1]], root, canonical_reel_url(url)[1])]
        _write_queue(root / "queues" / "failed.txt", failures)
        _write_queue(root / "queues" / "unresolved.txt", unresolved)

    def remaining_seconds():
        return args.max_minutes * 60 - (time.monotonic() - started)

    def attempt(url):
        _, code = canonical_reel_url(url)
        record = records[code]
        destination = root / "media" / code
        if destination.is_symlink() or not destination.resolve().is_relative_to(root):
            raise ValueError("Media destination must stay in this run's data folder.")
        destination.mkdir(parents=True, exist_ok=True)
        _quarantine_incomplete(destination, root, code)
        attempt_started = time.monotonic()
        attempt_record = {"run_id": run_id, "backend": "yt-dlp",
                          "started_at": utc_now(), "status": "pending"}
        record["attempts"].append(attempt_record)
        record["status"] = "pending"
        summary["attempted"] += 1
        persist()
        call_args = argparse.Namespace(**vars(args))
        call_args.cookies_file = getattr(args, "cookies_file", None)
        call_args.download_timeout = max(0.01, min(60, remaining_seconds()))
        try:
            metadata = download_ytdlp(call_args, url, destination)
            files = _files(destination, root)
            if not files:
                raise RuntimeError("The downloader returned without a complete video file.")
            # A bounded set of fields prevents signed URLs and cookies entering state.
            for key in ("caption", "duration_seconds", "uploader", "timestamp", "execution_mode"):
                if key in metadata:
                    record[key] = metadata[key]
            record.update(status="downloaded", backend="yt-dlp", completed_at=utc_now(), files=files)
            record.pop("error", None)
            record.pop("failure_kind", None)
            attempt_record["status"] = "downloaded"
            downloaded.add(code)
        except Exception as exc:
            error = clean_error(exc)
            kind = classify_error(error)
            record.update(status="failed", error=error, failure_kind=kind, attempted_at=utc_now())
            attempt_record.update(status="failed", error=error, failure_kind=kind)
            failures.append(url)
            if kind in {"rate_limit", "access_restricted"}:
                summary["stop_reason"] = kind
        attempt_record["elapsed_seconds"] = round(time.monotonic() - attempt_started, 3)
        attempt_record["finished_at"] = utc_now()
        summary["timings"].append({"id": code, "backend": "yt-dlp",
                                   "seconds": attempt_record["elapsed_seconds"], "status": record["status"]})
        print(f"{code}: {record['status']} ({attempt_record['elapsed_seconds']:.1f}s)", flush=True)
        persist()

    def can_attempt():
        if summary["stop_reason"] in {"rate_limit", "access_restricted"}:
            return False
        if remaining_seconds() <= 0:
            summary["stop_reason"] = "time_budget"
            return False
        return True

    def pause():
        if args.delay and summary["stop_reason"] not in {"rate_limit", "access_restricted"}:
            time.sleep(max(0, min(args.delay, remaining_seconds())))

    persist()
    try:
        for url in selected:
            _, code = canonical_reel_url(url)
            if _valid(records[code], root, code):
                records[code]["status"] = "cached"
                cached.add(code)
                persist()
                continue
            if not can_attempt():
                break
            attempt(url)
            pause()
    except KeyboardInterrupt:
        summary["stop_reason"] = "interrupted"
    except Exception as exc:
        summary["stop_reason"] = "pipeline_error"
        summary["errors"].append(clean_error(exc))
    persist()
    successful = {canonical_reel_url(url)[1] for url in urls
                  if _valid(records[canonical_reel_url(url)[1]], root, canonical_reel_url(url)[1])}
    selected_codes = {canonical_reel_url(url)[1] for url in selected}
    summary.update(downloaded=len(downloaded), cached=len(cached), successful=len(downloaded | cached),
                   remaining=len(urls) - len(successful), remaining_selected=len(selected_codes - successful),
                   failed=sum(records[code]["status"] == "failed" for code in selected_codes),
                   pending=sum(records[code]["status"] == "pending" for code in selected_codes),
                   bytes=sum(item["bytes"] for code in selected_codes & successful for item in records[code]["files"]),
                   elapsed_seconds=round(time.monotonic() - started, 3), finished_at=utc_now(),
                   cached_ids=sorted(cached), downloaded_ids=sorted(downloaded))
    summary["complete"] = summary["remaining_selected"] == 0 and not summary["stop_reason"]
    summary["all_input_complete"] = summary["remaining"] == 0 and not summary["stop_reason"]
    summary["stop_reason"] = summary["stop_reason"] or ("selected_complete" if summary["complete"] else "unresolved_items")
    summary["time_budget_note"] = "Each portable process is bounded to at most 60 seconds and the remaining budget; Python yt-dlp request timeouts are soft."
    summary["queues"] = {name: str(root / "queues" / filename) for name, filename in
                         (("failures", "failed.txt"), ("unresolved", "unresolved.txt"))}
    report = root / "runs" / (run_id + "-pipeline.json")
    save_json(report, summary)
    return summary, report
