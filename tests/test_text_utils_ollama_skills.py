"""Test unitari per le skill di skills/text_utils.py che delegano a Ollama (ProofreadTextSkill/
SummarizeTextSkill/DetectLanguageSkill, tutte basate su _OllamaTextSkill._complete): nessuna
suite esisteva finora per l'intero file. Le altre skill pure-computazionali dello stesso file
(CountWordsSkill, ConvertCaseSkill, ecc.) non toccano Ollama e restano fuori scopo qui.

F1: buco reale corretto in questa sessione (stesso pattern di skills/ask_question.py e altre
skill) - un corpo JSON valido ma non nella forma attesa faceva sollevare un TypeError mai
catturato invece di degradare a None. Corretto una volta sola in _complete(), condiviso dalle
tre sottoclassi."""
import json
import unittest
from unittest import mock
from urllib import error

from skills.text_utils import DetectLanguageSkill, ProofreadTextSkill, SummarizeTextSkill


def _fake_json_response(payload) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class ProofreadTextTests(unittest.TestCase):
    def test_missing_text_fails(self):
        result = ProofreadTextSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_successful_correction_is_returned(self):
        with mock.patch(
            "urllib.request.urlopen", return_value=_fake_json_response({"message": {"content": "testo corretto"}}),
        ):
            result = ProofreadTextSkill().execute({"text": "testo con erori"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["text"], "testo corretto")

    def test_ollama_failure_reports_ollama_unavailable(self):
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("boom")):
            result = ProofreadTextSkill().execute({"text": "testo"})
        self.assertEqual(result.error, "OLLAMA_UNAVAILABLE")


class SummarizeTextTests(unittest.TestCase):
    def test_missing_text_fails(self):
        result = SummarizeTextSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_successful_summary_is_returned(self):
        with mock.patch(
            "urllib.request.urlopen", return_value=_fake_json_response({"message": {"content": "riassunto"}}),
        ):
            result = SummarizeTextSkill().execute({"text": "un testo lungo"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["summary"], "riassunto")


class DetectLanguageTests(unittest.TestCase):
    def test_missing_text_fails(self):
        result = DetectLanguageSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")


class MalformedResponseRegressionTests(unittest.TestCase):
    """Il buco reale trovato e corretto in questa sessione, verificato su tutte e tre le skill
    condivise (stessa _complete())."""

    def _assert_degrades_gracefully(self, skill, parameters, body: bytes):
        response = mock.MagicMock()
        response.read.return_value = body
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with mock.patch("urllib.request.urlopen", return_value=response):
            result = skill.execute(parameters)
        self.assertEqual(result.error, "OLLAMA_UNAVAILABLE", msg=(type(skill).__name__, body))

    def test_proofread_does_not_crash_on_a_malformed_response(self):
        self._assert_degrades_gracefully(ProofreadTextSkill(), {"text": "x"}, b"null")

    def test_summarize_does_not_crash_on_a_malformed_response(self):
        self._assert_degrades_gracefully(SummarizeTextSkill(), {"text": "x"}, b"[]")

    def test_detect_language_does_not_crash_on_a_malformed_response(self):
        self._assert_degrades_gracefully(DetectLanguageSkill(), {"text": "x"}, b'{"message": null}')


if __name__ == "__main__":
    unittest.main()
