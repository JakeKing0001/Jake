"""Test unitari per skills/editor_control.py: nessuna suite esisteva finora. subprocess.Popen e'
sempre mockato (un test che lo chiamasse per davvero aprirebbe VS Code)."""
import tempfile
import unittest
from unittest import mock

from skills.editor_control import OpenInEditorSkill


class OpenInEditorTests(unittest.TestCase):
    def test_missing_path_fails(self):
        result = OpenInEditorSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_nonexistent_path_fails(self):
        result = OpenInEditorSkill().execute({"path": r"C:\non\esiste\davvero"})
        self.assertEqual(result.error, "PATH_NOT_FOUND")

    def test_an_existing_folder_is_opened_in_vscode(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with mock.patch("subprocess.Popen") as popen:
                result = OpenInEditorSkill().execute({"path": tmp_dir})
            self.assertTrue(result.success)
            popen.assert_called_once_with(["code", tmp_dir], shell=True)

    def test_a_launch_failure_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with mock.patch("subprocess.Popen", side_effect=OSError("code non trovato")):
                result = OpenInEditorSkill().execute({"path": tmp_dir})
            self.assertEqual(result.error, "LAUNCH_FAILED")


if __name__ == "__main__":
    unittest.main()
