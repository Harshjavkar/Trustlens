from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from .collector import run_fetch, save_json


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Must be greater than zero.")
    return number


def positive_float(value):
    number = float(value)
    if not 0 < number < float("inf"):
        raise argparse.ArgumentTypeError("Must be a finite number greater than zero.")
    return number


def doctor():
    results = {"python": sys.version.split()[0], "executable": sys.executable}
    ready = True
    for package in ("yt-dlp", "imageio-ffmpeg"):
        try:
            results[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            results[package] = "missing"
            ready = False
    try:
        import imageio_ffmpeg
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run([executable, "-version"], capture_output=True, text=True, timeout=15, check=True)
        results["ffmpeg"] = result.stdout.splitlines()[0]
    except Exception:
        results["ffmpeg"] = "unavailable"
        ready = False
    results["ocr_installed"] = all(importlib.util.find_spec(name) is not None for name in ("rapidocr_onnxruntime", "cv2"))
    results["speech_installed"] = importlib.util.find_spec("faster_whisper") is not None
    results["evidence_app_ready"] = True  # The evidence app uses Python's standard library.
    tools = Path(__file__).resolve().parents[1] / "tools"
    results["portable_tools"] = {}
    for name, relative in (("yt-dlp", "yt-dlp.exe"),):
        executable = tools / relative
        if executable.is_file():
            try:
                check = subprocess.run([str(executable), "--version"], capture_output=True, text=True, timeout=20, check=True)
                results["portable_tools"][name] = check.stdout.strip()
            except Exception:
                results["portable_tools"][name] = "check failed"
    results["python_workflow_ready"] = ready
    results["portable_url_fetch_ready"] = results["portable_tools"].get("yt-dlp") not in (None, "check failed")
    results["instagram_access_checked_by_doctor"] = False
    print(json.dumps(results, indent=2))
    return 0 if ready or results["portable_url_fetch_ready"] else 1


def build_parser():
    parser = argparse.ArgumentParser(description="InstaNT: collect source evidence and ask questions of a creator's indexed material.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check local installation without accessing Instagram")
    bootstrap = commands.add_parser("bootstrap-demo", help="Prepare the bundled text-only pilot in an empty library, without downloads")
    bootstrap.add_argument("--library", type=Path, default=Path("data/library"))
    speech = commands.add_parser("transcribe", help="Transcribe local media with optional faster-whisper; no Instagram access")
    speech.add_argument("--input", type=Path, default=Path("data/live-first-test/media"))
    speech.add_argument("--output", type=Path, default=Path("data/live-first-test/transcripts"))
    speech.add_argument("--model", default="base", help="Local Whisper model name; first use may download its weights")
    speech.add_argument("--model-path", type=Path, help="Existing local model directory, for offline use")
    speech.add_argument("--device", choices=("cpu", "cuda", "auto"), default="cpu")
    speech.add_argument("--compute-type", default="int8")
    speech.add_argument("--language", default=None)
    library = commands.add_parser("library", help="Import existing captions, transcripts and OCR; export evidence notes and index")
    library.add_argument("--manifest", type=Path, default=Path("data/live-first-test/manifest.json"))
    library.add_argument("--creator", required=True)
    library.add_argument("--root", type=Path, default=Path("data/library"))
    library.add_argument("--transcripts", type=Path, default=Path("data/live-first-test/transcripts"))
    library.add_argument("--ocr", type=Path, default=Path("data/live-first-test/ocr"))
    index = commands.add_parser("reindex", help="Rebuild the disposable search index from canonical evidence records")
    index.add_argument("--library", type=Path, default=Path("data/library"))
    demo = commands.add_parser("export-demo", help="Export a creator's text evidence without copying media or local paths")
    demo.add_argument("--library", type=Path, default=Path("data/library"))
    demo.add_argument("--creator", required=True)
    demo.add_argument("--output", type=Path, required=True)
    ask = commands.add_parser("ask", help="Preview source excerpts within one creator's saved evidence")
    ask.add_argument("--library", type=Path, default=Path("data/library"))
    ask.add_argument("--creator", required=True)
    ask.add_argument("--question", required=True)
    app = commands.add_parser("serve", help="Start the local InstaNT web app")
    app.add_argument("--library", type=Path, default=Path("data/library"))
    app.add_argument("--media-root", type=Path, default=Path("data/live-first-test/media"))
    app.add_argument("--port", type=positive_int, default=8765)
    app.add_argument("--env-file", type=Path, default=Path(".env.local"), help="Private chat settings; existing environment variables take precedence")
    discovery = commands.add_parser("discover", help="Save reel URLs separately; can import links observed in a browser")
    discovery.add_argument("--profile", required=True)
    discovery.add_argument("--output", type=Path, default=Path("data/queues/discovered.txt"))
    discovery.add_argument("--limit", type=positive_int, default=20)
    discovery_source = discovery.add_mutually_exclusive_group()
    discovery_source.add_argument("--html", type=Path, help="Saved public profile HTML")
    discovery_source.add_argument("--import-json", type=Path, help="JSON list or observed browser link snapshot")
    pipeline = commands.add_parser("pipeline", help="Download saved URLs with yt-dlp and preserve resumable progress")
    pipeline.add_argument("--urls", type=Path, required=True)
    pipeline.add_argument("--data-dir", type=Path, default=Path("data"))
    pipeline.add_argument("--limit", type=positive_int, default=20)
    pipeline.add_argument("--max-minutes", type=positive_float, default=10)
    pipeline.add_argument("--delay", type=positive_float, default=2)
    pipeline.add_argument("--cookies-file", type=Path)
    for name in ("fetch", "benchmark"):
        command = commands.add_parser(name, help="Collect reels" if name == "fetch" else "Measure collection, optionally followed by OCR")
        command.add_argument("--urls", type=Path, required=True, help="Text file with one Instagram reel URL per line")
        command.add_argument("--limit", type=positive_int, default=20)
        command.add_argument("--data-dir", type=Path, default=Path("data"))
        command.add_argument("--max-minutes", type=positive_float, default=10, help="Soft collection budget; in-flight work may overrun")
        command.add_argument("--delay", type=positive_float, default=2, help="Seconds between uncached items")
        command.add_argument("--cookies-file", type=Path, help="Existing Netscape cookies file, used only by yt-dlp")
        if name == "benchmark":
            command.add_argument("--with-ocr", action="store_true", help="Include local OCR of successfully downloaded/cached videos")
            command.add_argument("--fps", type=positive_float, default=2)
    ocr = commands.add_parser("ocr", help="OCR sampled frames in local videos; never accesses Instagram")
    ocr.add_argument("--input", type=Path, default=Path("data/media"))
    ocr.add_argument("--output", type=Path, default=Path("data/ocr"))
    ocr.add_argument("--fps", type=positive_float, default=2)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor()
    if args.command in {"bootstrap-demo", "transcribe", "library", "reindex", "export-demo", "ask", "serve"}:
        try:
            if args.command == "transcribe":
                from .transcribe import run_transcribe
                result = run_transcribe(args.input, args.output, model=args.model, device=args.device,
                    compute_type=args.compute_type, language=args.language, model_path=args.model_path)
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return 1 if result.get("failed", 0) or result.get("no_speech", 0) else 0
            from .library import Library
            if args.command == "bootstrap-demo":
                from .bootstrap import bootstrap_demo
                result = bootstrap_demo(args.library)
            elif args.command == "library":
                result = Library(args.root).import_manifest(args.manifest, args.creator,
                    transcripts_dir=args.transcripts, ocr_dir=args.ocr)
            elif args.command == "reindex":
                result = Library(args.library).rebuild()
            elif args.command == "export-demo":
                from .demo import export_demo
                result = export_demo(Library(args.library), args.creator, args.output)
            elif args.command == "ask":
                from .answers import answer_question
                result = answer_question(Library(args.library), args.question, args.creator)
            else:
                from .server import serve
                from .config import load_chat_environment
                if args.port > 65535:
                    raise ValueError("Port must be between 1 and 65535.")
                load_chat_environment(args.env_file)
                serve(args.library, args.port, args.media_root)
                return 0
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        except (RuntimeError, ValueError, OSError) as exc:
            from .collector import clean_error
            print(clean_error(exc), file=sys.stderr)
            return 1
    if args.command == "discover":
        if not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", args.profile):
            parser.error("--profile must be an Instagram username, without @ or a URL.")
        for path in (args.html, args.import_json):
            if path is not None and not path.is_file():
                parser.error("The supplied discovery input file does not exist.")
        from .links import discover_links
        summary, report = discover_links(args)
        print(json.dumps(summary, indent=2))
        print(f"Link discovery report: {report}")
        return 1 if summary.get("status") == "failed" else 0
    if args.command == "pipeline":
        if not args.urls.is_file():
            parser.error("--urls must refer to an existing saved URL list.")
        for path in (args.cookies_file,):
            if path is not None and not path.is_file():
                parser.error("The explicitly supplied authentication file does not exist.")
        from .pipeline import run_pipeline
        try:
            summary, report = run_pipeline(args)
        except (RuntimeError, ValueError, OSError) as exc:
            from .collector import clean_error
            print(clean_error(exc), file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2))
        print(f"Pipeline report: {report}")
        return 0 if summary.get("complete") else 1
    if args.command == "ocr":
        from .ocr import run_ocr
        try:
            result = run_ocr(args.input, args.output, args.fps)
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 1 if result.get("failed", 0) else 0
    for name in ("urls", "cookies_file"):
        path = getattr(args, name)
        if path is not None and not path.is_file():
            parser.error(f"--{name.replace('_', '-')} does not exist.")
    if args.command == "benchmark" and args.with_ocr:
        if not all(importlib.util.find_spec(name) is not None for name in ("rapidocr_onnxruntime", "cv2")):
            parser.error("OCR dependencies missing. Run setup.ps1 -WithOcr first.")
    started = time.monotonic()
    ocr_failed = False
    summary, report = run_fetch(args)
    print(f"Collection report: {report}")
    if args.command == "benchmark":
        benchmark = {"collection": summary, "ocr": None, "scope": "collection only"}
        if args.with_ocr and summary["downloaded"] + summary["cached"]:
            from .ocr import run_ocr
            # Only include successful items encountered by this run, not unrelated archive videos.
            import tempfile
            import shutil
            manifest = json.loads((args.data_dir / "manifest.json").read_text(encoding="utf-8"))
            codes = {item["id"] for item in summary["timings"] if item["status"] == "downloaded"}
            # Cached items count toward the sample: record them in collector timings as zero time.
            codes.update(summary.get("cached_ids", []))
            with tempfile.TemporaryDirectory(prefix="reel-lab-ocr-", dir=args.data_dir) as folder:
                sample = Path(folder)
                for code in codes:
                    for entry in manifest["reels"][code]["files"]:
                        source = args.data_dir / entry["path"]
                        shutil.copyfile(source, sample / f"{code}{source.suffix}")
                try:
                    benchmark["ocr"] = run_ocr(sample, args.data_dir / "ocr", args.fps)
                except (RuntimeError, ValueError, OSError) as exc:
                    benchmark["ocr"] = {"failed": len(codes), "processed": 0, "error": str(exc)}
                ocr_failed = bool(benchmark["ocr"].get("failed"))
            benchmark["scope"] = "collection + OCR; includes temporary staging copy time; not an accuracy audit"
        benchmark["elapsed_seconds"] = round(time.monotonic() - started, 3)
        benchmark["requested_sample_completed"] = summary["downloaded"] + summary["cached"] == args.limit and summary["failed"] == 0
        benchmark["timing_target_100_in_600_seconds_met"] = bool(args.with_ocr and args.limit >= 100 and
            not summary["preexisting_media"] and
            summary["downloaded"] == args.limit and benchmark["requested_sample_completed"] and benchmark["ocr"] and
            benchmark["ocr"]["processed"] == args.limit and not benchmark["ocr"].get("failed") and
            benchmark["elapsed_seconds"] <= 600)
        report = report.with_name(report.name.replace("-fetch", "-benchmark"))
        save_json(report, benchmark)
        print(f"Benchmark report: {report}")
    print(json.dumps({key: summary[key] for key in ("downloaded", "cached", "failed", "elapsed_seconds", "stop_reason")}, indent=2))
    return 1 if ocr_failed or summary["failed"] or summary["errors"] or summary["stop_reason"] in {"time_budget", "access_or_rate_limit"} else 0
