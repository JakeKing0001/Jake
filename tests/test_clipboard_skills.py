"""Test unitari per skills/clipboard.py: nessuna suite esisteva finora. win32clipboard e
urllib.request.urlopen sono sempre mockati.

F1: buco reale trovato e corretto in questa sessione (il primo di 6 file con lo stesso pattern
copiaincollato, insieme a core/vision_provider.py in precedenza) - TranslateClipboardSkill._
translate() non catturava TypeError: un corpo JSON valido ma non nella forma attesa faceva
sollevare un'eccezione mai gestita invece di degradare a None."""
import json
import unittest
from unittest import mock
from urllib import error

from skills.clipboard import ClipboardReadSkill, ClipboardWriteSkill, TranslateClipboardSkill


def _fake_clipboard_module(read_value=None, raise_on_open=False):
    module = mock.MagicMock()
    if raise_on_open:
        module.OpenClipboard.side_effect = Exception("appunti non disponibili")
    module.GetClipboardData.return_value = read_value
    return module


def _fake_json_response(payload) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class ClipboardReadTests(unittest.TestCase):
    def test_reads_the_current_clipboard_text(self):
        module = _fake_clipboard_module(read_value="ciao mondo")
        with mock.patch.dict("sys.modules", {"win32clipboard": module}):
            result = ClipboardReadSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["text"], "ciao mondo")

    def test_empty_clipboard_reports_clipboard_empty(self):
        module = _fake_clipboard_module(read_value="")
        with mock.patch.dict("sys.modules", {"win32clipboard": module}):
            result = ClipboardReadSkill().execute({})
        self.assertEqual(result.error, "CLIPBOARD_EMPTY")

    def test_clipboard_access_failure_reports_clipboard_empty(self):
        module = _fake_clipboard_module(raise_on_open=True)
        with mock.patch.dict("sys.modules", {"win32clipboard": module}):
            result = ClipboardReadSkill().execute({})
        self.assertEqual(result.error, "CLIPBOARD_EMPTY")


class ClipboardWriteTests(unittest.TestCase):
    def test_missing_text_fails(self):
        result = ClipboardWriteSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_writes_the_text_to_the_clipboard(self):
        module = _fake_clipboard_module()
        with mock.patch.dict("sys.modules", {"win32clipboard": module}):
            result = ClipboardWriteSkill().execute({"text": "nuovo contenuto"})
        self.assertTrue(result.success)
        module.SetClipboardData.assert_called_once_with(module.CF_UNICODETEXT, "nuovo contenuto")

    def test_a_write_failure_is_reported_not_raised(self):
        module = _fake_clipboard_module(raise_on_open=True)
        with mock.patch.dict("sys.modules", {"win32clipboard": module}):
            result = ClipboardWriteSkill().execute({"text": "x"})
        self.assertEqual(result.error, "OPERATION_FAILED")


class TranslateClipboardTests(unittest.TestCase):
    def test_missing_target_language_fails(self):
        result = TranslateClipboardSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_empty_clipboard_reports_clipboard_empty(self):
        module = _fake_clipboard_module(read_value="   ")
        with mock.patch.dict("sys.modules", {"win32clipboard": module}):
            result = TranslateClipboardSkill().execute({"target_language": "inglese"})
        self.assertEqual(result.error, "CLIPBOARD_EMPTY")

    def test_a_successful_translation_is_returned(self):
        module = _fake_clipboard_module(read_value="ciao")
        with mock.patch.dict("sys.modules", {"win32clipboard": module}):
            with mock.patch(
                "urllib.request.urlopen", return_value=_fake_json_response({"message": {"content": "hello"}}),
            ):
                result = TranslateClipboardSkill().execute({"target_language": "inglese"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["translation"], "hello")

    def test_ollama_failure_reports_ollama_unavailable(self):
        module = _fake_clipboard_module(read_value="ciao")
        with mock.patch.dict("sys.modules", {"win32clipboard": module}):
            with mock.patch("urllib.request.urlopen", side_effect=error.URLError("boom")):
                result = TranslateClipboardSkill().execute({"target_language": "inglese"})
        self.assertEqual(result.error, "OLLAMA_UNAVAILABLE")

    def test_malformed_json_root_does_not_crash(self):
        """Il buco reale trovato e corretto in questa sessione."""
        module = _fake_clipboard_module(read_value="ciao")
        response = mock.MagicMock()
        response.read.return_value = b"null"
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with mock.patch.dict("sys.modules", {"win32clipboard": module}):
            with mock.patch("urllib.request.urlopen", return_value=response):
                result = TranslateClipboardSkill().execute({"target_language": "inglese"})
        self.assertEqual(result.error, "OLLAMA_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
