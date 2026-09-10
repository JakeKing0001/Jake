"""Test unitari per core/context_summarizer.py: nessuna suite esisteva finora (tests/
test_privacy.py sostituisce ContextSummarizer con uno stub, non ne testa mai il comportamento
vero). urllib.request.urlopen e' sempre mockato.

F1: buco reale corretto in questa sessione (stesso pattern di skills/ask_question.py e altre
skill) - un corpo JSON valido ma non nella forma attesa faceva sollevare un TypeError mai
catturato invece di degradare a None."""
import json
import unittest
from unittest import mock
from urllib import error

from core.context_summarizer import ContextSummarizer


def _fake_json_response(payload) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class ContextSummarizerTests(unittest.TestCase):
    def test_no_turns_returns_none_without_calling_ollama(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            result = ContextSummarizer().summarize([])
        self.assertIsNone(result)
        urlopen.assert_not_called()

    def test_a_successful_summary_is_returned(self):
        turns = [{"role": "user", "text": "che tempo fa"}, {"role": "jake", "text": "sereno"}]
        with mock.patch(
            "urllib.request.urlopen", return_value=_fake_json_response({"message": {"content": "riassunto"}}),
        ):
            result = ContextSummarizer().summarize(turns)
        self.assertEqual(result, "riassunto")

    def test_connection_failure_returns_none(self):
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("boom")):
            result = ContextSummarizer().summarize([{"role": "user", "text": "ciao"}])
        self.assertIsNone(result)

    def test_malformed_json_root_does_not_crash(self):
        """Il buco reale trovato e corretto in questa sessione."""
        for body in (b"null", b"[]", b'{"message": null}'):
            response = mock.MagicMock()
            response.read.return_value = body
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            with mock.patch("urllib.request.urlopen", return_value=response):
                result = ContextSummarizer().summarize([{"role": "user", "text": "ciao"}])
            self.assertIsNone(result, msg=body)


if __name__ == "__main__":
    unittest.main()
