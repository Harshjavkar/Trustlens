import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from reel_lab.answers import answer_question
from reel_lab.library import Library
from reel_lab.server import create_server


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "source"
        media = self.data / "media" / "POST1"
        media.mkdir(parents=True)
        (media / "video.mp4").write_bytes(b"0123456789abcdef")
        manifest = {"reels": {"POST1": {"status": "downloaded", "caption": "Build an emergency fund before a crisis. Save for unexpected expenses.",
                    "files": [{"path": "media/POST1/video.mp4", "bytes": 16}]}}}
        (self.data / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.library = Library(self.root / "library")
        self.library.import_manifest(self.data / "manifest.json", "creator_one")
        self.model_patch = patch.dict(os.environ, {"INSTANT_MODEL": "", "INSTANT_CHAT_BASE_URL": "",
                                                 "INSTANT_CHAT_MODEL": "", "INSTANT_CHAT_API_KEY": ""})
        self.model_patch.start()
        self.server = create_server(self.library.root, 0, self.data / "media")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.model_patch.stop()
        self.temporary.cleanup()

    def test_answer_cites_actual_caption_without_fake_timestamp(self):
        result = answer_question(self.library, "How should I prepare an emergency fund?", "creator_one")
        self.assertTrue(result["grounded"])
        self.assertEqual(result["mode"], "extractive")
        self.assertIn("[1]", result["answer"])
        self.assertIsNone(result["sources"][0]["start_seconds"])
        self.assertEqual(result["sources"][0]["modality"], "caption")

    def test_absent_evidence_abstains(self):
        result = answer_question(self.library, "Explain lunar geology", "creator_one")
        self.assertFalse(result["grounded"])
        self.assertEqual(result["sources"], [])

    def test_short_followup_uses_previous_user_question(self):
        result = answer_question(self.library, "Tell me more", "creator_one", [{"role": "user", "content": "emergency fund"}])
        self.assertTrue(result["sources"])

    def test_unknown_creator_rejected(self):
        with self.assertRaises(ValueError):
            answer_question(self.library, "emergency fund", "another_creator")

    def test_creator_requests_reuse_shared_ready_and_queued_jobs(self):
        def request(handle):
            data = json.dumps({"handle": handle}).encode()
            with urlopen(Request(self.url + "/api/creators/request", data=data,
                                 headers={"Content-Type": "application/json"})) as response:
                return response.status, json.load(response)
        status, existing = request("@CREATOR_ONE")
        self.assertEqual(status, 200)
        self.assertEqual(existing["creator"]["status"], "ready")
        status, first = request("new_creator")
        self.assertEqual(status, 202)
        _, second = request("https://www.instagram.com/NEW_CREATOR/")
        self.assertEqual(first["creator"]["job_id"], second["creator"]["job_id"])
        with urlopen(self.url + "/api/creators/status?handle=new_creator") as response:
            self.assertEqual(json.load(response)["creator"]["status"], "queued")

    def test_local_model_failure_preserves_source_evidence(self):
        with patch.dict(os.environ, {"INSTANT_MODEL": "configured-model"}), patch("reel_lab.answers._local_generate", side_effect=RuntimeError("offline")):
            result = answer_question(self.library, "emergency fund", "creator_one")
        self.assertEqual(result["mode"], "extractive")
        self.assertTrue(result["sources"])
        self.assertTrue(result["warnings"])

    def test_http_answer_and_media_range(self):
        payload = json.dumps({"question": "emergency fund", "creator": "creator_one"}).encode()
        request = Request(self.url + "/api/ask", data=payload, headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            result = json.load(response)
        self.assertEqual(result["sources"][0]["post_id"], "POST1")
        with urlopen(Request(self.url + "/media/POST1", headers={"Range": "bytes=3-6"})) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers["Content-Range"], "bytes 3-6/16")
            self.assertEqual(response.read(), b"3456")

    def test_chat_unavailable_returns_service_error_without_excerpt_fallback(self):
        payload = json.dumps({"question": "emergency fund", "creator": "creator_one"}).encode()
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(self.url + "/api/chat", data=payload, headers={"Content-Type": "application/json"}))
        self.assertEqual(caught.exception.code, 503)
        with caught.exception as response:
            body = json.load(response)
        self.assertEqual(body["code"], "conversation_unavailable")
        self.assertNotIn("answer", body)

    def test_chat_http_passes_preferences_and_evidence_to_model(self):
        history = [{"role": "user", "content": "Please keep things short and use Hinglish."},
                   {"role": "assistant", "content": "Sure, we can do that."}]
        payload = json.dumps({"question": "And emergency funds?", "creator": "creator_one", "history": history}).encode()
        with patch.dict(os.environ, {"INSTANT_CHAT_BASE_URL": "https://model.example/v1",
                                    "INSTANT_CHAT_MODEL": "test-model", "INSTANT_CHAT_API_KEY": "private-test-key"}), \
                patch("reel_lab.conversation._generate", return_value="Pehle emergency fund samjho. [1]") as generate:
            with urlopen(Request(self.url + "/api/chat", data=payload, headers={"Content-Type": "application/json"})) as response:
                result = json.load(response)
        self.assertEqual(result["mode"], "model")
        self.assertEqual(result["sources"][0]["post_id"], "POST1")
        messages = generate.call_args.args[1]
        self.assertEqual(messages[2:4], history)
        self.assertEqual(json.loads(messages[1]["content"])["creator"], "creator_one")
        self.assertNotIn("private-test-key", json.dumps(result))

    def test_health_exposes_chat_readiness_without_credentials(self):
        with patch.dict(os.environ, {"INSTANT_CHAT_BASE_URL": "https://model.example/v1",
                                    "INSTANT_CHAT_MODEL": "test-model", "INSTANT_CHAT_API_KEY": "private-test-key"}):
            with urlopen(self.url + "/api/health") as response:
                result = json.load(response)
        self.assertEqual(result["conversation"], {"configured": True, "provider": "hosted", "model": "test-model"})
        self.assertNotIn("private-test-key", json.dumps(result))
        self.assertNotIn("model.example", json.dumps(result))

    def test_rejects_cross_origin_and_unsafe_media_path(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(self.url + "/api/creators", headers={"Origin": "https://unrelated.example"}))
        self.assertEqual(caught.exception.code, 403)
        post = self.library.get_post("POST1")
        outside = self.root / "outside.mp4"
        outside.write_bytes(b"private")
        post["media"][0]["path"] = "../outside.mp4"
        (self.library.records_dir / "POST1.json").write_text(json.dumps(post), encoding="utf-8")
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.url + "/media/POST1")
        self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
