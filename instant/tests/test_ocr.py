import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reel_lab.ocr import merge_samples, normalize_text, run_ocr, should_sample


class FakeCapture:
    def __init__(self, opened=True):
        self.opened, self.index, self.released = opened, 0, False

    def isOpened(self):
        return self.opened

    def get(self, _):
        return 5.0

    def read(self):
        index = self.index
        self.index += 1
        return (True, index) if index < 10 else (False, None)

    def release(self):
        self.released = True


class FakeCV2:
    CAP_PROP_FPS = 5

    def __init__(self, opened=True):
        self.capture = FakeCapture(opened)

    def VideoCapture(self, _):
        return self.capture


def fake_engine(frame):
    text = {0: "Hello World", 3: "hello  world", 5: "Next", 8: ""}[frame]
    return ([[None, text, 0.95]] if text else None), []


class OCRTests(unittest.TestCase):
    def test_comparison_preserves_words_and_ignores_case_and_whitespace(self):
        self.assertEqual(normalize_text(" Hello\n WORLD\t"), "hello world")
        self.assertNotEqual(normalize_text("5%"), normalize_text("50%"))

    def test_reappearing_text_is_retained_after_blank_or_different_text(self):
        result = merge_samples([(0, "Hi"), (0.5, " HI "), (1, ""), (1.5, "Hi"), (2, "Bye"), (2.5, "Hi")])
        self.assertEqual([entry["text"] for entry in result], ["Hi", "Hi", "Bye", "Hi"])
        self.assertEqual(result[0]["last_seen_seconds"], 0.5)

    def test_fractional_video_rate_and_sampling_above_native_rate(self):
        selected = [i for i in range(61) if should_sample(i, 29.97, 2)]
        self.assertEqual(selected, [0, 15, 30, 45, 60])
        self.assertTrue(all(should_sample(i, 24, 60) for i in range(48)))
        with self.assertRaises(ValueError):
            should_sample(0, 0, 2)

    def test_outputs_and_cache_are_local_and_configuration_sensitive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "media" / "account" / "clip.mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"fake video")
            output = root / "ocr"
            backend = FakeCV2()
            with patch("reel_lab.ocr._dependencies", return_value=(backend, fake_engine)):
                result = run_ocr(root / "media", output, 2)
            self.assertEqual((result["processed"], result["failed"]), (1, 0))
            self.assertTrue(backend.capture.released)
            saved = json.loads((output / "account" / "clip.mp4.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["sampled_frames"], 4)
            self.assertEqual([item["text"] for item in saved["segments"]], ["Hello World", "Next"])
            with patch("reel_lab.ocr._dependencies", side_effect=AssertionError("Cached run loaded OCR")):
                self.assertEqual(run_ocr(root / "media", output, 2)["skipped"], 1)
            # Changing the requested sampling rate must not reuse old text.
            with patch("reel_lab.ocr._dependencies", side_effect=RuntimeError("configuration changed")):
                with self.assertRaisesRegex(RuntimeError, "configuration changed"):
                    run_ocr(root / "media", output, 1)
            source.write_bytes(b"changed fake video")
            with patch("reel_lab.ocr._dependencies", side_effect=RuntimeError("source changed")):
                with self.assertRaisesRegex(RuntimeError, "source changed"):
                    run_ocr(root / "media", output, 2)

    def test_unreadable_video_is_reported_and_capture_released(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "bad.mp4").write_bytes(b"bad")
            backend = FakeCV2(opened=False)
            with patch("reel_lab.ocr._dependencies", return_value=(backend, fake_engine)):
                result = run_ocr(root, root / "output", 2)
            self.assertEqual((result["processed"], result["failed"]), (0, 1))
            self.assertIn("could not be opened", result["errors"][0]["error"])
            self.assertTrue(backend.capture.released)


if __name__ == "__main__":
    unittest.main()
