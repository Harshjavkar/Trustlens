import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reel_lab.pipeline import classify_error, run_pipeline


class PipelineTests(unittest.TestCase):
    def args(self, root, codes=("AAA", "BBB", "CCC"), **kwargs):
        urls = root / "urls.txt"
        urls.write_text("".join(f"https://www.instagram.com/reel/{code}/\n" for code in codes), encoding="utf-8")
        values = dict(urls=urls, data_dir=root / "data", limit=100,
                      max_minutes=10, delay=0, cookies_file=None)
        values.update(kwargs)
        return argparse.Namespace(**values)

    @staticmethod
    def succeeds(args, url, destination):
        (destination / "video.mp4").write_bytes(b"fake-complete-video")
        return {"caption": "Example", "url": "https://cdn.example/?secret=omit"}

    def manifest(self, args):
        return json.loads((args.data_dir / "manifest.json").read_text(encoding="utf-8"))

    def test_failed_url_is_queued_without_automatic_second_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(Path(folder))
            def download(call_args, url, destination):
                if "/BBB/" in url:
                    raise RuntimeError("Temporary extractor error")
                return self.succeeds(call_args, url, destination)
            with patch("reel_lab.pipeline.download_ytdlp", side_effect=download) as downloader:
                summary, report = run_pipeline(args)
            self.assertEqual(downloader.call_count, 3)
            self.assertEqual((summary["downloaded"], summary["remaining"], summary["attempted"]), (2, 1, 3))
            self.assertFalse(summary["complete"])
            self.assertEqual(json.loads(report.read_text())["backend"], "yt-dlp")
            state = self.manifest(args)
            self.assertEqual([item["status"] for item in state["reels"]["BBB"]["attempts"]], ["failed"])
            self.assertNotIn("cdn.example", json.dumps(state))
            self.assertIn("/BBB/", (args.data_dir / "queues/unresolved.txt").read_text())
            self.assertIn("/BBB/", (args.data_dir / "queues/failed.txt").read_text())

    def test_real_rate_limit_stops_and_preserves_unattempted(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(Path(folder))
            with patch("reel_lab.pipeline.download_ytdlp", side_effect=RuntimeError("HTTP 429 Too Many Requests")) as downloader:
                summary, _ = run_pipeline(args)
            self.assertEqual(downloader.call_count, 1)
            self.assertEqual(summary["stop_reason"], "rate_limit")
            self.assertFalse(summary["complete"])
            self.assertEqual((summary["remaining"], summary["pending"], summary["failed"]), (3, 2, 1))
            self.assertEqual(len((args.data_dir / "queues/unresolved.txt").read_text().splitlines()), 3)
            self.assertEqual(self.manifest(args)["reels"]["BBB"]["attempts"], [])

    def test_checkpoint_stops_without_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(Path(folder))
            with patch("reel_lab.pipeline.download_ytdlp", side_effect=RuntimeError("checkpoint required")) as downloader:
                summary, _ = run_pipeline(args)
            self.assertEqual(downloader.call_count, 1)
            self.assertEqual(summary["stop_reason"], "access_restricted")

    def test_ambiguous_extractor_failure_is_not_automatically_retried(self):
        self.assertEqual(classify_error("rate-limit reached or login required"), "download_error")
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(Path(folder), codes=("AAA",))
            with patch("reel_lab.pipeline.download_ytdlp", side_effect=RuntimeError("Login required")) as downloader:
                summary, _ = run_pipeline(args)
            self.assertEqual(downloader.call_count, 1)
            self.assertEqual(summary["downloaded"], 0)
            self.assertFalse(summary["complete"])

    def test_dedupe_cache_and_retry_missing_or_changed_size(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(Path(folder), codes=("AAA", "AAA", "BBB"))
            with patch("reel_lab.pipeline.download_ytdlp", side_effect=self.succeeds) as downloader:
                initial, _ = run_pipeline(args)
                cached, _ = run_pipeline(args)
                self.assertEqual((initial["downloaded"], cached["cached"]), (2, 2))
                self.assertEqual(downloader.call_count, 2)
                (args.data_dir / "media/AAA/video.mp4").unlink()
                (args.data_dir / "media/BBB/video.mp4").write_bytes(b"changed-size")
                retried, _ = run_pipeline(args)
            self.assertEqual((retried["downloaded"], retried["cached"]), (2, 0))
            self.assertEqual(len(self.manifest(args)["reels"]["AAA"]["attempts"]), 2)
            self.assertTrue(list((args.data_dir / "rejected/BBB").rglob("video.mp4")))

    def test_attempt_history_survives_failed_run_then_success(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(Path(folder), codes=("AAA",))
            with patch("reel_lab.pipeline.download_ytdlp", side_effect=RuntimeError("extractor failed")):
                failed, _ = run_pipeline(args)
            self.assertFalse(failed["complete"])
            with patch("reel_lab.pipeline.download_ytdlp", side_effect=self.succeeds):
                success, _ = run_pipeline(args)
            self.assertTrue(success["complete"])
            history = self.manifest(args)["reels"]["AAA"]["attempts"]
            self.assertEqual([item["status"] for item in history], ["failed", "downloaded"])

    def test_legacy_manifest_cache_preserves_backend_and_history(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(Path(folder), codes=("AAA",))
            media = args.data_dir / "media/AAA/video.mp4"
            media.parent.mkdir(parents=True)
            media.write_bytes(b"existing")
            original = {"status": "downloaded", "backend": "legacy collector", "attempts": [{"stage": 1, "backend": "legacy collector"}],
                        "files": [{"path": "media/AAA/video.mp4", "bytes": 8}]}
            (args.data_dir / "manifest.json").write_text(json.dumps({"reels": {"AAA": original}}))
            with patch("reel_lab.pipeline.download_ytdlp") as downloader:
                summary, _ = run_pipeline(args)
            downloader.assert_not_called()
            self.assertEqual((summary["cached"], summary["attempted"]), (1, 0))
            record = self.manifest(args)["reels"]["AAA"]
            self.assertEqual(record["backend"], original["backend"])
            self.assertEqual(record["attempts"], original["attempts"])

    def test_limit_leaves_other_urls_in_unresolved_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(Path(folder), limit=1)
            with patch("reel_lab.pipeline.download_ytdlp", side_effect=self.succeeds):
                summary, _ = run_pipeline(args)
            self.assertTrue(summary["complete"])
            self.assertFalse(summary["all_input_complete"])
            self.assertEqual((summary["remaining"], summary["remaining_selected"]), (2, 0))
            self.assertEqual(len((args.data_dir / "queues/unresolved.txt").read_text().splitlines()), 2)

    def test_empty_url_input_is_rejected_before_downloader(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(Path(folder), codes=())
            with patch("reel_lab.pipeline.download_ytdlp") as downloader:
                with self.assertRaises(ValueError):
                    run_pipeline(args)
            downloader.assert_not_called()

    def test_bytes_excludes_cached_media_outside_run_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(Path(folder), codes=("AAA", "BBB"))
            with patch("reel_lab.pipeline.download_ytdlp", side_effect=self.succeeds):
                first, _ = run_pipeline(args)
            args.limit = 1
            with patch("reel_lab.pipeline.download_ytdlp") as downloader:
                limited, _ = run_pipeline(args)
            downloader.assert_not_called()
            self.assertEqual(limited["cached"], 1)
            self.assertEqual(limited["bytes"], len(b"fake-complete-video"))
            self.assertEqual(first["bytes"], limited["bytes"] * 2)

    def test_elapsed_budget_does_not_drop_pending_items(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(Path(folder), max_minutes=0.000001)
            with patch("reel_lab.pipeline.download_ytdlp") as downloader, \
                 patch("reel_lab.pipeline.time.monotonic", side_effect=[0, 1, 1]):
                summary, _ = run_pipeline(args)
            downloader.assert_not_called()
            self.assertEqual(summary["stop_reason"], "time_budget")
            self.assertFalse(summary["complete"])
            self.assertEqual(summary["pending"], 3)

    def test_wrong_shortcode_cache_path_does_not_count(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(Path(folder), codes=("AAA",))
            other = args.data_dir / "media/OTHER/video.mp4"
            other.parent.mkdir(parents=True)
            other.write_bytes(b"other")
            (args.data_dir / "manifest.json").write_text(json.dumps({"reels": {"AAA": {
                "status": "downloaded", "files": [{"path": "media/OTHER/video.mp4", "bytes": 5}]}}}))
            with patch("reel_lab.pipeline.download_ytdlp", side_effect=self.succeeds) as downloader:
                summary, _ = run_pipeline(args)
            self.assertEqual(downloader.call_count, 1)
            self.assertEqual(summary["cached"], 0)


if __name__ == "__main__":
    unittest.main()
