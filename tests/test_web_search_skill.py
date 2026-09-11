"""Test unitari per skills/web_search.py: nessuna suite esisteva finora. urllib.request.urlopen
e' sempre mockato, is_online forzato a True.

F1: buco reale corretto in questa sessione (stesso pattern sistemico gia' corretto per altri
consumatori diretti di API esterne) - WebSearchSkill non validava che il payload fosse un
dizionario prima di chiamare payload.get(...): un corpo JSON valido ma non un dizionario faceva
sollevare AttributeError, mai catturato, invece di degradare a NOT_FOUND."""
import json
import unittest
from unittest import mock
from urllib import error

from skills.web_search import WebSearchSkill


def _fake_response(body_bytes: bytes) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = body_bytes
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _fake_json_response(payload) -> mock.MagicMock:
    return _fake_response(json.dumps(payload).encode("utf-8"))


class WebSearchTests(unittest.TestCase):
    def test_missing_query_fails(self):
        result = WebSearchSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_offline_reports_network_unavailable(self):
        with mock.patch("skills.web_search.is_online", return_value=False):
            result = WebSearchSkill().execute({"query": "python"})
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")

    def test_a_direct_abstract_is_used_as_the_summary(self):
        payload = {"AbstractText": "Python e' un linguaggio.", "AbstractURL": "https://example.com"}
        with mock.patch("skills.web_search.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
                result = WebSearchSkill().execute({"query": "python"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["summary"], "Python e' un linguaggio.")
        self.assertEqual(result.data["url"], "https://example.com")

    def test_falls_back_to_related_topics_when_no_abstract(self):
        payload = {"AbstractText": "", "RelatedTopics": [{"Text": "Argomento correlato", "FirstURL": "https://x.com"}]}
        with mock.patch("skills.web_search.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
                result = WebSearchSkill().execute({"query": "argomento"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["summary"], "Argomento correlato")

    def test_no_usable_result_reports_not_found(self):
        payload = {"AbstractText": "", "RelatedTopics": []}
        with mock.patch("skills.web_search.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
                result = WebSearchSkill().execute({"query": "boh"})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_connection_failure_reports_network_unavailable(self):
        with mock.patch("skills.web_search.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
                result = WebSearchSkill().execute({"query": "python"})
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")

    def test_malformed_payload_does_not_crash(self):
        """Il buco reale trovato e corretto in questa sessione."""
        bodies = [b"null", b"[]", b"42", json.dumps({"RelatedTopics": "boh"}).encode()]
        for body in bodies:
            with mock.patch("skills.web_search.is_online", return_value=True):
                with mock.patch("urllib.request.urlopen", return_value=_fake_response(body)):
                    result = WebSearchSkill().execute({"query": "python"})
            self.assertEqual(result.error, "NOT_FOUND", msg=body)

    def test_malformed_related_topics_entries_are_skipped_not_a_crash(self):
        payload = {"AbstractText": "", "RelatedTopics": ["stringa inattesa", {"senza_text": True}, 42]}
        with mock.patch("skills.web_search.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
                result = WebSearchSkill().execute({"query": "python"})
        self.assertEqual(result.error, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
