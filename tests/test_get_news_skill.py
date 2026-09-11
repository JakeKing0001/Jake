"""Test unitari per skills/get_news.py: nessuna suite esisteva finora. urllib.request.urlopen e'
sempre mockato, is_online forzato a True, config e' un MagicMock con una news_api_key finta.

F1: buco reale corretto in questa sessione (stesso pattern sistemico gia' corretto per altri
consumatori diretti di API esterne) - GetNewsSkill non validava la forma della risposta prima di
usarla: un corpo JSON valido ma non nella forma attesa faceva sollevare AttributeError, mai
catturato, invece di degradare a NOT_FOUND."""
import json
import unittest
from unittest import mock
from urllib import error

from skills.get_news import GetNewsSkill


def _fake_response(body_bytes: bytes) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = body_bytes
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _fake_json_response(payload) -> mock.MagicMock:
    return _fake_response(json.dumps(payload).encode("utf-8"))


def _config():
    config = mock.MagicMock()
    config.get.return_value = "fake-key"
    return config


class GetNewsTests(unittest.TestCase):
    def test_missing_api_key_fails(self):
        config = mock.MagicMock()
        config.get.return_value = None
        result = GetNewsSkill(config).execute({})
        self.assertEqual(result.error, "MISSING_API_KEY")

    def test_offline_reports_network_unavailable(self):
        with mock.patch("skills.get_news.is_online", return_value=False):
            result = GetNewsSkill(_config()).execute({})
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")

    def test_a_successful_response_returns_headlines(self):
        payload = {"articles": [
            {"title": "Titolo uno", "source": {"name": "Fonte Uno"}},
            {"title": "Titolo due", "source": {"name": "Fonte Due"}},
        ]}
        with mock.patch("skills.get_news.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
                result = GetNewsSkill(_config()).execute({})
        self.assertTrue(result.success)
        self.assertEqual(len(result.data["headlines"]), 2)
        self.assertEqual(result.data["headlines"][0], {"title": "Titolo uno", "source": "Fonte Uno"})

    def test_caps_results_at_max(self):
        payload = {"articles": [{"title": f"T{i}", "source": {"name": "F"}} for i in range(20)]}
        with mock.patch("skills.get_news.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
                result = GetNewsSkill(_config()).execute({})
        self.assertEqual(len(result.data["headlines"]), GetNewsSkill.MAX_RESULTS)

    def test_connection_failure_reports_network_unavailable(self):
        with mock.patch("skills.get_news.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
                result = GetNewsSkill(_config()).execute({})
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")

    def test_malformed_payload_does_not_crash(self):
        """Il buco reale trovato e corretto in questa sessione."""
        bodies = [b"null", b"[]", b"42", json.dumps({"articles": "boh"}).encode(),
                  json.dumps({"articles": [1, 2, "boh"]}).encode()]
        for body in bodies:
            with mock.patch("skills.get_news.is_online", return_value=True):
                with mock.patch("urllib.request.urlopen", return_value=_fake_response(body)):
                    result = GetNewsSkill(_config()).execute({})
            self.assertEqual(result.error, "NOT_FOUND", msg=body)

    def test_article_with_malformed_source_still_returns_empty_source_name(self):
        payload = {"articles": [{"title": "Titolo", "source": "non un dizionario"}]}
        with mock.patch("skills.get_news.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
                result = GetNewsSkill(_config()).execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["headlines"][0]["source"], "")


if __name__ == "__main__":
    unittest.main()
