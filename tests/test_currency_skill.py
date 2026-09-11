"""Test unitari per skills/currency.py: nessuna suite esisteva finora. urllib.request.urlopen e'
sempre mockato, is_online forzato a True.

F1: buco reale corretto in questa sessione (stesso pattern sistemico gia' corretto per altri
consumatori diretti di API esterne) - ConvertCurrencySkill non validava la forma della risposta
prima di chiamare payload.get("rates").get(...): un corpo JSON valido ma non nella forma attesa
faceva sollevare AttributeError, mai catturato, invece di degradare a CURRENCY_NOT_FOUND."""
import json
import unittest
from unittest import mock
from urllib import error

from skills.currency import ConvertCurrencySkill


def _fake_response(body_bytes: bytes) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = body_bytes
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _fake_json_response(payload) -> mock.MagicMock:
    return _fake_response(json.dumps(payload).encode("utf-8"))


PARAMS = {"amount": 10, "from_currency": "eur", "to_currency": "usd"}


class ConvertCurrencyTests(unittest.TestCase):
    def test_missing_parameters_fails(self):
        result = ConvertCurrencySkill().execute({"amount": 10, "from_currency": "EUR"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_offline_reports_network_unavailable(self):
        with mock.patch("skills.currency.is_online", return_value=False):
            result = ConvertCurrencySkill().execute(PARAMS)
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")

    def test_a_successful_response_converts_the_amount(self):
        payload = {"result": "success", "rates": {"USD": 1.1}}
        with mock.patch("skills.currency.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
                result = ConvertCurrencySkill().execute(PARAMS)
        self.assertTrue(result.success)
        self.assertEqual(result.data["result"], 11.0)
        self.assertEqual(result.data["from_currency"], "EUR")
        self.assertEqual(result.data["to_currency"], "USD")

    def test_unknown_target_currency_reports_not_found(self):
        payload = {"result": "success", "rates": {"GBP": 0.9}}
        with mock.patch("skills.currency.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
                result = ConvertCurrencySkill().execute(PARAMS)
        self.assertEqual(result.error, "CURRENCY_NOT_FOUND")

    def test_connection_failure_reports_network_unavailable(self):
        with mock.patch("skills.currency.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
                result = ConvertCurrencySkill().execute(PARAMS)
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")

    def test_malformed_payload_does_not_crash(self):
        """Il buco reale trovato e corretto in questa sessione."""
        bodies = [b"null", b"[]", b"42", json.dumps({"result": "success", "rates": "boh"}).encode()]
        for body in bodies:
            with mock.patch("skills.currency.is_online", return_value=True):
                with mock.patch("urllib.request.urlopen", return_value=_fake_response(body)):
                    result = ConvertCurrencySkill().execute(PARAMS)
            self.assertEqual(result.error, "CURRENCY_NOT_FOUND", msg=body)


if __name__ == "__main__":
    unittest.main()
