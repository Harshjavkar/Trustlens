import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from reel_lab.bootstrap import BUNDLED_RECORDS, bootstrap_demo
from reel_lab.library import Library


def snapshot(root):
    return {path.relative_to(root).as_posix(): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


class DemoBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="instant-bootstrap-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "data" / "library"

    def test_empty_checkout_gets_searchable_caption_library_without_downloads(self):
        with patch("reel_lab.collector.download_ytdlp", side_effect=AssertionError("No downloads")):
            result = bootstrap_demo(self.output)
        self.assertTrue(result["initialized"])
        self.assertEqual((result["posts"], result["captions"], result["speech"], result["ocr"]), (11, 11, 0, 0))
        library = Library(self.output)
        self.assertEqual([creator["handle"] for creator in library.creators()], ["financewithsharan"])
        matches = library.search("emergency fund", "financewithsharan")
        self.assertIn("De_6_GyMQke", [source["post_id"] for source in matches])
        self.assertTrue((self.output / "notes" / "De_6_GyMQke.md").is_file())
        self.assertEqual(list(self.output.rglob("*.mp4")), [])
        self.assertEqual(list(self.output.rglob("*.bin")), [])
        self.assertEqual(list(self.output.rglob(".env*")), [])
        for record in library.posts("financewithsharan"):
            self.assertEqual(record["media"], [])
            self.assertNotIn("extractions", record)
            self.assertEqual(record["processing"]["media"], "not_retained")
            self.assertEqual(record["url"], f"https://www.instagram.com/reel/{record['id']}/")
        self.assertFalse(any(path.name.startswith("instant-demo-bootstrap-") for path in self.output.parent.iterdir()))

    def test_repeat_preserves_all_records_notes_and_index_bytes_and_mtimes(self):
        bootstrap_demo(self.output)
        before = snapshot(self.output)
        result = bootstrap_demo(self.output)
        self.assertFalse(result["initialized"])
        self.assertTrue(result["preserved_existing"])
        self.assertEqual(result["posts"], 11)
        self.assertEqual(snapshot(self.output), before)

    def test_foreign_content_and_existing_creator_records_are_never_replaced_or_merged(self):
        library = Library(self.output)
        path = library.records_dir / "FOREIGN.json"
        record = json.loads(next(BUNDLED_RECORDS.glob("*.json")).read_text(encoding="utf-8"))
        record.update(id="FOREIGN", creator="another_creator", caption="User-owned text")
        record["segments"] = []
        path.write_text(json.dumps(record), encoding="utf-8")
        (self.output / "important.txt").write_text("User file", encoding="utf-8")
        before = snapshot(self.output)
        result = bootstrap_demo(self.output, seed_records=self.root / "missing-seed")
        self.assertTrue(result["preserved_existing"])
        self.assertEqual(snapshot(self.output), before)
        self.assertEqual([path.name for path in library.records_dir.iterdir()], ["FOREIGN.json"])

    def test_edited_demo_record_is_preserved_on_repeat(self):
        bootstrap_demo(self.output)
        path = self.output / "records" / "De_6_GyMQke.json"
        post = json.loads(path.read_text(encoding="utf-8"))
        post["title"] = "User correction must stay"
        path.write_text(json.dumps(post), encoding="utf-8")
        before = snapshot(self.output)
        result = bootstrap_demo(self.output)
        self.assertTrue(result["preserved_existing"])
        self.assertEqual(snapshot(self.output), before)

    def test_precreated_empty_library_directories_can_be_initialized(self):
        Library(self.output)
        result = bootstrap_demo(self.output)
        self.assertTrue(result["initialized"])
        self.assertEqual(len(list((self.output / "records").glob("*.json"))), 11)

    def test_invalid_seed_and_index_failure_leave_destination_untouched(self):
        seed = self.root / "bad-seed"
        seed.mkdir()
        (seed / "BAD.json").write_text('{"id":"DIFFERENT"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            bootstrap_demo(self.output, seed_records=seed)
        self.assertFalse(self.output.exists())
        with patch("reel_lab.bootstrap.Library.rebuild", side_effect=RuntimeError("FTS5 unavailable")):
            with self.assertRaises(RuntimeError):
                bootstrap_demo(self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.output.parent.iterdir()), [])

    def test_library_created_while_staging_is_preserved(self):
        rebuild = Library.rebuild
        def concurrent_library(staged):
            result = rebuild(staged)
            self.output.mkdir(parents=True)
            (self.output / "new-user-content.txt").write_text("Keep this", encoding="utf-8")
            return result
        with patch("reel_lab.bootstrap.Library.rebuild", new=concurrent_library):
            result = bootstrap_demo(self.output)
        self.assertTrue(result["preserved_existing"])
        self.assertEqual((self.output / "new-user-content.txt").read_text(encoding="utf-8"), "Keep this")
        self.assertFalse((self.output / "records").exists())

    def test_fresh_source_copy_bootstraps_using_standard_library_without_site_packages(self):
        checkout = self.root / "clean-checkout"
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source / "reel_lab", checkout / "reel_lab", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        result = subprocess.run([sys.executable, "-S", "-B", "-m", "reel_lab", "bootstrap-demo"],
                                cwd=checkout, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["posts"], 11)
        self.assertTrue((checkout / "data" / "library" / "library.sqlite3").is_file())
        self.assertFalse((checkout / "data" / "live-first-test").exists())
        self.assertFalse((checkout / ".env.local").exists())

    def test_bundled_records_are_declared_package_data_and_contain_only_portable_evidence(self):
        source = Path(__file__).resolve().parents[1]
        config = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("demo_seed/records/*.json", config["tool"]["setuptools"]["package-data"]["reel_lab"])
        files = list(BUNDLED_RECORDS.glob("*.json"))
        self.assertEqual(len(files), 11)
        forbidden = {"path", "output", "extractions", "cookies_file", "api_key", "model_path", "access_token"}
        def inspect(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden & set(value))
                for child in value.values():
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)
        for path in files:
            inspect(json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
