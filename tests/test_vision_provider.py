"""Test unitari per core/vision_provider.py: nessuna suite esisteva finora. describe() mocka
urllib.request.urlopen, stesso confine gia' usato per EmbeddingProvider/HomeAssistantClient/
NestClient/OllamaClient (vedi tests/test_embedding_provider.py) - nessuna vera chiamata a Ollama.

F1: buco reale trovato e corretto in questa sessione. describe() dichiara esplicitamente
"nessuna eccezione esce da describe()" (vedi il docstring della classe) e la skill chiamante
(skills/describe_screen.py) si fida di quella promessa - non ha un try/except attorno alla
chiamata. Riprodotto per davvero che un corpo JSON valido ma non nella forma attesa (es. "null",
"[]", un numero) faceva uscire un AttributeError invece di restituire None, rompendo quella
promessa e potendo far propagare un errore non gestito fino a JakeCore."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib import error

from core.vision_provider import VisionProvider


def _fake_response(body_bytes: bytes) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = body_bytes
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _fake_json_response(payload) -> mock.MagicMock:
    return _fake_response(json.dumps(payload).encode("utf-8"))


class _WithImageFile(unittest.TestCase):
    def setUp(self):
        tmp_dir = tempfile.mkdtemp(prefix="jake_vision_provider_test_")
        self.image_path = Path(tmp_dir) / "screenshot.png"
        self.image_path.write_bytes(b"contenuto finto di un'immagine")


class DescribeSuccessTests(_WithImageFile):
    def test_successful_response_returns_the_description(self):
        provider = VisionProvider()
        with mock.patch(
            "urllib.request.urlopen",
            return_value=_fake_json_response({"message": {"content": "una finestra di codice"}}),
        ):
            self.assertEqual(provider.describe(self.image_path), "una finestra di codice")

    def test_sends_the_model_question_and_base64_image_in_the_request_body(self):
        provider = VisionProvider(model="qwen2.5vl:7b")
        with mock.patch(
            "urllib.request.urlopen", return_value=_fake_json_response({"message": {"content": "ok"}}),
        ) as urlopen:
            provider.describe(self.image_path, question="cosa mostra il grafico?")

        sent_request = urlopen.call_args[0][0]
        body = json.loads(sent_request.data.decode("utf-8"))
        self.assertEqual(body["model"], "qwen2.5vl:7b")
        self.assertEqual(body["messages"][0]["content"], "cosa mostra il grafico?")
        self.assertTrue(body["messages"][0]["images"][0])  # stringa base64 non vuota

    def test_missing_question_uses_the_default_prompt(self):
        provider = VisionProvider()
        with mock.patch(
            "urllib.request.urlopen", return_value=_fake_json_response({"message": {"content": "ok"}}),
        ) as urlopen:
            provider.describe(self.image_path)

        sent_request = urlopen.call_args[0][0]
        body = json.loads(sent_request.data.decode("utf-8"))
        self.assertIn("Descrivi in italiano", body["messages"][0]["content"])

    def test_whitespace_only_content_is_treated_as_no_description(self):
        provider = VisionProvider()
        with mock.patch(
            "urllib.request.urlopen", return_value=_fake_json_response({"message": {"content": "   "}}),
        ):
            self.assertIsNone(provider.describe(self.image_path))


class DescribeDegradesGracefullyTests(_WithImageFile):
    """F1: describe() non deve mai sollevare - la skill chiamante non ha un try/except attorno
    alla chiamata (vedi skills/describe_screen.py) e si affida a questa garanzia."""

    def test_missing_image_file_returns_none(self):
        provider = VisionProvider()
        self.assertIsNone(provider.describe(Path("non_esiste_davvero.png")))

    def test_connection_failure_returns_none(self):
        provider = VisionProvider()
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("connessione rifiutata")):
            self.assertIsNone(provider.describe(self.image_path))

    def test_timeout_returns_none(self):
        provider = VisionProvider()
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            self.assertIsNone(provider.describe(self.image_path))

    def test_invalid_json_response_returns_none(self):
        provider = VisionProvider()
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(b"non e' json valido")):
            self.assertIsNone(provider.describe(self.image_path))

    def test_missing_message_field_returns_none_not_a_crash(self):
        provider = VisionProvider()
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response({})):
            self.assertIsNone(provider.describe(self.image_path))

    def test_json_root_not_a_dict_returns_none_not_a_crash(self):
        """Il buco reale trovato in questa sessione: 'null'/'[]'/un numero sono JSON validi ma
        non hanno la forma attesa - prima della correzione result.get(...) sollevava
        AttributeError invece di degradare a None."""
        provider = VisionProvider()
        for body in (b"null", b"[]", b"42", b'"ciao"'):
            with mock.patch("urllib.request.urlopen", return_value=_fake_response(body)):
                self.assertIsNone(provider.describe(self.image_path), msg=body)

    def test_message_field_not_a_dict_returns_none_not_a_crash(self):
        provider = VisionProvider()
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response({"message": None})):
            self.assertIsNone(provider.describe(self.image_path))
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response({"message": "testo"})):
            self.assertIsNone(provider.describe(self.image_path))

    def test_content_field_not_a_string_returns_none_not_a_crash(self):
        provider = VisionProvider()
        with mock.patch(
            "urllib.request.urlopen", return_value=_fake_json_response({"message": {"content": 123}}),
        ):
            self.assertIsNone(provider.describe(self.image_path))


if __name__ == "__main__":
    unittest.main()
