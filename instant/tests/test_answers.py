import json
import os
import unittest
from unittest.mock import patch

from reel_lab.answers import _local_generate, answer_question


def source(text, post_id="POST1"):
    return {"id": post_id + ":caption:stable", "post_id": post_id,
            "creator": "creator_one", "url": f"https://www.instagram.com/reel/{post_id}/",
            "modality": "caption", "text": text, "start_seconds": None,
            "end_seconds": None, "title": "Source caption", "published_at": None, "score": 1}


class EvidenceLibrary:
    """Return broad candidates so the answer relevance guard is exercised."""
    def __init__(self, sources=None):
        self.sources = sources if sources is not None else [source("Build an emergency fund before a crisis. Save for unexpected expenses.")]
        self.queries = []

    def creators(self):
        return [{"handle": "creator_one", "posts": len(self.sources), "captions": len(self.sources),
                 "speech": 0, "ocr": 0, "duration_seconds": 0, "coverage_note": "Partial sample."}]

    def search(self, query, creator, limit=6):
        self.queries.append((query, creator, limit))
        return self.sources[:limit]


class AnswerRegressionTests(unittest.TestCase):
    def setUp(self):
        self.model_environment = patch.dict(os.environ, {"INSTANT_MODEL": ""})
        self.model_environment.start()
        self.addCleanup(self.model_environment.stop)
        self.history = [{"role": "user", "content": "emergency fund"},
                        {"role": "assistant", "content": "Build an emergency fund. [1]"}]

    def test_unrelated_short_question_does_not_inherit_previous_topic(self):
        library = EvidenceLibrary()
        result = answer_question(library, "Quantum entanglement?", "creator_one", self.history)
        self.assertEqual(library.queries[-1][0], "Quantum entanglement?")
        self.assertEqual(result["grounding_status"], "no_evidence")
        self.assertFalse(result["grounded"])
        self.assertEqual(result["sources"], [])

    def test_what_about_new_topic_requires_its_new_terms(self):
        library = EvidenceLibrary([source("Build an emergency fund before a crisis."),
                                   source("The company is expanding its cloud computing services.", "POST2")])
        result = answer_question(library, "What about quantum computing?", "creator_one", self.history)
        self.assertEqual(library.queries[-1][0], "What about quantum computing?")
        self.assertEqual(result["grounding_status"], "no_evidence")
        self.assertEqual(result["sources"], [])

    def test_explicit_referential_followups_keep_creator_and_previous_topic(self):
        for question in ("Tell me more", "Why is that?", "Explain that.", "Can you explain that in more detail?"):
            with self.subTest(question=question):
                library = EvidenceLibrary()
                result = answer_question(library, question, "@Creator_One", self.history)
                self.assertEqual(library.queries[-1], ("emergency fund", "creator_one", 6))
                self.assertEqual(result["grounding_status"], "source_excerpts")
                self.assertTrue(result["grounded"])
                self.assertIn("emergency fund", result["answer"])

    def test_substantive_tail_does_not_turn_into_referential_followup(self):
        for question in ("Tell me more about quantum computing", "Why does that affect quantum computing?"):
            with self.subTest(question=question):
                library = EvidenceLibrary([source("Build an emergency fund. Cloud computing is also discussed.")])
                result = answer_question(library, question, "creator_one", self.history)
                self.assertEqual(library.queries[-1][0], question)
                self.assertEqual(result["sources"], [])

    def test_late_matching_guidance_is_quoted_with_visible_omissions(self):
        text = ("India waited for a crisis to fix its economy.\n"
                "Are you doing the same with your money?\n👀\n"
                "In 1991, India was running out of foreign exchange.\n"
                "And we often follow the same pattern with personal finances.\n"
                "We build an emergency fund after an emergency.\n"
                "Buy health insurance after a hospital bill.\n"
                "Financial security is built before a crisis.")
        library = EvidenceLibrary([source(text)])
        result = answer_question(library, "emergency fund", "creator_one")
        self.assertIn("We build an emergency fund after an emergency.", result["answer"])
        self.assertNotIn("India waited for a crisis", result["answer"])
        self.assertNotIn("👀", result["answer"])
        self.assertIn("...", result["answer"])
        self.assertEqual(result["sources"][0]["text"], text)
        self.assertIsNone(result["sources"][0]["start_seconds"])

    def test_long_quote_has_explicit_truncation_marker(self):
        library = EvidenceLibrary([source("Emergency fund " + "source words " * 100 + ".")])
        result = answer_question(library, "emergency fund", "creator_one")
        quote = result["answer"].split("“", 1)[1].rsplit("”", 1)[0]
        self.assertLessEqual(len(quote), 850)
        self.assertTrue(quote.endswith(" ..."))

    def test_no_evidence_skips_model_even_when_configured(self):
        with patch.dict(os.environ, {"INSTANT_MODEL": "test-model"}), \
             patch("reel_lab.answers._local_generate") as generate:
            result = answer_question(EvidenceLibrary(), "quantum computing", "creator_one", self.history)
        generate.assert_not_called()
        self.assertEqual(result["grounding_status"], "no_evidence")

    def test_generated_answer_is_explicitly_unverified_despite_valid_citations(self):
        generated = "Build an emergency fund. [1]\n\nSave for unexpected expenses. [1]"
        with patch.dict(os.environ, {"INSTANT_MODEL": "test-model"}), \
             patch("reel_lab.answers._local_generate", return_value=generated):
            result = answer_question(EvidenceLibrary(), "emergency fund", "creator_one")
        self.assertEqual(result["answer"], generated)
        self.assertEqual(result["mode"], "model")
        self.assertEqual(result["grounding_status"], "generated_unverified")
        self.assertTrue(result["grounded"])
        self.assertTrue(any("source support has not been verified" in warning for warning in result["warnings"]))

    def test_invalid_citations_or_uncited_paragraphs_fall_back_to_excerpts(self):
        invalid = ("Build a fund. [0]", "Build a fund. [2]", "Build a fund. [-1]",
                   "Build a fund. [1]\n\nAn uncited second paragraph.",
                   "Uncited heading\n\nBuild a fund. [1]", "No citations.", "")
        for generated in invalid:
            with self.subTest(generated=generated), \
                 patch.dict(os.environ, {"INSTANT_MODEL": "test-model"}), \
                 patch("reel_lab.answers._local_generate", return_value=generated):
                result = answer_question(EvidenceLibrary(), "emergency fund", "creator_one")
                self.assertEqual(result["mode"], "extractive")
                self.assertEqual(result["grounding_status"], "source_excerpts")
                self.assertIn("Build an emergency fund before a crisis.", result["answer"])
                self.assertTrue(result["warnings"])

    def test_local_response_checks_each_paragraph_without_contacting_a_model(self):
        with patch.dict(os.environ, {"INSTANT_MODEL": "test-model", "INSTANT_OLLAMA_URL": "http://127.0.0.1:11434"}), \
             patch("reel_lab.answers.urlopen") as request:
            response = request.return_value.__enter__.return_value
            response.read.return_value = json.dumps({"message": {"content": "Cited statement. [1]\n\nUncited paragraph."}}).encode()
            with self.assertRaises(ValueError):
                _local_generate("emergency fund", EvidenceLibrary().sources, [])
            response.read.return_value = json.dumps({"message": {"content": "Cited statement. [1]\n\nAnother cited statement. [1]"}}).encode()
            self.assertEqual(_local_generate("emergency fund", EvidenceLibrary().sources, []),
                             "Cited statement. [1]\n\nAnother cited statement. [1]")


if __name__ == "__main__":
    unittest.main()
