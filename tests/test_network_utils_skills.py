"""Test unitari per skills/network_utils.py: nessuna suite esisteva finora. subprocess.run e
urllib.request.urlopen sono sempre mockati: un test che li chiamasse per davvero farebbe
traffico di rete reale (ping/tracert/richieste HTTP) durante la suite."""
import unittest
from unittest import mock
from urllib import error

from skills.network_utils import CheckWebsiteStatusSkill, PingHostSkill, TraceRouteSkill


def _fake_completed(returncode: int, stdout: str = "") -> mock.MagicMock:
    completed = mock.MagicMock()
    completed.returncode = returncode
    completed.stdout = stdout
    return completed


class PingHostTests(unittest.TestCase):
    def test_missing_host_fails(self):
        result = PingHostSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_unreachable_host_reports_host_unreachable(self):
        with mock.patch("subprocess.run", return_value=_fake_completed(1)):
            result = PingHostSkill().execute({"host": "host.che.non.risponde"})
        self.assertEqual(result.error, "HOST_UNREACHABLE")

    def test_a_reachable_host_extracts_the_average_latency(self):
        with mock.patch("subprocess.run", return_value=_fake_completed(0, "Round Trip... Average = 23ms")):
            result = PingHostSkill().execute({"host": "google.com"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["latency"], "23ms")

    def test_italian_windows_output_is_also_recognized(self):
        with mock.patch("subprocess.run", return_value=_fake_completed(0, "Round trip... Media = 15ms")):
            result = PingHostSkill().execute({"host": "google.com"})
        self.assertEqual(result.data["latency"], "15ms")

    def test_missing_latency_in_the_output_still_succeeds(self):
        with mock.patch("subprocess.run", return_value=_fake_completed(0, "output inatteso")):
            result = PingHostSkill().execute({"host": "google.com"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["latency"], "n/d")

    def test_a_subprocess_failure_is_reported_not_raised(self):
        with mock.patch("subprocess.run", side_effect=OSError("boom")):
            result = PingHostSkill().execute({"host": "google.com"})
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_the_host_is_passed_as_a_single_argv_element_not_shell_interpreted(self):
        """Nessuna shell=True: anche un host con spazi/flag incorporati resta un unico
        argomento, non puo' iniettare flag aggiuntivi al comando ping."""
        with mock.patch("subprocess.run", return_value=_fake_completed(0)) as run:
            PingHostSkill().execute({"host": "google.com -n 100"})
        run.assert_called_once_with(
            ["ping", "-n", "3", "google.com -n 100"], capture_output=True, timeout=15,
            text=True, encoding="utf-8", errors="ignore",
        )


class TraceRouteTests(unittest.TestCase):
    def test_missing_host_fails(self):
        result = TraceRouteSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_no_hops_reports_host_unreachable(self):
        with mock.patch("subprocess.run", return_value=_fake_completed(0, "richiesta scaduta per tutti i salti")):
            result = TraceRouteSkill().execute({"host": "google.com"})
        self.assertEqual(result.error, "HOST_UNREACHABLE")

    def test_counts_the_numbered_hops(self):
        output = "  1    1 ms\n  2    5 ms\n  3    10 ms\n"
        with mock.patch("subprocess.run", return_value=_fake_completed(0, output)):
            result = TraceRouteSkill().execute({"host": "google.com"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["hop_count"], 3)

    def test_a_subprocess_failure_is_reported_not_raised(self):
        with mock.patch("subprocess.run", side_effect=OSError("boom")):
            result = TraceRouteSkill().execute({"host": "google.com"})
        self.assertEqual(result.error, "OPERATION_FAILED")


class CheckWebsiteStatusTests(unittest.TestCase):
    def test_missing_url_fails(self):
        result = CheckWebsiteStatusSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_bare_domain_gets_an_https_prefix(self):
        response = mock.MagicMock(status=200)
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            result = CheckWebsiteStatusSkill().execute({"url": "example.com"})
        self.assertEqual(result.data["url"], "https://example.com")
        urlopen.assert_called_once_with("https://example.com", timeout=8)

    def test_a_200_response_is_online(self):
        response = mock.MagicMock(status=200)
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with mock.patch("urllib.request.urlopen", return_value=response):
            result = CheckWebsiteStatusSkill().execute({"url": "https://example.com"})
        self.assertTrue(result.data["online"])
        self.assertEqual(result.data["status_code"], 200)

    def test_a_client_error_status_is_still_considered_online(self):
        """Un 404 vuol dire che il server ha risposto per davvero: il sito e' raggiungibile,
        anche se quella pagina specifica non esiste."""
        http_error = error.HTTPError("https://example.com/x", 404, "Not Found", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=http_error):
            result = CheckWebsiteStatusSkill().execute({"url": "https://example.com/x"})
        self.assertTrue(result.data["online"])
        self.assertEqual(result.data["status_code"], 404)

    def test_a_server_error_status_is_considered_offline(self):
        http_error = error.HTTPError("https://example.com", 503, "Service Unavailable", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=http_error):
            result = CheckWebsiteStatusSkill().execute({"url": "https://example.com"})
        self.assertFalse(result.data["online"])

    def test_a_connection_failure_is_considered_offline(self):
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
            result = CheckWebsiteStatusSkill().execute({"url": "https://example.com"})
        self.assertTrue(result.success)
        self.assertFalse(result.data["online"])


if __name__ == "__main__":
    unittest.main()
