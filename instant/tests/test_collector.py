import argparse
import json
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from reel_lab.collector import canonical_reel_url, clean_error, download_ytdlp, read_urls, run_fetch


class CollectionTests(unittest.TestCase):
    def test_sensitive_headers_are_fully_redacted(self):
        for message in ("Authorization: Bearer EXAMPLE_TOKEN", "Cookie: first=EXAMPLE_ONE; second=EXAMPLE_TWO"):
            self.assertNotIn("EXAMPLE", clean_error(RuntimeError(message)))

    def test_portable_fetch_uses_official_binary_and_drops_signed_urls(self):
        args = argparse.Namespace(cookies_file=None)
        result = argparse.Namespace(returncode=0, stdout=json.dumps({"description": "caption",
            "duration": 12, "url": "https://cdn.invalid/?secret=value"}), stderr="")
        with patch.dict(sys.modules, {"yt_dlp": None}), patch("pathlib.Path.is_file", return_value=True), \
             patch("reel_lab.collector.subprocess.run", return_value=result) as runner:
            metadata = download_ytdlp(args, "https://www.instagram.com/reel/AAA/", Path("media/AAA"))
        self.assertEqual(metadata["caption"], "caption")
        self.assertNotIn("url", metadata)
        self.assertIn("--ignore-config", runner.call_args.args[0])
        self.assertEqual(runner.call_args.kwargs["timeout"], 90)

    def test_external_and_malformed_links_rejected_before_network(self):
        for value in ("https://instagram.com.evil.test/reel/ABC/", "http://instagram.com/reel/A/",
                      "https://user@instagram.com/reel/A/", "https://instagram.com:443/reel/A/", "https://instagram.com/stories/a/"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_reel_url(value)
        self.assertEqual(canonical_reel_url("https://www.instagram.com/p/ABC_123/?igsh=tracking")[0],
                         "https://www.instagram.com/reel/ABC_123/")

    def test_resume_redownloads_missing_files_but_skips_complete_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            urls = root / "urls.txt"
            urls.write_text("https://www.instagram.com/reel/AAA/\nhttps://www.instagram.com/reel/BBB/\n", encoding="utf-8")
            args = argparse.Namespace(urls=urls, profile=None, backend="yt-dlp", limit=2,
                                      data_dir=root / "data", max_minutes=1, delay=0,
                                      session_file=None, username=None, cookies_file=None)
            def fake_download(args, url, destination):
                (destination / "video.mp4").write_bytes(b"test-video")
                return {"caption": "test"}
            with patch("reel_lab.collector.download_ytdlp", side_effect=fake_download) as downloader:
                first, _ = run_fetch(args)
                self.assertEqual(first["downloaded"], 2)
                second, _ = run_fetch(args)
                self.assertEqual(second["cached"], 2)
                self.assertEqual(len(second["cached_ids"]), 2)
                self.assertEqual(downloader.call_count, 2)
                (args.data_dir / "media/AAA/video.mp4").unlink()
                third, _ = run_fetch(args)
                self.assertEqual((third["downloaded"], third["cached"]), (1, 1))

    def test_access_failure_stops_batch_and_persists_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            urls = root / "urls.txt"
            urls.write_text("https://www.instagram.com/reel/AAA/\nhttps://www.instagram.com/reel/BBB/", encoding="utf-8")
            args = argparse.Namespace(urls=urls, profile=None, backend="yt-dlp", limit=2,
                                      data_dir=root / "data", max_minutes=1, delay=0,
                                      session_file=None, username=None, cookies_file=None)
            with patch("reel_lab.collector.download_ytdlp", side_effect=RuntimeError("429 Too Many Requests")) as downloader:
                summary, report = run_fetch(args)
            self.assertEqual(downloader.call_count, 1)
            self.assertEqual(summary["stop_reason"], "access_or_rate_limit")
            self.assertEqual(summary["downloaded"], 0)
            self.assertEqual(json.loads(report.read_text())["failed"], 1)


if __name__ == "__main__":
    unittest.main()
