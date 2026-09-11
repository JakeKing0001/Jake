"""Test unitari per skills/astronomy.py: nessuna suite esisteva finora. urllib.request.urlopen e'
sempre mockato, is_online forzato a True.

F1: buchi reali corretti in questa sessione (stesso pattern sistemico gia' corretto per altri
consumatori diretti di API esterne) - GetSunriseSunsetSkill non validava la forma delle risposte
di geocoding/forecast prima di usarle: un corpo JSON valido ma non nella forma attesa faceva
sollevare AttributeError (da payload.get in _geocode) o TypeError (indicizzando un forecast non
valido, PRIMA fuori dal blocco try/except e quindi mai catturato)."""
import json
import unittest
from unittest import mock
from urllib import error

from skills.astronomy import GetMoonPhaseSkill, GetSunriseSunsetSkill


def _fake_response(body_bytes: bytes) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = body_bytes
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _fake_json_response(payload) -> mock.MagicMock:
    return _fake_response(json.dumps(payload).encode("utf-8"))


GEOCODE_OK = {"results": [{"latitude": 41.9, "longitude": 12.5}]}
FORECAST_OK = {"daily": {"sunrise": ["2026-09-11T06:30"], "sunset": ["2026-09-11T19:45"]}}


class GetSunriseSunsetTests(unittest.TestCase):
    def test_missing_city_fails(self):
        result = GetSunriseSunsetSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_offline_reports_network_unavailable(self):
        with mock.patch("skills.astronomy.is_online", return_value=False):
            result = GetSunriseSunsetSkill().execute({"city": "Roma"})
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")

    def test_a_successful_response_returns_sunrise_and_sunset(self):
        responses = [_fake_json_response(GEOCODE_OK), _fake_json_response(FORECAST_OK)]
        with mock.patch("skills.astronomy.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", side_effect=responses):
                result = GetSunriseSunsetSkill().execute({"city": "Roma"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["sunrise"], "06:30")
        self.assertEqual(result.data["sunset"], "19:45")

    def test_city_not_found_when_geocoding_has_no_results(self):
        with mock.patch("skills.astronomy.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", return_value=_fake_json_response({"results": []})):
                result = GetSunriseSunsetSkill().execute({"city": "Cittainesistente"})
        self.assertEqual(result.error, "CITY_NOT_FOUND")

    def test_connection_failure_reports_network_unavailable(self):
        with mock.patch("skills.astronomy.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
                result = GetSunriseSunsetSkill().execute({"city": "Roma"})
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")

    def test_malformed_geocode_payload_does_not_crash(self):
        """Il buco reale: payload non un dizionario faceva sollevare AttributeError in _geocode."""
        for body in (b"null", b"[]", b"42"):
            with mock.patch("skills.astronomy.is_online", return_value=True):
                with mock.patch("urllib.request.urlopen", return_value=_fake_response(body)):
                    result = GetSunriseSunsetSkill().execute({"city": "Roma"})
            self.assertEqual(result.error, "NETWORK_UNAVAILABLE", msg=body)

    def test_malformed_forecast_payload_does_not_crash(self):
        """Il buco reale: forecast non valido faceva sollevare TypeError fuori dal try/except."""
        for forecast_body in (b"null", b"{}", b'{"daily": null}', b'{"daily": {"sunrise": []}}'):
            responses = [_fake_json_response(GEOCODE_OK), _fake_response(forecast_body)]
            with mock.patch("skills.astronomy.is_online", return_value=True):
                with mock.patch("urllib.request.urlopen", side_effect=responses):
                    result = GetSunriseSunsetSkill().execute({"city": "Roma"})
            self.assertEqual(result.error, "NETWORK_UNAVAILABLE", msg=forecast_body)


class GetMoonPhaseTests(unittest.TestCase):
    def test_returns_a_known_phase_name(self):
        result = GetMoonPhaseSkill().execute({})
        self.assertTrue(result.success)
        self.assertIn(result.data["phase"], [
            "luna nuova", "luna crescente", "primo quarto", "gibbosa crescente",
            "luna piena", "gibbosa calante", "ultimo quarto", "luna calante",
        ])


if __name__ == "__main__":
    unittest.main()
