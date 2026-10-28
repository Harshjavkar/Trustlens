import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reel_lab.links import discover_links, extract_reel_links


class LinkDiscoveryTests(unittest.TestCase):
    def args(self, root, **overrides):
        values = dict(profile="financewithsharan", output=root / "queue.txt", limit=20,
                      html=None, import_json=None)
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_anchors_only_filter_host_owner_posts_and_preserve_order(self):
        html = '''<script>{"url":"https://www.instagram.com/reel/SECRET/"}</script>
            <a href="/financewithsharan/reel/BBB/?igsh=tracking">b</a>
            <a href="https://www.instagram.com/reel/AAA/">a</a>
            <a href="/reel/BBB/">duplicate</a>
            <a href="https://instagram.com.evil.invalid/reel/EVIL/">bad</a>
            <a href="https://user@instagram.com/reel/EVIL/">bad</a>
            <a href="https://instagram.com:443/reel/EVIL/">bad</a>
            <a href="/somebodyelse/reel/NOPE/">wrong owner</a>
            <a href="/p/POST/">post</a>'''
        self.assertEqual(extract_reel_links(html, "financewithsharan"), [
            "https://www.instagram.com/reel/BBB/", "https://www.instagram.com/reel/AAA/"])

    def test_import_keeps_provenance_and_limits_canonical_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            imported = root / "observed.json"
            imported.write_text(json.dumps({"urls": ["/financewithsharan/reel/ONE/", "/reel/TWO/"],
                "source_url": "https://www.instagram.com/financewithsharan/reels/?tracking=value",
                "collected_at": "2026-10-28T10:00:00+05:30"}), encoding="utf-8")
            args = self.args(root, import_json=imported, limit=1)
            with patch("reel_lab.links._public_html") as fetch:
                summary, report = discover_links(args)
            fetch.assert_not_called()
            self.assertEqual(summary["discovered"], 1)
            self.assertEqual(args.output.read_text(), "https://www.instagram.com/reel/ONE/\n")
            metadata = json.loads(report.read_text())
            self.assertEqual(metadata["source_url"], "https://www.instagram.com/financewithsharan/reels/")
            self.assertIn("not a full profile", metadata["completeness"])

    def test_access_restriction_stops_and_preserves_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = self.args(root)
            args.output.write_text("existing queue\n")
            with patch("reel_lab.links._public_html", side_effect=RuntimeError("429 Too many requests")) as fetch:
                summary, _ = discover_links(args)
            fetch.assert_called_once_with("financewithsharan")
            self.assertEqual(summary["stop_reason"], "access_restricted")
            self.assertEqual(args.output.read_text(), "existing queue\n")

    def test_empty_public_html_does_not_clear_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = self.args(root)
            args.output.write_text("existing queue\n")
            with patch("reel_lab.links._public_html", return_value='<script>"/reel/HIDDEN/"</script>'):
                summary, _ = discover_links(args)
            self.assertEqual(summary["discovered"], 0)
            self.assertEqual(summary["stop_reason"], "needs_rendered_browser_snapshot")
            self.assertEqual(args.output.read_text(), "existing queue\n")

    def test_offline_html_uses_no_network(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            html = root / "profile.html"
            html.write_text('<a href="/reel/VISIBLE/">reel</a>')
            with patch("reel_lab.links._public_html") as fetch:
                summary, _ = discover_links(self.args(root, html=html))
            fetch.assert_not_called()
            self.assertEqual(summary["status"], "ok")


if __name__ == "__main__":
    unittest.main()
