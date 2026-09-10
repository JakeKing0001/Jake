"""Test unitari per skills/summarize_clipboard.py: nessuna suite esisteva finora. win32clipboard
e urllib.request.urlopen sono sempre mockati.

F1: buco reale corretto in questa sessione (stesso pattern di skills/ask_question.py e altre
skill) - un corpo JSON valido ma non nella forma attesa faceva sollevare un TypeError mai
catturato invece di degradare a None."""
import json
import unittest
from unittest import mock
from urllib import error

from skills.summarize_clipboard import SummarizeClipboardSkill


def _fake_json_response(payload) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _with_clipboard_text(text):
    win32clipboard = mock.MagicMock()
    win32clipboard.GetClipboardData.return_value = text
    return mock.patch.dict("sys.modules", {"win32clipboard": win32clipboard}), win32clipboard


class ClipboardStateTests(unittest.TestCase):
    def test_empty_clipboard_reports_clipboard_empty(self):
        patcher, _ = _with_clipboard_text("")
        with patcher:
            result = SummarizeClipboardSkill().execute({})
        self.assertEqual(result.error, "CLIPBOARD_EMPTY")

    def test_clipboard_access_failure_reports_clipboard_empty(self):
        win32clipboard = mock.MagicMock()
        win32clipboard.OpenClipboard.side_effect = Exception("nessun testo")
        with mock.patch.dict("sys.modules", {"win32clipboard": win32clipboard}):
            result = SummarizeClipboardSkill().execute({})
        self.assertEqual(result.error, "CLIPBOARD_EMPTY")


class SummarizeTests(unittest.TestCase):
    def test_a_successful_summary_is_returned(self):
        patcher, _ = _with_clipboard_text("un testo lungo da riassumere")
        with patcher:
            with mock.patch(
                "urllib.request.urlopen", return_value=_fake_json_response({"message": {"content": "riassunto"}}),
            ):
                result = SummarizeClipboardSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["summary"], "riassunto")

    def test_ollama_failure_reports_ollama_unavailable(self):
        patcher, _ = _with_clipboard_text("testo")
        with patcher:
            with mock.patch("urllib.request.urlopen", side_effect=error.URLError("boom")):
                result = SummarizeClipboardSkill().execute({})
        self.assertEqual(result.error, "OLLAMA_UNAVAILABLE")

    def test_malformed_json_root_does_not_crash(self):
        """Il buco reale trovato e corretto in questa sessione."""
        patcher, _ = _with_clipboard_text("testo")
        response = mock.MagicMock()
        response.read.return_value = b"[]"
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with patcher:
            with mock.patch("urllib.request.urlopen", return_value=response):
                result = SummarizeClipboardSkill().execute({})
        self.assertEqual(result.error, "OLLAMA_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
