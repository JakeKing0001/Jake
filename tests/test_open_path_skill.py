"""Test unitari per skills/open_path.py: nessuna suite esisteva finora. os.startfile e'
mockato (apre davvero un'applicazione GUI, non simulabile in un test automatico), il resto
(esistenza del percorso, branch 'shell:') usa file/cartelle reali su disco temporaneo."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skills.open_path import OpenPathSkill


class MissingParametersTests(unittest.TestCase):
    def test_empty_path_fails(self):
        result = OpenPathSkill().execute({"path": ""})
        self.assertEqual(result.error, "MISSING_PARAMETERS")


class RealPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_open_path_test_"))

    def test_nonexistent_path_fails(self):
        result = OpenPathSkill().execute({"path": str(self.tmp_dir / "non_esiste.txt")})
        self.assertEqual(result.error, "PATH_NOT_FOUND")

    def test_an_existing_file_is_opened_via_startfile(self):
        target = self.tmp_dir / "appunti.txt"
        target.write_text("ciao", encoding="utf-8")
        with mock.patch("sys.platform", "win32"), mock.patch("os.startfile") as startfile:
            result = OpenPathSkill().execute({"path": str(target)})
        self.assertTrue(result.success)
        startfile.assert_called_once_with(str(target))

    def test_an_existing_folder_is_opened_via_startfile(self):
        with mock.patch("sys.platform", "win32"), mock.patch("os.startfile") as startfile:
            result = OpenPathSkill().execute({"path": str(self.tmp_dir)})
        self.assertTrue(result.success)
        startfile.assert_called_once_with(str(self.tmp_dir))

    def test_startfile_failure_is_reported_not_raised(self):
        target = self.tmp_dir / "appunti.txt"
        target.write_text("ciao", encoding="utf-8")
        with mock.patch("sys.platform", "win32"), mock.patch("os.startfile", side_effect=OSError("boom")):
            result = OpenPathSkill().execute({"path": str(target)})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_non_windows_platform_uses_xdg_open(self):
        target = self.tmp_dir / "appunti.txt"
        target.write_text("ciao", encoding="utf-8")
        with mock.patch("sys.platform", "linux"), mock.patch("subprocess.Popen") as popen:
            result = OpenPathSkill().execute({"path": str(target)})
        self.assertTrue(result.success)
        popen.assert_called_once_with(["xdg-open", str(target)])


class ShellSpecialFolderTests(unittest.TestCase):
    def test_a_shell_path_is_passed_directly_to_startfile_without_an_existence_check(self):
        with mock.patch("os.startfile") as startfile:
            result = OpenPathSkill().execute({"path": "shell:RecycleBinFolder"})
        self.assertTrue(result.success)
        startfile.assert_called_once_with("shell:RecycleBinFolder")

    def test_shell_prefix_is_case_insensitive(self):
        with mock.patch("os.startfile") as startfile:
            result = OpenPathSkill().execute({"path": "SHELL:RecycleBinFolder"})
        self.assertTrue(result.success)
        startfile.assert_called_once_with("SHELL:RecycleBinFolder")

    def test_a_failing_shell_path_is_reported_not_raised(self):
        with mock.patch("os.startfile", side_effect=OSError("boom")):
            result = OpenPathSkill().execute({"path": "shell:BogusFolder"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")


if __name__ == "__main__":
    unittest.main()
