import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reel_lab.config import load_chat_environment


class ChatConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="instant-chat-config-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / ".env.local"
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write(self, contents):
        self.path.write_text(contents, encoding="utf-8")

    def test_missing_explicit_file_is_harmless(self):
        self.assertEqual(load_chat_environment(self.path), 0)
        self.assertEqual(dict(os.environ), {})

    def test_only_allowlisted_chat_values_are_loaded_and_counted(self):
        self.write("INSTANT_CHAT_BASE_URL=https://model.example/v1\n"
                   "INSTANT_CHAT_MODEL=demo-model\nINSTANT_CHAT_API_KEY=private-test-value\n"
                   "UNRELATED_SETTING=ignored\nPATH=ignored\n")
        self.assertEqual(load_chat_environment(self.path), 3)
        self.assertEqual(dict(os.environ), {"INSTANT_CHAT_BASE_URL": "https://model.example/v1",
            "INSTANT_CHAT_MODEL": "demo-model", "INSTANT_CHAT_API_KEY": "private-test-value"})
        self.assertEqual(load_chat_environment(self.path), 0)

    def test_comments_blank_lines_bom_spacing_and_whole_quotes(self):
        self.write("\ufeff# Local chat\n\n  # Another comment\n"
                   " INSTANT_CHAT_MODEL = 'model/name' \n"
                   'INSTANT_CHAT_BASE_URL="https://model.example/v1"\n'
                   "INSTANT_CHAT_API_KEY = \"private=test#literal\"\n")
        self.assertEqual(load_chat_environment(self.path), 3)
        self.assertEqual(os.environ["INSTANT_CHAT_MODEL"], "model/name")
        self.assertEqual(os.environ["INSTANT_CHAT_API_KEY"], "private=test#literal")

    def test_environment_including_empty_values_takes_precedence(self):
        self.write("INSTANT_CHAT_BASE_URL=https://file.example/v1\n"
                   "INSTANT_CHAT_MODEL=file-model\nINSTANT_CHAT_API_KEY=file-value\n")
        with patch.dict(os.environ, {"INSTANT_CHAT_MODEL": "process-model", "INSTANT_CHAT_API_KEY": ""}):
            self.assertEqual(load_chat_environment(self.path), 1)
            self.assertEqual(os.environ["INSTANT_CHAT_MODEL"], "process-model")
            self.assertEqual(os.environ["INSTANT_CHAT_API_KEY"], "")
            self.assertEqual(os.environ["INSTANT_CHAT_BASE_URL"], "https://file.example/v1")

    def test_empty_values_are_supported(self):
        self.write("INSTANT_CHAT_BASE_URL=\nINSTANT_CHAT_MODEL=''\nINSTANT_CHAT_API_KEY=\"\"\n")
        self.assertEqual(load_chat_environment(self.path), 3)
        self.assertTrue(all(value == "" for value in os.environ.values()))

    def test_values_are_literal_and_never_evaluated_or_interpolated(self):
        self.write("INSTANT_CHAT_API_KEY=$(Write-Output literal); $SECRET_VALUE\n"
                   "INSTANT_CHAT_MODEL=${MODEL_NAME}\\literal\n")
        self.assertEqual(load_chat_environment(self.path), 2)
        self.assertEqual(os.environ["INSTANT_CHAT_API_KEY"], "$(Write-Output literal); $SECRET_VALUE")
        self.assertEqual(os.environ["INSTANT_CHAT_MODEL"], "${MODEL_NAME}\\literal")

    def test_parse_errors_are_atomic_and_do_not_echo_values(self):
        malformed = ("missing_equals_private-value", "export INSTANT_CHAT_API_KEY=private-value",
                     "=private-value", "BAD KEY=private-value", "INSTANT_CHAT_API_KEY='private-value",
                     'INSTANT_CHAT_API_KEY="private-value\' ', 'INSTANT_CHAT_API_KEY="private-value" trailing',
                     'INSTANT_CHAT_API_KEY="private"value"', "INSTANT_CHAT_API_KEY=private\x00value")
        for bad_line in malformed:
            with self.subTest(line=bad_line[:25]):
                self.write("INSTANT_CHAT_MODEL=should-not-load\n" + bad_line + "\n")
                with self.assertRaises(ValueError) as error:
                    load_chat_environment(self.path)
                self.assertEqual(dict(os.environ), {})
                self.assertNotIn("private", str(error.exception))
                self.assertNotIn("should-not-load", str(error.exception))

    def test_duplicate_chat_keys_fail_before_any_environment_changes(self):
        self.write("INSTANT_CHAT_MODEL=first\nINSTANT_CHAT_MODEL=second\n")
        with self.assertRaises(ValueError):
            load_chat_environment(self.path)
        self.assertEqual(dict(os.environ), {})

    def test_valid_unknown_keys_are_ignored_but_malformed_lines_are_rejected(self):
        self.write("OTHER_KEY=literal-value\nINSTANT_CHAT_MODEL=accepted\n")
        self.assertEqual(load_chat_environment(self.path), 1)
        self.assertNotIn("OTHER_KEY", os.environ)
        self.write("OTHER KEY=malformed\nINSTANT_CHAT_API_KEY=not-loaded\n")
        with self.assertRaises(ValueError):
            load_chat_environment(self.path)
        self.assertNotIn("INSTANT_CHAT_API_KEY", os.environ)

    def test_unreadable_encoding_and_file_errors_have_safe_messages(self):
        self.path.write_bytes(b"INSTANT_CHAT_API_KEY=private-value\xff")
        with self.assertRaises(ValueError) as error:
            load_chat_environment(self.path)
        self.assertNotIn("private-value", str(error.exception))
        with patch("reel_lab.config.Path.read_text", side_effect=PermissionError("private-value")):
            with self.assertRaises(ValueError) as error:
                load_chat_environment(self.path)
            self.assertNotIn("private-value", str(error.exception))
        self.assertEqual(dict(os.environ), {})


if __name__ == "__main__":
    unittest.main()
