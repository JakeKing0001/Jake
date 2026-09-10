"""Test unitari per skills/translate_text.py: nessuna suite esisteva finora.

F1: buco reale corretto in questa sessione (stesso pattern di skills/ask_question.py e altre
skill) - un corpo JSON valido ma non nella forma attesa faceva sollevare un TypeError mai
catturato invece di degradare a None."""
import json
import unittest
from unittest import mock
from urllib import error

from skills.translate_text import TranslateTextSkill


def _fake_json_response(payload) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class MissingParametersTests(unittest.TestCase):
    def test_missing_text_or_target_language_fails(self):
        self.assertEqual(TranslateTextSkill().execute({"target_language": "inglese"}).error, "MISSING_PARAMETERS")
        self.assertEqual(TranslateTextSkill().execute({"text": "ciao"}).error, "MISSING_PARAMETERS")


class TranslationTests(unittest.TestCase):
    def test_a_successful_translation_is_returned(self):
        with mock.patch(
            "urllib.request.urlopen", return_value=_fake_json_response({"message": {"content": "hello"}}),
        ):
            result = TranslateTextSkill().execute({"text": "ciao", "target_language": "inglese"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["translation"], "hello")
        self.assertEqual(result.data["target_language"], "inglese")

    def test_ollama_failure_reports_ollama_unavailable(self):
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("boom")):
            result = TranslateTextSkill().execute({"text": "ciao", "target_language": "inglese"})
        self.assertEqual(result.error, "OLLAMA_UNAVAILABLE")

    def test_malformed_json_root_does_not_crash(self):
        """Il buco reale trovato e corretto in questa sessione."""
        response = mock.MagicMock()
        response.read.return_value = b"42"
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with mock.patch("urllib.request.urlopen", return_value=response):
            result = TranslateTextSkill().execute({"text": "ciao", "target_language": "inglese"})
        self.assertEqual(result.error, "OLLAMA_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
