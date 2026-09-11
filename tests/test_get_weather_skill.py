"""Test unitari per skills/get_weather.py: nessuna suite esisteva finora. urllib.request.urlopen
e' sempre mockato, is_online forzato a True, config e' un MagicMock con una weather_api_key finta.

F1: buco reale corretto in questa sessione (stesso pattern sistemico gia' corretto per altri
consumatori diretti di API esterne) - GetWeatherSkill non validava la forma della risposta prima
di usarla: un corpo JSON valido ma non nella forma attesa faceva sollevare AttributeError o
TypeError, mai catturato, invece di degradare a NETWORK_UNAVAILABLE o restituire campi vuoti."""
import json
import unittest
from unittest import mock
from urllib import error

from skills.get_weather import GetWeatherSkill


def _fake_response(body_bytes: bytes) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = body_bytes
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _fake_json_response(payload) -> mock.MagicMock:
    return _fake_response(json.dumps(payload).encode("utf-8"))


def _fake_http_error(code: int) -> error.HTTPError:
    return error.HTTPError(url="https://api.openweathermap.org", code=code, msg="err", hdrs=None, fp=None)


def _config():
    config = mock.MagicMock()
    config.get.return_value = "fake-key"
    return config


class GetWeatherTests(unittest.TestCase):
    def test_missing_city_fails(self):
        result = GetWeatherSkill(_config()).execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_missing_api_key_fails(self):
        config = mock.MagicMock()
        config.get.return_value = None
        result = GetWeatherSkill(config).execute({"city": "Roma"})
        self.assertEqual(result.error, "MISSING_API_KEY")

    def test_offline_reports_network_unavailable(self):
        with mock.patch("skills.get_weather.is_online", return_value=False):
            result = GetWeatherSkill(_config()).execute({"city": "Roma"})
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")

    def test_a_successful_response_returns_weather_data(self):
        payload = {"weather": [{"description": "sereno"}], "main": {"temp": 22.5, "feels_like": 21.0}}
        with mock.patch("skills.get_weather.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
                result = GetWeatherSkill(_config()).execute({"city": "Roma"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["description"], "sereno")
        self.assertEqual(result.data["temperature"], 22.5)
        self.assertEqual(result.data["feels_like"], 21.0)

    def test_city_not_found_on_http_404(self):
        with mock.patch("skills.get_weather.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", side_effect=_fake_http_error(404)):
                result = GetWeatherSkill(_config()).execute({"city": "Cittainesistente"})
        self.assertEqual(result.error, "CITY_NOT_FOUND")

    def test_other_http_error_reports_network_unavailable(self):
        with mock.patch("skills.get_weather.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", side_effect=_fake_http_error(500)):
                result = GetWeatherSkill(_config()).execute({"city": "Roma"})
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")

    def test_connection_failure_reports_network_unavailable(self):
        with mock.patch("skills.get_weather.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
                result = GetWeatherSkill(_config()).execute({"city": "Roma"})
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")

    def test_malformed_payload_root_does_not_crash(self):
        """Il buco reale trovato e corretto in questa sessione."""
        for body in (b"null", b"[]", b"42"):
            with mock.patch("skills.get_weather.is_online", return_value=True):
                with mock.patch("urllib.request.urlopen", return_value=_fake_response(body)):
                    result = GetWeatherSkill(_config()).execute({"city": "Roma"})
            self.assertEqual(result.error, "NETWORK_UNAVAILABLE", msg=body)

    def test_malformed_weather_and_main_fields_degrade_gracefully(self):
        payload = {"weather": "boh", "main": "boh"}
        with mock.patch("skills.get_weather.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
                result = GetWeatherSkill(_config()).execute({"city": "Roma"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["description"], "")
        self.assertIsNone(result.data["temperature"])
        self.assertIsNone(result.data["feels_like"])


if __name__ == "__main__":
    unittest.main()
