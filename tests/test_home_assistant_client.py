"""Test unitari per HomeAssistantClient (v5.7, Home/IoT). Nessun hub reale disponibile in questo
ambiente: mocka urllib.request.urlopen verificando che le richieste abbiano la forma esatta
dell'API REST ufficiale di Home Assistant (bearer token, GET /api/states, POST /api/services/
<domain>/<service>) - lo stesso confine gia' usato per NestClient/OllamaClient."""
import io
import json
import unittest
from unittest import mock
from urllib import error

from core.home_assistant_client import HomeAssistantClient, HomeAssistantError, HomeAssistantUnavailable


def _fake_response(payload) -> mock.MagicMock:
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    response = mock.MagicMock()
    response.read.return_value = body
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class IsAvailableTests(unittest.TestCase):
    def test_available_only_with_both_url_and_token(self):
        self.assertFalse(HomeAssistantClient().is_available())
        self.assertFalse(HomeAssistantClient(base_url="http://homeassistant.local:8123").is_available())
        self.assertFalse(HomeAssistantClient(token="abc").is_available())
        self.assertTrue(HomeAssistantClient(base_url="http://homeassistant.local:8123", token="abc").is_available())

    def test_never_makes_a_network_call(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            HomeAssistantClient(base_url="http://x", token="t").is_available()
        urlopen.assert_not_called()


def _client() -> HomeAssistantClient:
    return HomeAssistantClient(base_url="http://homeassistant.local:8123", token="secret-token")


class ListStatesTests(unittest.TestCase):
    def test_sends_the_bearer_token_and_correct_path(self):
        with mock.patch("urllib.request.urlopen", return_value=_fake_response([])) as urlopen:
            _client().list_states()

        sent_request = urlopen.call_args[0][0]
        self.assertEqual(sent_request.full_url, "http://homeassistant.local:8123/api/states")
        self.assertEqual(sent_request.get_method(), "GET")
        self.assertEqual(sent_request.get_header("Authorization"), "Bearer secret-token")

    def test_parses_the_states_list(self):
        payload = [{"entity_id": "light.soggiorno", "state": "on", "attributes": {"friendly_name": "Luce soggiorno"}}]
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)):
            states = _client().list_states()
        self.assertEqual(states, payload)

    def test_filters_by_domain(self):
        payload = [
            {"entity_id": "light.soggiorno", "state": "on"},
            {"entity_id": "switch.presa_cucina", "state": "off"},
        ]
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload)):
            states = _client().list_states(domain="light")
        self.assertEqual([s["entity_id"] for s in states], ["light.soggiorno"])

    def test_connection_failure_raises_unavailable(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("no route to host")):
            with self.assertRaises(HomeAssistantUnavailable):
                _client().list_states()

    def test_http_error_raises_home_assistant_error(self):
        http_err = error.HTTPError("http://x", 401, "Unauthorized", {}, io.BytesIO(b"invalid token"))
        with mock.patch("urllib.request.urlopen", side_effect=http_err):
            with self.assertRaises(HomeAssistantError):
                _client().list_states()


class CallServiceTests(unittest.TestCase):
    def test_posts_to_the_correct_service_path_with_entity_id_in_the_body(self):
        with mock.patch("urllib.request.urlopen", return_value=_fake_response([])) as urlopen:
            _client().call_service("light", "turn_on", entity_id="light.soggiorno")

        sent_request = urlopen.call_args[0][0]
        self.assertEqual(sent_request.full_url, "http://homeassistant.local:8123/api/services/light/turn_on")
        self.assertEqual(sent_request.get_method(), "POST")
        body = json.loads(sent_request.data.decode("utf-8"))
        self.assertEqual(body, {"entity_id": "light.soggiorno"})

    def test_extra_service_data_is_included(self):
        with mock.patch("urllib.request.urlopen", return_value=_fake_response([])) as urlopen:
            _client().call_service("light", "turn_on", entity_id="light.soggiorno", brightness=200)

        body = json.loads(urlopen.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(body, {"entity_id": "light.soggiorno", "brightness": 200})


if __name__ == "__main__":
    unittest.main()
