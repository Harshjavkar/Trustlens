"""A single, bounded collector. Authentication is always supplied explicitly."""
from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def canonical_reel_url(value: str) -> tuple[str, str]:
    parsed = urlparse(value.strip())
    match = re.fullmatch(r"/(?:reel|reels|p)/([A-Za-z0-9_-]+)/?", parsed.path)
    if parsed.scheme != "https" or parsed.hostname not in {"instagram.com", "www.instagram.com"} or parsed.port or not match or parsed.username:
        raise ValueError("Use an HTTPS Instagram reel/post link, e.g. https://www.instagram.com/reel/CODE/.")
    code = match.group(1)
    return f"https://www.instagram.com/reel/{code}/", code


def read_urls(path: Path) -> list[str]:
    # Validate the entire input before making any network requests.
    return list(dict.fromkeys(canonical_reel_url(line)[0] for line in path.read_text(encoding="utf-8-sig").splitlines()
                              if line.strip() and not line.lstrip().startswith("#")))


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def clean_error(exc: Exception) -> str:
    # Library errors can embed temporary CDN URLs, cookies or access tokens.
    message = re.sub(r"(?im)^.*\b(?:sessionid|csrftoken|access_token|authorization|cookie|set-cookie)\b.*$", "[sensitive error details redacted]", str(exc))
    message = re.sub(r"https?://\S+", "[URL]", message)
    return message[:700]


def access_blocked(message: str) -> bool:
    message = message.lower()
    return any(word in message for word in ("429", "too many requests", "rate-limit", "rate limit", "login required", "log in", "login_required", "checkpoint", "challenge_required", "please wait", "401", "403"))


class QuietLogger:
    def debug(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


def download_ytdlp(args, url, destination):
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        executable = Path(__file__).resolve().parents[1] / "tools/yt-dlp.exe"
        if not executable.is_file():
            raise RuntimeError("yt-dlp is missing. Run setup.ps1 or install the official portable release.")
        command = [str(executable), "--ignore-config", "--no-playlist", "--quiet", "--no-warnings",
                   "--dump-single-json", "--no-simulate", "--socket-timeout", "20", "--retries", "1",
                   "--fragment-retries", "1", "--extractor-retries", "0", "--no-overwrites",
                   "--format", "best[height<=720]/best", "--output", str(destination / "video.%(ext)s")]
        if args.cookies_file:
            command += ["--cookies", str(args.cookies_file.resolve())]
        command.append(url)
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=getattr(args, "download_timeout", 90))
        if result.returncode:
            raise RuntimeError(result.stderr or "Portable yt-dlp failed.")
        info = json.loads(result.stdout)
        return {"caption": info.get("description"), "duration_seconds": info.get("duration"),
                "uploader": info.get("uploader"), "timestamp": info.get("timestamp"),
                "execution_mode": "official portable executable; combined format"}
    import imageio_ffmpeg
    options = {
        "outtmpl": str(destination / "video.%(ext)s"),
        "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "merge_output_format": "mp4", "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "noplaylist": True, "quiet": True, "no_warnings": True, "logger": QuietLogger(),
        "socket_timeout": 20, "retries": 1, "fragment_retries": 1,
        "extractor_retries": 0, "concurrent_fragment_downloads": 1,
        "continuedl": True, "overwrites": False,
    }
    if args.cookies_file:
        options["cookiefile"] = str(args.cookies_file.resolve())
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        if not info or info.get("_type") in {"playlist", "multi_video"}:
            raise RuntimeError("Expected one reel video; multi-item results are not supported.")
        return {"caption": info.get("description"), "duration_seconds": info.get("duration"),
                "uploader": info.get("uploader"), "timestamp": info.get("timestamp")}


def cache_valid(record, root):
    if record.get("status") not in {"downloaded", "cached"} or not record.get("files"):
        return False
    for item in record["files"]:
        candidate = (root / item["path"]).resolve()
        if not candidate.is_relative_to(root.resolve()) or not candidate.is_file() or candidate.stat().st_size != item["bytes"] or item["bytes"] <= 0:
            return False
    return True


def run_fetch(args):
    started = time.monotonic()
    root = args.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"schema_version": 1, "reels": {}}
    summary = {"started_at": utc_now(), "requested": args.limit, "backend": "yt-dlp",
               "discovered": 0, "downloaded": 0, "cached": 0, "failed": 0,
               "stop_reason": None, "timings": [], "cached_ids": [], "preexisting_media": 0, "errors": []}
    try:
        if not args.urls:
            raise ValueError("A saved URL list is required (--urls).")
        urls = read_urls(args.urls)
        iterator = iter(urls)
        for index in range(args.limit):
            if time.monotonic() - started >= args.max_minutes * 60:
                summary["stop_reason"] = "time_budget"
                break
            try:
                url = next(iterator)
            except StopIteration:
                summary["stop_reason"] = "end_of_available_items"
                break
            _, code = canonical_reel_url(url)
            summary["discovered"] += 1
            record = manifest["reels"].get(code, {})
            if cache_valid(record, root):
                summary["cached"] += 1
                summary["cached_ids"].append(code)
                print(f"[{index + 1}/{args.limit}] {code}: cached", flush=True)
                continue
            destination = root / "media" / code
            destination.mkdir(parents=True, exist_ok=True)
            if any(destination.iterdir()):
                summary["preexisting_media"] += 1
            item_started = time.monotonic()
            try:
                metadata = download_ytdlp(args, url, destination)
                files = [{"path": str(path.relative_to(root)), "bytes": path.stat().st_size}
                         for path in destination.iterdir() if path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"} and path.stat().st_size > 0]
                if not files:
                    raise RuntimeError("The downloader returned without a complete video file.")
                record = {"url": url, "status": "downloaded", "backend": "yt-dlp",
                          "completed_at": utc_now(), "files": files, **metadata}
                summary["downloaded"] += 1
            except Exception as exc:
                error = clean_error(exc)
                record = {"url": url, "status": "failed", "error": error, "attempted_at": utc_now()}
                summary["failed"] += 1
                if access_blocked(error):
                    summary["stop_reason"] = "access_or_rate_limit"
            manifest["reels"][code] = record
            save_json(manifest_path, manifest)
            elapsed = time.monotonic() - item_started
            summary["timings"].append({"id": code, "seconds": round(elapsed, 3), "status": record["status"]})
            print(f"[{index + 1}/{args.limit}] {code}: {record['status']} ({elapsed:.1f}s)", flush=True)
            if summary["stop_reason"]:
                break
            if index + 1 < args.limit:
                remaining = args.max_minutes * 60 - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(min(args.delay, remaining))
        else:
            summary["stop_reason"] = "requested_limit"
    except Exception as exc:
        error = clean_error(exc)
        summary["stop_reason"] = "access_or_rate_limit" if access_blocked(error) else "discovery_or_input_error"
        summary["errors"].append(error)
    summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
    summary["finished_at"] = utc_now()
    summary["scope"] = "collection only; OCR excluded"
    summary["time_budget_note"] = "Soft budget: checked between items; an in-flight request or library rate-limit wait may overrun."
    report = root / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-fetch.json")
    save_json(report, summary)
    return summary, report
