"""Test unitari per skills/open_search_result.py: nessuna suite esisteva finora, nessun bug
trovato. Path.exists/os.startfile sempre mockati (mai un vero file aperto)."""
import unittest
from unittest import mock

from skills.open_search_result import OpenSearchResultSkill


class OpenSearchResultTests(unittest.TestCase):
    def test_no_path_and_no_index_fails(self):
        result = OpenSearchResultSkill(mock.MagicMock()).execute({})
        self.assertEqual(result.error, "RESULT_NOT_FOUND")

    def test_index_out_of_range_fails(self):
        conversation_state = mock.MagicMock()
        conversation_state.get_last_search_results.return_value = [{"path": "C:\\a.txt"}]
        result = OpenSearchResultSkill(conversation_state).execute({"index": 5})
        self.assertEqual(result.error, "RESULT_NOT_FOUND")

    def test_a_nonexistent_path_fails(self):
        with mock.patch("pathlib.Path.exists", return_value=False):
            result = OpenSearchResultSkill(mock.MagicMock()).execute({"path": "C:\\inesistente.txt"})
        self.assertEqual(result.error, "RESULT_NOT_FOUND")

    def test_a_direct_path_is_opened(self):
        with mock.patch("pathlib.Path.exists", return_value=True):
            with mock.patch("os.startfile", create=True) as startfile:
                with mock.patch("sys.platform", "win32"):
                    result = OpenSearchResultSkill(mock.MagicMock()).execute({"path": "C:\\a.txt"})
        self.assertTrue(result.success)
        startfile.assert_called_once()

    def test_an_index_resolves_from_the_last_search_results(self):
        conversation_state = mock.MagicMock()
        conversation_state.get_last_search_results.return_value = [{"path": "C:\\a.txt"}, {"path": "C:\\b.txt"}]
        with mock.patch("pathlib.Path.exists", return_value=True):
            with mock.patch("os.startfile", create=True) as startfile:
                with mock.patch("sys.platform", "win32"):
                    result = OpenSearchResultSkill(conversation_state).execute({"index": 2})
        self.assertTrue(result.success)
        self.assertEqual(result.data["path"], "C:\\b.txt")
        startfile.assert_called_once()

    def test_a_launch_failure_reports_operation_failed(self):
        with mock.patch("pathlib.Path.exists", return_value=True):
            with mock.patch("os.startfile", side_effect=OSError("boom"), create=True):
                with mock.patch("sys.platform", "win32"):
                    result = OpenSearchResultSkill(mock.MagicMock()).execute({"path": "C:\\a.txt"})
        self.assertEqual(result.error, "OPERATION_FAILED")


if __name__ == "__main__":
    unittest.main()
