import json
import os
import tempfile
import unittest
from pathlib import Path

from reel_lab.library import Library


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "source"
        self.data.mkdir()
        self.library = Library(self.root / "evidence")

    def tearDown(self):
        self.temporary.cleanup()

    def manifest(self, code="AAA", caption="Mutual funds diversify investments.", **fields):
        media = self.data / "media" / code / "video.mp4"
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(b"fake local media " + code.encode())
        record = {"status": "downloaded", "caption": caption,
                  "files": [{"path": f"media/{code}/video.mp4", "bytes": media.stat().st_size}], **fields}
        path = self.data / f"{code}-manifest.json"
        path.write_text(json.dumps({"reels": {code: record}}), encoding="utf-8")
        return path, media

    def extracted(self, folder, code, segments, **fields):
        path = self.root / folder / code / "video.mp4.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": "complete", "segments": segments, **fields}), encoding="utf-8")
        return path.parent.parent

    def test_creator_isolation_and_required_scope(self):
        alpha, _ = self.manifest("AAA", "Mutual funds diversify investments.")
        beta, _ = self.manifest("BBB", "Mutual funds can charge fees.")
        self.library.import_manifest(alpha, "@Alpha")
        self.library.import_manifest(beta, "beta")
        self.assertEqual({row["post_id"] for row in self.library.search("mutual funds", "alpha")}, {"AAA"})
        self.assertEqual({row["post_id"] for row in self.library.search("mutual funds", "BETA")}, {"BBB"})
        self.assertEqual([post["id"] for post in self.library.posts("alpha")], ["AAA"])
        self.assertEqual([creator["handle"] for creator in self.library.creators()], ["alpha", "beta"])
        with self.assertRaises(ValueError):
            self.library.search("funds", "")
        with self.assertRaises(TypeError):
            self.library.search("funds")
        with self.assertRaises(ValueError):
            self.library.import_manifest(alpha, "beta")

    def test_modalities_timestamps_and_unknown_publication_stay_distinct(self):
        manifest, media = self.manifest(caption="Caption fund details.")
        speech = self.extracted("transcripts", "AAA", [
            {"start_seconds": 1.25, "end_seconds": 3.75, "text": "Speech fund explanation.", "speaker": "unknown"}],
            source={"size": media.stat().st_size, "mtime_ns": media.stat().st_mtime_ns},
            language="en", config={"model": "small", "secret_token": "DO_NOT_COPY"})
        ocr = self.extracted("ocr", "AAA", [
            {"first_seen_seconds": 2, "last_seen_seconds": 2.5, "text": "On screen fund fees."}])
        summary = self.library.import_manifest(manifest, "alpha", speech, ocr)
        self.assertEqual((summary["captions"], summary["speech"], summary["ocr"]), (1, 1, 1))
        post = self.library.get_post("AAA")
        self.assertIsNone(post["published_at"])
        self.assertIsNone(post["duration_seconds"])
        by_type = {item["modality"]: item for item in self.library.search("fund", "alpha")}
        self.assertEqual(set(by_type), {"caption", "speech", "ocr"})
        self.assertIsNone(by_type["caption"]["start_seconds"])
        self.assertIsNone(by_type["caption"]["end_seconds"])
        self.assertEqual((by_type["speech"]["start_seconds"], by_type["speech"]["end_seconds"]), (1.25, 3.75))
        self.assertEqual((by_type["ocr"]["start_seconds"], by_type["ocr"]["end_seconds"]), (2, 2.5))
        self.assertNotIn("DO_NOT_COPY", (self.library.records_dir / "AAA.json").read_text())
        note = (self.library.notes_dir / "AAA.md").read_text(encoding="utf-8")
        self.assertIn("Published: Unknown", note)
        self.assertIn("sample observations", note)
        self.assertIn("Post caption", note)
        self.assertEqual(self.library.creators()[0]["speech"], 1)

    def test_unchanged_import_preserves_record_note_and_segment_identity(self):
        manifest, _ = self.manifest()
        first = self.library.import_manifest(manifest, "alpha")
        record = self.library.records_dir / "AAA.json"
        note = self.library.notes_dir / "AAA.md"
        # Set distinctive mtimes so a rewrite is detected without sleeps.
        os.utime(record, ns=(1234567890000000000, 1234567890000000000))
        os.utime(note, ns=(1234567890000000000, 1234567890000000000))
        identity = self.library.get_post("AAA")["segments"][0]["id"]
        second = self.library.import_manifest(manifest, "alpha")
        self.assertEqual((first["imported"], second["unchanged"]), (1, 1))
        self.assertEqual(record.stat().st_mtime_ns, 1234567890000000000)
        self.assertEqual(note.stat().st_mtime_ns, 1234567890000000000)
        self.assertEqual(self.library.get_post("AAA")["segments"][0]["id"], identity)

    def test_missing_evidence_does_not_become_transcript_or_search_hit(self):
        manifest, media = self.manifest(caption=None)
        media.unlink()
        self.library.import_manifest(manifest, "alpha")
        post = self.library.get_post("AAA")
        self.assertEqual(post["segments"], [])
        self.assertEqual(post["processing"], {"media": "missing", "caption": "missing", "speech": "missing", "ocr": "missing"})
        self.assertEqual(self.library.search("funds", "alpha"), [])
        self.assertEqual(self.library.search("what does the creator say", "alpha"), [])
        self.assertEqual(self.library.search("quasars", "alpha"), [])

    def test_rebuild_recovers_deleted_index_and_notes_from_records(self):
        manifest, _ = self.manifest()
        self.library.import_manifest(manifest, "alpha")
        before = self.library.search("funds", "alpha")
        self.library.database_path.unlink()
        (self.library.notes_dir / "AAA.md").unlink()
        rebuilt = self.library.rebuild()
        self.assertEqual((rebuilt["posts"], rebuilt["segments"]), (1, 1))
        self.assertTrue((self.library.notes_dir / "AAA.md").is_file())
        self.assertEqual(self.library.search("funds", "alpha"), before)
        self.library.database_path.unlink()
        self.assertEqual(self.library.search("funds", "alpha"), before)

    def test_manifest_sidecar_and_verification_handle_legacy_paths(self):
        manifest, media = self.manifest(caption=None, timestamp=1700000000)
        media.with_suffix(".txt").write_text("Legacy caption about pension funds.", encoding="utf-8")
        (self.data / "verification.json").write_text(json.dumps([{
            "file": r"C:\old\machine\media\AAA\video.mp4", "duration_seconds": 12.5}]), encoding="utf-8")
        self.library.import_manifest(manifest, "alpha")
        post = self.library.get_post("AAA")
        self.assertEqual(post["caption"], "Legacy caption about pension funds.")
        self.assertEqual(post["duration_seconds"], 12.5)
        self.assertEqual(post["published_at"], "2023-11-14T22:13:20+00:00")
        self.assertFalse(Path(post["media"][0]["path"]).is_absolute())
        self.assertEqual((self.library.root / post["media"][0]["path"]).resolve(), media.resolve())
        self.assertEqual(len(post["media"][0]["sha256"]), 64)

    def test_unsafe_shortcodes_and_cross_post_media_paths_are_rejected(self):
        for identifier in ("../AAA", "..", "AAA/BBB", "AAA\\BBB"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.library.get_post(identifier)
        manifest, _ = self.manifest()
        payload = json.loads(manifest.read_text())
        payload["reels"]["AAA"]["files"][0]["path"] = "../../outside.mp4"
        manifest.write_text(json.dumps(payload))
        with self.assertRaises(ValueError):
            self.library.import_manifest(manifest, "alpha")
        self.assertEqual(list(self.library.records_dir.iterdir()), [])

    def test_invalid_timestamps_and_stale_extraction_are_not_indexed(self):
        manifest, _ = self.manifest()
        speech = self.extracted("transcripts", "AAA", [
            {"start_seconds": -1, "end_seconds": 2, "text": "Invalid negative."},
            {"start_seconds": 4, "end_seconds": 2, "text": "Invalid reversed."},
            {"text": "No timestamp."}])
        ocr = self.extracted("ocr", "AAA", [
            {"first_seen_seconds": 0, "last_seen_seconds": 1, "text": "Stale output."}], source={"size": 999999})
        summary = self.library.import_manifest(manifest, "alpha", speech, ocr)
        post = self.library.get_post("AAA")
        self.assertEqual(post["processing"]["speech"], "partial")
        self.assertEqual(post["processing"]["ocr"], "stale")
        self.assertTrue(summary["warnings"])
        self.assertEqual(self.library.search("invalid stale timestamp", "alpha"), [])

    def test_reimport_without_optional_directory_preserves_evidence_until_media_changes(self):
        manifest, media = self.manifest()
        speech = self.extracted("transcripts", "AAA", [{"start_seconds": 0, "end_seconds": 1, "text": "Pension speech."}])
        self.library.import_manifest(manifest, "alpha", speech)
        self.library.import_manifest(manifest, "alpha")
        self.assertEqual(len(self.library.search("pension", "alpha")), 1)
        media.write_bytes(b"changed source media")
        self.library.import_manifest(manifest, "alpha")
        self.assertEqual(self.library.get_post("AAA")["processing"]["speech"], "stale")
        self.assertEqual(self.library.search("pension", "alpha"), [])

    def test_quoted_fts_operators_and_record_edits_cannot_leak_creator_scope(self):
        manifest, _ = self.manifest()
        self.library.import_manifest(manifest, "alpha")
        self.assertEqual(self.library.search('" OR * NOT (quasars)', "alpha"), [])
        record_path = self.library.records_dir / "AAA.json"
        post = json.loads(record_path.read_text(encoding="utf-8"))
        post["segments"][0]["text"] = "Nebula observations."
        record_path.write_text(json.dumps(post), encoding="utf-8")
        self.assertEqual(self.library.search("funds", "alpha"), [])
        self.assertEqual(len(self.library.search("nebula", "alpha")), 1)


if __name__ == "__main__":
    unittest.main()
