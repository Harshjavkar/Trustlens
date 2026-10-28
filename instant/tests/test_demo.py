import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from reel_lab.demo import export_demo
from reel_lab.library import Library


def snapshot(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


class DemoExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = Library(self.root / "original-library")
        self.output = self.root / "hosted-demo"
        self.data = self.root / "original-data"
        self._import("AAA", "alpha", "Build an emergency fund before a crisis.")
        self._import("BBB", "beta", "A separate creator discusses pension planning.")

    def tearDown(self):
        self.temporary.cleanup()

    def _import(self, code, creator, caption, with_extractions=False):
        folder = self.data / "media" / code
        folder.mkdir(parents=True, exist_ok=True)
        video = folder / "video.mp4"
        video.write_bytes(b"original-media-" + code.encode())
        manifest = self.data / f"{code}-manifest.json"
        manifest.write_text(json.dumps({"reels": {code: {"status": "downloaded", "caption": caption,
            "duration_seconds": 20.5, "timestamp": 1700000000,
            "files": [{"path": f"media/{code}/video.mp4", "bytes": video.stat().st_size}]}}}), encoding="utf-8")
        transcripts, ocr = None, None
        if with_extractions:
            transcripts, ocr = self.root / "transcripts", self.root / "ocr"
            for directory, segments in (
                (transcripts, [{"start_seconds": 1.25, "end_seconds": 3.75, "text": "Emergency savings protect against surprise bills.", "speaker": "guest"}]),
                (ocr, [{"first_seen_seconds": 4, "last_seen_seconds": 4.5, "text": "Save for unexpected expenses."}])):
                path = directory / code / "video.mp4.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"status": "complete", "source": {"size": video.stat().st_size,
                    "mtime_ns": video.stat().st_mtime_ns}, "segments": segments}), encoding="utf-8")
        self.source.import_manifest(manifest, creator, transcripts, ocr)

    def test_export_contains_only_selected_creator_and_search_works_without_media(self):
        private_model = self.source.root / ".venv" / "models" / "private-model.bin"
        private_model.parent.mkdir(parents=True)
        private_model.write_bytes(b"private model must stay here")
        result = export_demo(self.source, "@Alpha", self.output)
        exported = Library(self.output)
        self.assertEqual(result["posts"], 1)
        self.assertFalse(result["media_retained"])
        self.assertEqual([item["handle"] for item in exported.creators()], ["alpha"])
        self.assertEqual([item["id"] for item in exported.posts("alpha")], ["AAA"])
        self.assertEqual(exported.search("emergency fund", "alpha")[0]["post_id"], "AAA")
        self.assertEqual(exported.search("pension", "beta"), [])
        self.assertIsNone(exported.get_post("BBB"))
        self.assertEqual(list(self.output.rglob("*.mp4")), [])
        self.assertEqual(list(self.output.rglob("*.bin")), [])
        self.assertFalse((self.output / ".venv").exists())
        self.assertTrue((self.output / "notes" / "AAA.md").is_file())
        post = exported.get_post("AAA")
        self.assertEqual(post["media"], [])
        self.assertEqual(post["processing"]["media"], "not_retained")
        self.assertEqual(post["source_media"], [{"sha256": self.source.get_post("AAA")["media"][0]["sha256"],
                                                "bytes": self.source.get_post("AAA")["media"][0]["bytes"]}])

    def test_real_modalities_timestamps_speaker_and_publication_are_preserved(self):
        self._import("AAA", "alpha", "Emergency fund source caption.", with_extractions=True)
        export_demo(self.source, "alpha", self.output)
        exported = Library(self.output).get_post("AAA")
        original = self.source.get_post("AAA")
        self.assertEqual(exported["segments"], original["segments"])
        self.assertEqual(exported["published_at"], original["published_at"])
        self.assertEqual(exported["duration_seconds"], 20.5)
        self.assertEqual(exported["processing"]["speech"], "complete")
        self.assertEqual(exported["processing"]["ocr"], "complete")
        speech = next(segment for segment in exported["segments"] if segment["modality"] == "speech")
        self.assertEqual((speech["start_seconds"], speech["end_seconds"], speech["speaker"]), (1.25, 3.75, "guest"))
        self.assertEqual(len(Library(self.output).search("unexpected", "alpha")), 1)

    def test_allowlist_strips_local_paths_and_arbitrary_secret_metadata(self):
        path = self.source.records_dir / "AAA.json"
        post = json.loads(path.read_text(encoding="utf-8"))
        private = "NEVER_EXPORT_PRIVATE_SENTINEL"
        post.update(api_key=private, cookies_file="C:/private/cookies.txt", model_path="C:/private/model.bin")
        post["extractions"] = {"speech": {"output": str(self.root / "private-transcript.json"),
            "config": {"model_path": "C:/private/model.bin", "access_token": private}}}
        post["segments"][0]["local_path"] = str(self.root / "private-source")
        post["media"][0]["private_metadata"] = private
        path.write_text(json.dumps(post), encoding="utf-8")
        export_demo(self.source, "alpha", self.output)
        for file in self.output.rglob("*"):
            if file.is_file():
                content = file.read_bytes()
                self.assertNotIn(private.encode(), content)
                self.assertNotIn(b"C:/private", content)
                self.assertNotIn(str(self.root).encode(), content)
                self.assertNotIn(b"private-transcript", content)
                self.assertNotIn(b"model_path", content)
        exported = Library(self.output).get_post("AAA")
        self.assertNotIn("extractions", exported)
        self.assertNotIn("local_path", exported["segments"][0])

    def test_original_records_media_and_manifests_are_unchanged(self):
        before_library, before_data = snapshot(self.source.root), snapshot(self.data)
        export_demo(self.source, "alpha", self.output)
        self.assertEqual(snapshot(self.source.root), before_library)
        self.assertEqual(snapshot(self.data), before_data)

    def test_repeated_matching_export_is_unchanged_and_can_refresh_new_text(self):
        first = export_demo(self.source, "alpha", self.output)
        before = snapshot(self.output)
        second = export_demo(self.source, "alpha", self.output)
        self.assertFalse(first["unchanged"])
        self.assertTrue(second["unchanged"])
        self.assertEqual(snapshot(self.output), before)
        self._import("AAA", "alpha", "Emergency funds cover surprise medical expenses.")
        refreshed = export_demo(self.source, "alpha", self.output)
        self.assertFalse(refreshed["unchanged"])
        self.assertIn("surprise medical", Library(self.output).get_post("AAA")["caption"])

    def test_existing_foreign_library_and_modified_exports_are_not_overwritten(self):
        foreign = Library(self.output)
        sentinel = self.output / "important.txt"
        sentinel.write_text("Do not replace this folder.", encoding="utf-8")
        before = snapshot(self.output)
        with self.assertRaises(ValueError):
            export_demo(self.source, "alpha", self.output)
        self.assertEqual(snapshot(self.output), before)
        own_output = self.root / "owned-demo"
        export_demo(self.source, "alpha", own_output)
        note = own_output / "notes" / "AAA.md"
        note.write_text("User edits.", encoding="utf-8")
        before = snapshot(own_output)
        with self.assertRaises(ValueError):
            export_demo(self.source, "alpha", own_output)
        self.assertEqual(snapshot(own_output), before)

    def test_source_root_and_nested_record_or_note_outputs_are_rejected(self):
        for output in (self.source.root, self.source.records_dir / "demo", self.source.notes_dir / "demo"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                export_demo(self.source, "alpha", output)
        self.assertFalse((self.source.records_dir / "demo").exists())
        self.assertFalse((self.source.notes_dir / "demo").exists())

    def test_unsafe_record_ids_and_different_creator_refresh_are_rejected(self):
        path = self.source.records_dir / "AAA.json"
        post = json.loads(path.read_text(encoding="utf-8"))
        post["segments"][0]["id"] = "../../outside"
        path.write_text(json.dumps(post), encoding="utf-8")
        with self.assertRaises(ValueError):
            export_demo(self.source, "alpha", self.output)
        self.assertFalse(self.output.exists())
        post["segments"][0]["id"] = "AAA:caption:restored"
        path.write_text(json.dumps(post), encoding="utf-8")
        export_demo(self.source, "beta", self.output)
        with self.assertRaises(ValueError):
            export_demo(self.source, "alpha", self.output)

    def test_marker_metadata_edits_are_rejected_instead_of_retained(self):
        export_demo(self.source, "alpha", self.output)
        marker = self.output / ".instant-demo-export.json"
        metadata = json.loads(marker.read_text(encoding="utf-8"))
        metadata["local_path"] = "C:/private/credentials.txt"
        marker.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaises(ValueError):
            export_demo(self.source, "alpha", self.output)


if __name__ == "__main__":
    unittest.main()
