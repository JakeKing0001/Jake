"""Test unitari per skills/print_utils.py: nessuna suite esisteva finora. os.startfile e'
sempre mockato (invierebbe per davvero un file alla stampante predefinita)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skills.print_utils import PrintFileSkill


class PrintFileTests(unittest.TestCase):
    def test_missing_path_fails(self):
        result = PrintFileSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_nonexistent_path_fails(self):
        result = PrintFileSkill().execute({"path": r"C:\non\esiste\davvero.pdf"})
        self.assertEqual(result.error, "PATH_NOT_FOUND")

    def test_an_existing_file_is_sent_to_the_default_printer(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
            handle.write(b"contenuto")
            path = Path(handle.name)
        try:
            with mock.patch("os.startfile") as startfile:
                result = PrintFileSkill().execute({"path": str(path)})
            self.assertTrue(result.success)
            startfile.assert_called_once_with(str(path), "print")
        finally:
            path.unlink(missing_ok=True)

    def test_a_startfile_failure_is_reported_not_raised(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
            path = Path(handle.name)
        try:
            with mock.patch("os.startfile", side_effect=OSError("nessuna stampante")):
                result = PrintFileSkill().execute({"path": str(path)})
            self.assertEqual(result.error, "OPERATION_FAILED")
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
