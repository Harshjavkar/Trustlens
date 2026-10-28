"""A single persistent-queue worker for the local shared creator library.

Only real, indexed evidence makes a creator ready. Public discovery can require
an explicitly saved browser-link snapshot; that limitation is a visible state.
Speech/OCR preparation is a later pipeline stage, not silently implied here.
"""
from __future__ import annotations

import argparse
import threading
from pathlib import Path

from .collector import clean_error
from .jobs import CreatorJobs
from .library import Library
from .links import discover_links
from .pipeline import run_pipeline


class CreatorWorker:
    def __init__(self, library: Library, jobs: CreatorJobs, data_root: Path,
                 *, limit=20, max_minutes=3, delay=2):
        self.library, self.jobs = library, jobs
        self.data_root = Path(data_root).resolve()
        self.limit, self.max_minutes, self.delay = limit, max_minutes, delay
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.jobs.recover_interrupted()
        self.stopped.clear()
        self.thread = threading.Thread(target=self._loop, name="instant-creator-worker", daemon=True)
        self.thread.start()

    def notify(self):
        self.wake.set()

    def stop(self):
        self.stopped.set()
        self.wake.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)

    def _loop(self):
        while not self.stopped.is_set():
            try:
                processed = self.process_one()
            except Exception:
                # Persistent state survives an unexpected registry/database issue.
                processed = False
            if not processed:
                self.wake.wait(2)
                self.wake.clear()

    def process_one(self):
        job = self.jobs.claim_next()
        if not job:
            return False
        handle = job["handle"]
        root = (self.data_root / handle).resolve()
        try:
            if root.parent != self.data_root:
                raise ValueError("Creator data must stay in the configured data folder.")
            self.jobs.update_progress(handle, "Finding accessible public reel links.")
            queue = root / "queues" / "discovered.txt"
            snapshot = root / "sources.json"
            discovery, _ = discover_links(argparse.Namespace(profile=handle, output=queue,
                limit=self.limit, html=None, import_json=snapshot if snapshot.is_file() else None))
            if discovery.get("status") != "ok":
                if discovery.get("stop_reason") == "needs_rendered_browser_snapshot":
                    self.jobs.finish(handle, "needs_source_links", detail=
                        "Instagram did not expose public reel links. A browser-observed source-link snapshot is needed to continue.")
                else:
                    self.jobs.finish(handle, "failed", detail=
                        "Public discovery could not complete. Access may be restricted or temporarily unavailable; retry after resolving it.")
                return True
            if self.stopped.is_set():
                return True  # Keep processing state for explicit startup recovery.
            self.jobs.update_progress(handle, "Saving missing reels and their source captions.")
            collected, _ = run_pipeline(argparse.Namespace(urls=queue, data_dir=root,
                limit=self.limit, max_minutes=self.max_minutes, delay=self.delay, cookies_file=None))
            if collected.get("stop_reason") in {"rate_limit", "access_restricted"}:
                self.jobs.finish(handle, "failed", detail=
                    "Collection stopped at an access restriction or rate limit. Completed files are saved for an explicit retry.")
                return True
            if not (root / "manifest.json").is_file():
                self.jobs.finish(handle, "failed", detail="No source evidence could be saved.")
                return True
            self.jobs.update_progress(handle, "Indexing saved source evidence.")
            self.library.import_manifest(root / "manifest.json", handle,
                transcripts_dir=root / "transcripts", ocr_dir=root / "ocr")
            coverage = next((c for c in self.library.creators() if c["handle"] == handle), None)
            if not coverage or not any(coverage.get(kind, 0) for kind in ("captions", "speech", "ocr")):
                self.jobs.finish(handle, "failed", detail="Media was saved, but no searchable text is available yet.")
            else:
                detail = "Saved evidence is ready. Coverage is a partial snapshot; speech and OCR availability are shown separately."
                if collected.get("remaining", 0):
                    detail += " Some discovered posts remain unprocessed."
                self.jobs.finish(handle, "ready", detail=detail, coverage=coverage)
        except Exception as exc:
            self.jobs.finish(handle, "failed", detail=clean_error(exc))
        return True
