"""Test unitari per skills/app_utils.py: nessuna suite esisteva finora, nessun bug trovato.
ListRecentFilesSkill usa una cartella VERA su disco temporaneo (mai i file recenti reali
dell'utente). winreg/win32clipboard sono importati DENTRO le funzioni (import locale, non a
livello di modulo): per queste due basta mock.patch.dict("sys.modules", ...), perche' l'import
avviene di nuovo a ogni chiamata e legge sys.modules al momento dell'esecuzione (diverso dal caso
di skills/system_maintenance.py, dove "import winreg" e' a livello di modulo e gia' legato prima
che il test possa intervenire)."""
import os
import time
import unittest
from pathlib import Path
from unittest import mock

from skills.app_utils import EmptyClipboardSkill, ListRecentFilesSkill, OpenIncognitoWindowSkill


class ListRecentFilesTests(unittest.TestCase):
    def test_missing_recent_folder_reports_not_found(self):
        with mock.patch.dict(os.environ, {"APPDATA": "C:\\percorso\\inesistente\\davvero"}):
            result = ListRecentFilesSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_empty_recent_folder_reports_not_found(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Microsoft" / "Windows" / "Recent").mkdir(parents=True)
            with mock.patch.dict(os.environ, {"APPDATA": tmp}):
                result = ListRecentFilesSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_lists_files_most_recent_first(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            recent = Path(tmp) / "Microsoft" / "Windows" / "Recent"
            recent.mkdir(parents=True)
            older = recent / "vecchio.lnk"
            newer = recent / "nuovo.lnk"
            older.write_text("x")
            time.sleep(0.01)
            newer.write_text("x")
            with mock.patch.dict(os.environ, {"APPDATA": tmp}):
                result = ListRecentFilesSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["files"][0], "nuovo")
        self.assertEqual(result.data["files"][1], "vecchio")


class OpenIncognitoWindowTests(unittest.TestCase):
    def _fake_winreg(self, prog_id: str):
        fake = mock.MagicMock()
        fake.HKEY_CURRENT_USER = object()
        fake.QueryValueEx.return_value = (prog_id, 1)
        fake.OpenKey.return_value.__enter__.return_value = mock.MagicMock()
        fake.OpenKey.return_value.__exit__.return_value = False
        return fake

    def test_registry_lookup_failure_reports_operation_failed(self):
        fake = self._fake_winreg("ChromeHTML")
        fake.OpenKey.side_effect = OSError("no key")
        with mock.patch.dict("sys.modules", {"winreg": fake}):
            result = OpenIncognitoWindowSkill().execute({})
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_an_unsupported_browser_is_rejected(self):
        fake = self._fake_winreg("FirefoxHTML")
        with mock.patch.dict("sys.modules", {"winreg": fake}):
            result = OpenIncognitoWindowSkill().execute({})
        self.assertEqual(result.error, "UNSUPPORTED_APP")

    def test_a_supported_browser_launches_with_the_right_flag(self):
        fake = self._fake_winreg("ChromeHTML")
        with mock.patch.dict("sys.modules", {"winreg": fake}):
            with mock.patch("subprocess.Popen") as popen:
                result = OpenIncognitoWindowSkill().execute({})
        self.assertTrue(result.success)
        popen.assert_called_once_with(["chrome.exe", "--incognito"], shell=True)

    def test_launch_failure_reports_launch_failed(self):
        fake = self._fake_winreg("ChromeHTML")
        with mock.patch.dict("sys.modules", {"winreg": fake}):
            with mock.patch("subprocess.Popen", side_effect=OSError("no chrome")):
                result = OpenIncognitoWindowSkill().execute({})
        self.assertEqual(result.error, "LAUNCH_FAILED")


class EmptyClipboardTests(unittest.TestCase):
    def test_a_successful_call_empties_the_clipboard(self):
        fake = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"win32clipboard": fake}):
            result = EmptyClipboardSkill().execute({})
        self.assertTrue(result.success)
        fake.OpenClipboard.assert_called_once()
        fake.EmptyClipboard.assert_called_once()
        fake.CloseClipboard.assert_called_once()

    def test_a_failure_reports_operation_failed_but_still_closes_the_clipboard(self):
        fake = mock.MagicMock()
        fake.EmptyClipboard.side_effect = RuntimeError("boom")
        with mock.patch.dict("sys.modules", {"win32clipboard": fake}):
            result = EmptyClipboardSkill().execute({})
        self.assertEqual(result.error, "OPERATION_FAILED")
        fake.CloseClipboard.assert_called_once()


if __name__ == "__main__":
    unittest.main()
