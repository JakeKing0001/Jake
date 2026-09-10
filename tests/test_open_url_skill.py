"""Test unitari per skills/open_url.py: nessuna suite esisteva finora. webbrowser.open e'
sempre mockato."""
import unittest
from unittest import mock

from skills.open_url import OpenUrlSkill


class MissingParametersTests(unittest.TestCase):
    def test_missing_url_fails(self):
        result = OpenUrlSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")


class SchemeHandlingTests(unittest.TestCase):
    def test_a_bare_domain_gets_an_https_prefix(self):
        with mock.patch("webbrowser.open", return_value=True) as browser_open:
            result = OpenUrlSkill().execute({"url": "example.com"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["url"], "https://example.com")
        browser_open.assert_called_once_with("https://example.com")

    def test_an_explicit_http_url_is_kept_as_is(self):
        with mock.patch("webbrowser.open", return_value=True) as browser_open:
            OpenUrlSkill().execute({"url": "http://example.com"})
        browser_open.assert_called_once_with("http://example.com")

    def test_scheme_check_is_case_insensitive(self):
        with mock.patch("webbrowser.open", return_value=True):
            result = OpenUrlSkill().execute({"url": "HTTPS://example.com"})
        self.assertTrue(result.success)


class DangerousSchemeRejectionTests(unittest.TestCase):
    def test_javascript_scheme_is_rejected(self):
        with mock.patch("webbrowser.open") as browser_open:
            result = OpenUrlSkill().execute({"url": "javascript:alert(1)"})
        self.assertEqual(result.error, "INVALID_URL")
        browser_open.assert_not_called()

    def test_data_scheme_is_rejected(self):
        result = OpenUrlSkill().execute({"url": "data:text/html,<script>alert(1)</script>"})
        self.assertEqual(result.error, "INVALID_URL")

    def test_file_scheme_is_rejected(self):
        result = OpenUrlSkill().execute({"url": "file:///C:/Windows/System32/config"})
        self.assertEqual(result.error, "INVALID_URL")

    def test_vbscript_scheme_is_rejected(self):
        result = OpenUrlSkill().execute({"url": "vbscript:msgbox(1)"})
        self.assertEqual(result.error, "INVALID_URL")

    def test_dangerous_scheme_check_is_case_insensitive(self):
        result = OpenUrlSkill().execute({"url": "JavaScript:alert(1)"})
        self.assertEqual(result.error, "INVALID_URL")

    def test_a_url_without_a_valid_host_is_rejected(self):
        with mock.patch("webbrowser.open") as browser_open:
            result = OpenUrlSkill().execute({"url": "https://"})
        self.assertEqual(result.error, "INVALID_URL")
        browser_open.assert_not_called()


class BrowserFailureTests(unittest.TestCase):
    def test_browser_open_returning_false_is_reported(self):
        with mock.patch("webbrowser.open", return_value=False):
            result = OpenUrlSkill().execute({"url": "https://example.com"})
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_browser_open_raising_is_reported_not_raised(self):
        with mock.patch("webbrowser.open", side_effect=Exception("boom")):
            result = OpenUrlSkill().execute({"url": "https://example.com"})
        self.assertEqual(result.error, "OPERATION_FAILED")


if __name__ == "__main__":
    unittest.main()
