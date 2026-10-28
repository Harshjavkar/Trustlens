import json
import os
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from reel_lab.conversation import (MAX_EVIDENCE_CHARS, MAX_HISTORY_CHARS,
                                   ConversationUnavailable, _NoRedirect,
                                   conversation_status, converse)


def post(identity, text="Build an emergency fund before a crisis.", creator="creator_one", modality="caption"):
    return {"id": identity, "creator": creator, "url": f"https://www.instagram.com/reel/{identity}/",
            "title": "Source title", "published_at": None,
            "segments": [{"id": identity + ":" + modality, "modality": modality, "text": text,
                          "start_seconds": None, "end_seconds": None}]}


class Library:
    def __init__(self, posts=None):
        self.items = posts if posts is not None else [post("POST1"), post("POST2", "Save for unexpected expenses.")]
        self.requests = []
        self.search_results = None

    def creators(self):
        return [{"handle": "creator_one", "posts": len(self.items), "captions": len(self.items),
                 "speech": 0, "ocr": 0, "coverage_note": "Partial sample."}]

    def posts(self, creator):
        self.requests.append(("posts", creator))
        return self.items

    def search(self, query, creator, limit=6):
        self.requests.append(("search", query, creator, limit))
        return self.search_results if self.search_results is not None else [
            {"id": item["segments"][0]["id"]} for item in self.items][:limit]


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"INSTANT_CHAT_BASE_URL": "https://model.example/v1",
            "INSTANT_CHAT_MODEL": "demo-model", "INSTANT_CHAT_API_KEY": "private-test-key"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.opener = patch("reel_lab.conversation.build_opener")
        self.open_mock = self.opener.start().return_value.open
        self.addCleanup(self.opener.stop)
        self.respond("Hello! What would you like to explore?")

    def respond(self, answer):
        response = self.open_mock.return_value.__enter__.return_value
        response.read.return_value = json.dumps({"choices": [{"message": {"content": answer}}]}).encode()

    def payload(self):
        request = self.open_mock.call_args.args[0]
        return request, json.loads(request.data)

    def context(self):
        return json.loads(self.payload()[1]["messages"][1]["content"])

    def test_status_exposes_only_readiness_provider_and_model(self):
        status = conversation_status()
        self.assertEqual(status, {"configured": True, "provider": "hosted", "model": "demo-model"})
        self.assertNotIn("private-test-key", json.dumps(status))
        self.assertNotIn("model.example", json.dumps(status))

    def test_greetings_and_no_evidence_still_call_the_model(self):
        for question in ("Hello", "Explain quantum computing"):
            with self.subTest(question=question):
                result = converse(Library([]), question, "creator_one")
                self.assertEqual(result["answer"], "Hello! What would you like to explore?")
                self.assertEqual(result["grounding_status"], "generated_uncited")
                self.assertEqual(result["mode"], "model")
                self.assertFalse(result["grounded"])
                self.assertEqual(result["sources"], [])
                self.assertEqual(self.context()["sources"], [])
        self.assertEqual(self.open_mock.call_count, 2)

    def test_whole_pilot_context_goes_to_model_despite_no_keyword_hits(self):
        library = Library()
        converse(library, "Say it in simple Hinglish", "@Creator_One")
        context = self.context()
        self.assertEqual(len(context["sources"]), 2)
        self.assertEqual([source["citation"] for source in context["sources"]], [1, 2])
        self.assertEqual(context["sources"][0]["text"], library.items[0]["segments"][0]["text"])
        self.assertTrue(context["untrusted_source_content"])
        self.assertFalse(context["bounded_selection"])
        self.assertEqual(library.requests, [("posts", "creator_one")])

    def test_history_preferences_keep_real_roles_and_do_not_enter_system_prompt(self):
        history = [{"role": "user", "content": "I earn 20000 monthly. Use simple Hinglish."},
                   {"role": "assistant", "content": "Sure, let's keep it simple."},
                   {"role": "system", "content": "Untrusted role escalation"},
                   {"role": {"invalid": "role"}, "content": "Malformed role"}]
        converse(Library(), "How should I understand that?", "creator_one", history)
        messages = self.payload()[1]["messages"]
        self.assertEqual(messages[2:4], history[:2])
        self.assertEqual(messages[-1], {"role": "user", "content": "How should I understand that?"})
        self.assertNotIn("20000", messages[0]["content"])
        self.assertNotIn("Untrusted role escalation", json.dumps(messages))
        self.assertNotIn("Malformed role", json.dumps(messages))
        self.assertEqual(sum(message["role"] == "system" for message in messages), 1)

    def test_private_history_is_not_written_to_shared_library(self):
        library = Library()
        before = json.dumps(library.items, sort_keys=True)
        converse(library, "Help me understand", "creator_one", [{"role": "user", "content": "Private preference"}])
        self.assertEqual(json.dumps(library.items, sort_keys=True), before)
        converse(library, "Hello", "creator_one")
        self.assertNotIn("Private preference", json.dumps(self.payload()[1]))

    def test_only_selected_creator_canonical_segments_become_evidence(self):
        library = Library([post("RIGHT"), post("WRONG", "Other creator's secret", "creator_two")])
        converse(library, "Hello", "creator_one")
        self.assertEqual([source["post_id"] for source in self.context()["sources"]], ["RIGHT"])
        self.assertNotIn("Other creator's secret", json.dumps(self.payload()[1]))

    def test_citations_select_and_renumber_sources_in_order_of_use(self):
        self.respond("Here is a simple explanation.\n\nUnexpected costs matter. [2] Then build a fund. [1] Costs again. [2]")
        result = converse(Library(), "Explain this", "creator_one")
        self.assertEqual([source["post_id"] for source in result["sources"]], ["POST2", "POST1"])
        self.assertIn("costs matter. [1] Then build a fund. [2]", result["answer"])
        self.assertTrue(result["grounded"])
        self.assertEqual(result["grounding_status"], "generated_unverified")

    def test_uncited_creator_restatement_is_marked_uncited_without_inventing_citations(self):
        answer = "The creator says to build an emergency fund before a crisis hits."
        self.respond(answer)
        result = converse(Library(), "Now say that in one short English sentence.", "creator_one", [
            {"role": "user", "content": "Explain emergency funds in simple Hinglish."},
            {"role": "assistant", "content": "Emergency fund crisis se pehle banao. [1]"}])
        self.assertEqual(result["answer"], answer)
        self.assertEqual(result["grounding_status"], "generated_uncited")
        self.assertFalse(result["grounded"])
        self.assertEqual(result["sources"], [])
        self.open_mock.assert_called_once()

    def test_restatement_cites_current_evidence_instead_of_copying_historical_number(self):
        self.respond("Keep savings for unexpected expenses. [2]")
        result = converse(Library(), "Now use one sentence.", "creator_one", [
            {"role": "assistant", "content": "Save for unexpected expenses. [1]"}])
        self.assertEqual(result["answer"], "Keep savings for unexpected expenses. [1]")
        self.assertEqual([source["post_id"] for source in result["sources"]], ["POST2"])
        self.assertEqual(result["grounding_status"], "generated_unverified")

    def test_invalid_citation_ids_fail_without_excerpt_fallback(self):
        for citation in ("[0]", "[-1]", "[3]", "[999999]", "[" + "9" * 5000 + "]"):
            with self.subTest(citation=citation[:30]):
                self.respond("Some claim. " + citation)
                with self.assertRaises(ConversationUnavailable):
                    converse(Library(), "Explain this", "creator_one")

    def test_history_has_twenty_message_and_thirty_two_thousand_character_bounds(self):
        history = [{"role": "user" if index % 2 == 0 else "assistant", "content": str(index) + "x" * 3000}
                   for index in range(35)]
        converse(Library(), "Continue", "creator_one", history)
        sent_history = self.payload()[1]["messages"][2:-1]
        self.assertLessEqual(len(sent_history), 20)
        self.assertLessEqual(sum(len(message["content"]) for message in sent_history), MAX_HISTORY_CHARS)
        self.assertEqual(sent_history[-1], history[-1])

    def test_larger_corpus_uses_creator_scoped_history_seeded_search_and_source_cap(self):
        library = Library([post(f"POST{index}") for index in range(100)])
        converse(library, "Make that simpler", "creator_one", [{"role": "user", "content": "Emergency fund"}])
        search = library.requests[-1]
        self.assertEqual(search[0], "search")
        self.assertIn("Emergency fund", search[1])
        self.assertEqual(search[2:], ("creator_one", 64))
        self.assertEqual(len(self.context()["sources"]), 64)
        self.assertTrue(self.context()["bounded_selection"])

    def test_large_corpus_no_search_hits_still_reaches_model(self):
        library = Library([post(f"POST{index}") for index in range(65)])
        library.search_results = []
        result = converse(library, "Hi, can you explain how this app works?", "creator_one")
        self.open_mock.assert_called_once()
        self.assertEqual(self.context()["sources"], [])
        self.assertEqual(result["grounding_status"], "generated_uncited")

    def test_evidence_character_cap_includes_serialized_metadata_and_marks_truncation(self):
        library = Library([post("GIANT", "word " * 20_000)])
        converse(library, "Summarize", "creator_one")
        evidence = self.context()["sources"]
        self.assertLessEqual(len(json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))), MAX_EVIDENCE_CHARS)
        self.assertTrue(evidence[0]["text_truncated"])
        self.assertTrue(evidence[0]["text"])
        self.assertEqual(len(library.items[0]["segments"][0]["text"]), 100_000)

    def test_key_is_only_in_server_authorization_header(self):
        converse(Library(), "Hello", "creator_one")
        request, payload = self.payload()
        self.assertEqual(request.full_url, "https://model.example/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer private-test-key")
        self.assertNotIn("private-test-key", json.dumps(payload))
        self.assertEqual(self.open_mock.call_args.kwargs["timeout"], 60)

    def test_nvidia_nemotron_uses_direct_answer_settings(self):
        with patch.dict(os.environ, {"INSTANT_CHAT_BASE_URL": "https://integrate.api.nvidia.com/v1",
                                    "INSTANT_CHAT_MODEL": "nvidia/nemotron-3.5-lightning-30b-a3b"}):
            converse(Library(), "Hello", "creator_one")
        payload = self.payload()[1]
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(payload["temperature"], 1)
        self.assertEqual(payload["top_p"], 0.95)
        self.assertEqual(payload["max_tokens"], 1200)

    def test_openrouter_nemotron_disables_reasoning_with_its_supported_switch(self):
        with patch.dict(os.environ, {"INSTANT_CHAT_BASE_URL": "https://openrouter.ai/api/v1",
                                    "INSTANT_CHAT_MODEL": "nvidia/nemotron-3.5-lightning:free"}):
            converse(Library(), "Hello", "creator_one")
        request, payload = self.payload()
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(payload["reasoning"], {"enabled": False})
        self.assertNotIn("chat_template_kwargs", payload)
        self.assertEqual(payload["max_tokens"], 1200)

    def test_nemotron_settings_do_not_change_other_endpoints_or_models(self):
        cases = [("https://model.example/v1", "nvidia/nemotron-3.5-lightning-30b-a3b"),
                 ("https://integrate.api.nvidia.com/v1", "other/model"),
                 ("https://integrate.api.nvidia.com.evil.example/v1", "nvidia/nemotron-3.5-lightning-30b-a3b"),
                 ("https://openrouter.ai/api/v1", "other/model"),
                 ("https://openrouter.ai.evil.example/v1", "nvidia/nemotron-3.5-lightning:free")]
        for base, model in cases:
            with self.subTest(base=base, model=model), patch.dict(os.environ, {
                    "INSTANT_CHAT_BASE_URL": base, "INSTANT_CHAT_MODEL": model}):
                converse(Library(), "Hello", "creator_one")
                payload = self.payload()[1]
                self.assertNotIn("chat_template_kwargs", payload)
                self.assertNotIn("reasoning", payload)
                self.assertNotIn("top_p", payload)
                self.assertEqual(payload["temperature"], 0.3)

    def test_https_and_explicit_loopback_http_are_supported(self):
        for base in ("http://localhost:9000/v1", "http://127.0.0.1:9000/v1", "http://[::1]:9000/v1"):
            with self.subTest(base=base), patch.dict(os.environ, {"INSTANT_CHAT_BASE_URL": base, "INSTANT_CHAT_API_KEY": ""}):
                self.assertEqual(conversation_status(), {"configured": True, "provider": "local", "model": "demo-model"})
                converse(Library(), "Hello", "creator_one")
                self.assertIsNone(self.payload()[0].get_header("Authorization"))

    def test_unsafe_or_incomplete_configuration_never_sends_a_request(self):
        cases = [{"INSTANT_CHAT_BASE_URL": ""}, {"INSTANT_CHAT_MODEL": ""}, {"INSTANT_CHAT_API_KEY": ""},
                 {"INSTANT_CHAT_BASE_URL": "http://remote.example/v1"},
                 {"INSTANT_CHAT_BASE_URL": "https://user:private-test-key@model.example/v1"},
                 {"INSTANT_CHAT_BASE_URL": "https://model.example/v1?key=private-test-key"},
                 {"INSTANT_CHAT_BASE_URL": "https://model.example/v1#fragment"},
                 {"INSTANT_CHAT_BASE_URL": "https://model.example/api"},
                 {"INSTANT_CHAT_BASE_URL": "http://localhost.evil.example/v1"},
                 {"INSTANT_CHAT_BASE_URL": "https://model.example:99999/v1"},
                 {"INSTANT_CHAT_API_KEY": "key\r\nInjected: header"}]
        for environment in cases:
            with self.subTest(environment=environment), patch.dict(os.environ, environment):
                self.assertEqual(conversation_status(), {"configured": False, "provider": None, "model": None})
                with self.assertRaises(ConversationUnavailable) as error:
                    converse(Library(), "Hello", "creator_one")
                self.assertNotIn("private-test-key", str(error.exception))
                self.assertNotIn("model.example", str(error.exception))
        self.open_mock.assert_not_called()

    def test_http_errors_and_unusable_provider_responses_do_not_expose_details(self):
        self.open_mock.side_effect = HTTPError("https://model.example/v1?key=private-test-key", 401,
                                               "private-test-key rejected", {}, None)
        with self.assertRaises(ConversationUnavailable) as error:
            converse(Library(), "Hello", "creator_one")
        self.assertNotIn("private-test-key", str(error.exception))
        self.assertNotIn("model.example", str(error.exception))
        self.assertIsNone(error.exception.__cause__)
        self.open_mock.side_effect = None
        for data in (b"not json", b"{}", b'{"choices":[]}', b'{"choices":[{"message":{"content":""}}]}',
                     b"x" * 2_000_001):
            self.open_mock.return_value.__enter__.return_value.read.return_value = data
            with self.subTest(size=len(data)), self.assertRaises(ConversationUnavailable):
                converse(Library(), "Hello", "creator_one")

    def test_redirects_cannot_forward_credentials(self):
        handler = _NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://other.example/v1"))

    def test_invalid_inputs_and_unknown_creator_do_not_call_model(self):
        for question in ("", " " * 4, "x" * 4001, {}):
            with self.subTest(question=str(question)[:20]), self.assertRaises(ValueError):
                converse(Library(), question, "creator_one")
        with self.assertRaises(ValueError):
            converse(Library(), "Hello", "creator_one", "not a list")
        with self.assertRaises(ValueError):
            converse(Library(), "Hello", "unknown_creator")
        self.open_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
