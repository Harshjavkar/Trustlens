import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from reel_lab.transcribe import run_transcribe


def segment(start=0.25, end=1.5, text=" Speech from the creator. "):
    return SimpleNamespace(start=start, end=end, text=text)


def speech_result(segments=None):
    return iter([segment()] if segments is None else segments), SimpleNamespace(
        language="en", language_probability=0.96, duration=2.0,
    )


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.media = self.root / "media"
        self.source = self.media / "post" / "video.mp4"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"local fake media")
        self.output = self.root / "speech"

    def engine(self, result=None):
        engine = MagicMock()
        engine.transcribe.side_effect = lambda *args, **kwargs: speech_result() if result is None else result()
        return engine

    def run_with(self, engine=None, **kwargs):
        with patch("reel_lab.transcribe._load_model", return_value=engine or self.engine()) as load:
            result = run_transcribe(self.media, self.output, **kwargs)
        return result, load

    def saved(self):
        return json.loads((self.output / "post" / "video.mp4.json").read_text(encoding="utf-8"))

    def test_source_linked_timestamped_output_and_summary(self):
        engine = self.engine()
        result, load = self.run_with(engine)
        self.assertEqual((result["processed"], result["failed"], result["total"]), (1, 0, 1))
        load.assert_called_once_with("base", "cpu", "int8", None)
        engine.transcribe.assert_called_once_with(str(self.source.resolve()), language=None)
        saved = self.saved()
        self.assertEqual(saved["schema_version"], 1)
        self.assertEqual(saved["status"], "complete")
        self.assertEqual(saved["language"], "en")
        self.assertEqual(saved["language_probability"], 0.96)
        self.assertEqual(saved["duration_seconds"], 2)
        self.assertEqual(saved["segments"], [{
            "start_seconds": 0.25, "end_seconds": 1.5,
            "text": "Speech from the creator.", "speaker": None,
        }])
        self.assertEqual(saved["source"]["size"], len(b"local fake media"))
        self.assertEqual(len(saved["source"]["sha256"]), 64)
        self.assertIn("unreviewed", saved["warning"])
        self.assertEqual(json.loads((self.output / "summary.json").read_text(encoding="utf-8")), result)

    def test_complete_cache_does_not_initialize_model(self):
        self.run_with()
        with patch("reel_lab.transcribe._load_model", side_effect=AssertionError("must remain unloaded")) as load:
            result = run_transcribe(self.media, self.output)
        self.assertEqual((result["processed"], result["skipped"]), (0, 1))
        load.assert_not_called()

    def test_configuration_change_invalidates_cache(self):
        self.run_with()
        result, load = self.run_with(language="hi")
        self.assertEqual(result["processed"], 1)
        load.assert_called_once()
        self.assertEqual(self.saved()["config"]["language"], "hi")

    def test_content_hash_invalidates_cache_with_same_size_and_mtime(self):
        self.run_with()
        before = self.source.stat()
        self.source.write_bytes(b"other fake media")
        os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.source.stat().st_size, before.st_size)
        result, load = self.run_with()
        self.assertEqual(result["processed"], 1)
        load.assert_called_once()

    def test_empty_transcription_is_explicit_and_retryable(self):
        empty = self.engine(lambda: speech_result([segment(text="   ")]))
        result, _ = self.run_with(empty)
        self.assertEqual((result["processed"], result["no_speech"], result["failed"]), (0, 1, 0))
        self.assertEqual(self.saved()["status"], "no_speech")
        self.assertEqual(self.saved()["segments"], [])
        retried, load = self.run_with()
        self.assertEqual(retried["processed"], 1)
        load.assert_called_once()

    def test_failure_is_sanitized_persisted_and_does_not_stop_other_files(self):
        (self.media / "good.wav").write_bytes(b"fake audio")
        engine = self.engine()

        def recognize(source, **kwargs):
            if source.endswith("video.mp4"):
                def interrupted_generator():
                    yield segment()
                    raise RuntimeError("authorization: secret-token\nfailed at https://host/path?secret=abc")
                return interrupted_generator(), SimpleNamespace(
                    language="en", language_probability=0.96, duration=2.0,
                )
            return speech_result()

        engine.transcribe.side_effect = recognize
        result, _ = self.run_with(engine)
        self.assertEqual((result["processed"], result["failed"], result["total"]), (1, 1, 2))
        saved = self.saved()
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["segments"], [])
        self.assertNotIn("secret", json.dumps(saved))
        self.assertNotIn("https://", json.dumps(result))
        retried, _ = self.run_with()
        self.assertEqual((retried["processed"], retried["skipped"]), (1, 1))

    def test_invalid_timestamps_are_failures(self):
        invalid = [(-0.1, 1.0), (1.0, 0.5), (0.5, float("nan")), (0.5, 3.0)]
        for start, end in invalid:
            with self.subTest(start=start, end=end):
                result, _ = self.run_with(self.engine(lambda: speech_result([segment(start, end)])))
                self.assertEqual((result["processed"], result["failed"]), (0, 1))
                self.assertEqual(self.saved()["status"], "failed")

    def test_broken_partial_or_empty_complete_cache_is_retried(self):
        target = self.output / "post" / "video.mp4.json"
        target.parent.mkdir(parents=True)
        target.write_text('{"status": "complete",', encoding="utf-8")
        result, _ = self.run_with()
        self.assertEqual(result["processed"], 1)
        saved = self.saved()
        saved["segments"] = []
        target.write_text(json.dumps(saved), encoding="utf-8")
        result, _ = self.run_with()
        self.assertEqual(result["processed"], 1)

    def test_local_model_path_is_used_without_network_fallback(self):
        model_dir = self.root / "weights"
        model_dir.mkdir()
        for name in ("config.json", "model.bin", "tokenizer.json"):
            (model_dir / name).write_bytes(b"test placeholder")
        result, load = self.run_with(model_path=model_dir)
        load.assert_called_once_with("base", "cpu", "int8", model_dir.resolve())
        self.assertEqual(result["config"]["model_path"], str(model_dir.resolve()))
        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=MagicMock())}):
            from reel_lab.transcribe import _load_model
            import faster_whisper
            _load_model("base", "cpu", "int8", model_dir)
            faster_whisper.WhisperModel.assert_called_once_with(
                str(model_dir), device="cpu", compute_type="int8", local_files_only=True,
            )

    def test_local_model_without_tokenizer_cannot_trigger_remote_fallback(self):
        model_dir = self.root / "incomplete-weights"
        model_dir.mkdir()
        for name in ("config.json", "model.bin"):
            (model_dir / name).write_bytes(b"test placeholder")
        from reel_lab.transcribe import _load_model
        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=MagicMock())}):
            import faster_whisper
            with self.assertRaisesRegex(ValueError, "tokenizer.json"):
                _load_model("base", "cpu", "int8", model_dir)
            faster_whisper.WhisperModel.assert_not_called()

    def test_model_initialization_failure_writes_each_pending_failure(self):
        (self.media / "another.mp3").write_bytes(b"audio")
        with patch("reel_lab.transcribe._load_model", side_effect=RuntimeError("missing engine")) as load:
            result = run_transcribe(self.media, self.output)
        load.assert_called_once()
        self.assertEqual((result["processed"], result["failed"]), (0, 2))
        self.assertEqual(self.saved()["status"], "failed")
        self.assertTrue((self.output / "another.mp3.json").is_file())

    def test_no_media_does_not_load_model(self):
        self.source.unlink()
        (self.media / "notes.txt").write_text("not media", encoding="utf-8")
        with patch("reel_lab.transcribe._load_model", side_effect=AssertionError("must remain unloaded")) as load:
            result = run_transcribe(self.media, self.output)
        self.assertEqual(result["total"], 0)
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
