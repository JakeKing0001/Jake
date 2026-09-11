"""Test unitari per core/voice/rvc_client.py: nessuna suite esisteva finora, nessun bug trovato.
urllib.request.urlopen e' sempre mockato (mai un vero server RVC locale)."""
import unittest
from unittest import mock
from urllib import error

from core.voice.rvc_client import RvcClient, RvcError


def _fake_response(status: int = 200, body: bytes = b""):
    response = mock.MagicMock()
    response.status = status
    response.read.return_value = body
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class IsAvailableTests(unittest.TestCase):
    def test_a_running_server_is_available(self):
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(200)):
            self.assertTrue(RvcClient().is_available())

    def test_a_connection_failure_is_unavailable(self):
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
            self.assertFalse(RvcClient().is_available())

    def test_a_timeout_is_unavailable(self):
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            self.assertFalse(RvcClient().is_available())


class ConvertTests(unittest.TestCase):
    def test_a_successful_conversion_returns_the_converted_audio(self):
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(200, b"WAVDATA")):
            result = RvcClient().convert(b"input wav bytes")
        self.assertEqual(result, b"WAVDATA")

    def test_a_connection_failure_raises_rvc_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
            with self.assertRaises(RvcError):
                RvcClient().convert(b"input wav bytes")


if __name__ == "__main__":
    unittest.main()
