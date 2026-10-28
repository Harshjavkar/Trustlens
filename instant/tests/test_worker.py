import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reel_lab.jobs import CreatorJobs
from reel_lab.library import Library
from reel_lab.worker import CreatorWorker


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library = Library(self.root / "library")
        self.jobs = CreatorJobs(self.library.root)
        self.worker = CreatorWorker(self.library, self.jobs, self.root / "creators", delay=0)

    def tearDown(self):
        self.worker.stop()
        self.temporary.cleanup()

    def test_inaccessible_public_discovery_is_visible_and_not_ready(self):
        self.jobs.request("new_creator")
        with patch("reel_lab.worker.discover_links", return_value=({"status": "failed", "stop_reason": "needs_rendered_browser_snapshot"}, None)), patch("reel_lab.worker.run_pipeline") as collect:
            self.assertTrue(self.worker.process_one())
            self.assertFalse(self.worker.process_one())
        self.assertEqual(self.jobs.get("new_creator")["status"], "needs_source_links")
        self.assertFalse(self.library.creators())
        collect.assert_not_called()

    def test_real_indexed_evidence_makes_creator_ready_and_is_reused(self):
        self.jobs.request("new_creator")

        def discovery(args):
            args.output.parent.mkdir(parents=True)
            args.output.write_text("https://www.instagram.com/reel/NEWPOST/\n", encoding="utf-8")
            return {"status": "ok"}, None

        def collect(args):
            video = args.data_dir / "media/NEWPOST/video.mp4"
            video.parent.mkdir(parents=True)
            video.write_bytes(b"existing source bytes")
            manifest = {"reels": {"NEWPOST": {"caption": "Keep an emergency fund.", "files": [{"path": "media/NEWPOST/video.mp4", "bytes": video.stat().st_size}]}}}
            (args.data_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            return {"stop_reason": "selected_complete", "remaining": 0}, None

        with patch("reel_lab.worker.discover_links", side_effect=discovery), patch("reel_lab.worker.run_pipeline", side_effect=collect) as download:
            self.assertTrue(self.worker.process_one())
            ready = self.jobs.request("@NEW_CREATOR")
            self.assertEqual(ready["status"], "ready")
            self.assertFalse(self.worker.process_one())
            self.assertEqual(download.call_count, 1)
        self.assertEqual(ready["coverage"]["captions"], 1)
        self.assertEqual(ready["coverage"]["speech"], 0)
        self.assertEqual(self.library.search("emergency", "new_creator")[0]["post_id"], "NEWPOST")

    def test_rate_limit_stops_without_claiming_readiness(self):
        self.jobs.request("new_creator")
        with patch("reel_lab.worker.discover_links", return_value=({"status": "ok"}, None)), patch("reel_lab.worker.run_pipeline", return_value=({"stop_reason": "rate_limit"}, None)):
            self.worker.process_one()
        self.assertEqual(self.jobs.get("new_creator")["status"], "failed")
        self.assertFalse(self.library.creators())

    def test_failed_worker_error_is_sanitized(self):
        self.jobs.request("new_creator")
        with patch("reel_lab.worker.discover_links", side_effect=RuntimeError("Authorization: private-secret")):
            self.worker.process_one()
        result = self.jobs.get("new_creator")
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("private-secret", result["detail"])


if __name__ == "__main__":
    unittest.main()
