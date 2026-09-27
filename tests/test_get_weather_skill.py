"""Test unitari per skills/get_weather.py: nessuna suite esisteva finora. urllib.request.urlopen
e' sempre mockato, is_online forzato a True. Open-Meteo: geocoding della citta' e poi meteo attuale, nessuna chiave.

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
    return error.HTTPError(url="https://api.open-meteo.com", code=code, msg="err", hdrs=None, fp=None)


def _config():
    config = mock.MagicMock()
    config.get.return_value = "fake-key"
    return config


_PLACE = {"results": [{"name": "Roma", "latitude": 41.89, "longitude": 12.51}]}
_CURRENT = {"current": {"temperature_2m": 22.5, "apparent_temperature": 21.0, "weather_code": 0, "wind_speed_10m": 7.4}}


class GetWeatherTests(unittest.TestCase):
    def test_missing_city_fails(self):
        result = GetWeatherSkill(_config()).execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_no_api_key_is_needed(self):
        with mock.patch("skills.get_weather.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", side_effect=[_fake_json_response(_PLACE),
                                                                   _fake_json_response(_CURRENT)]):
                result = GetWeatherSkill(None).execute({"city": "roma"})
        self.assertTrue(result.success, result)

    def test_offline_reports_network_unavailable(self):
        with mock.patch("skills.get_weather.is_online", return_value=False):
            result = GetWeatherSkill(_config()).execute({"city": "Roma"})
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")

    def test_a_successful_response_returns_weather_data(self):
        with mock.patch("skills.get_weather.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", side_effect=[_fake_json_response(_PLACE),
                                                                   _fake_json_response(_CURRENT)]) as urlopen:
                result = GetWeatherSkill(_config()).execute({"city": "roma"})
        self.assertTrue(result.success)
        self.assertIn("latitude=41.89", urlopen.call_args_list[1].args[0])
        self.assertEqual(result.data["city"], "Roma")
        self.assertEqual(result.data["wind_kmh"], 7.4)
        self.assertEqual(result.data["description"], "sereno")
        self.assertEqual(result.data["temperature"], 22.5)
        self.assertEqual(result.data["feels_like"], 21.0)

    def test_city_not_found_when_geocoding_has_no_results(self):
        with mock.patch("skills.get_weather.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", return_value=_fake_json_response({"generationtime_ms": 0.8})):
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

    def test_malformed_current_fields_degrade_gracefully(self):
        for current in ({"current": "boh"}, {"current": {"weather_code": "x"}}):
            with mock.patch("skills.get_weather.is_online", return_value=True):
                with mock.patch("urllib.request.urlopen", side_effect=[_fake_json_response(_PLACE),
                                                                       _fake_json_response(current)]):
                    result = GetWeatherSkill(_config()).execute({"city": "Roma"})
            if isinstance(current["current"], dict):
                self.assertTrue(result.success)
                self.assertEqual(result.data["description"], "")
                self.assertIsNone(result.data["temperature"])
            else:
                self.assertEqual(result.error, "NETWORK_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
