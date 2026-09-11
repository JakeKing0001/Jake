"""Test unitari per core/ollama_client.py: nessuna suite esisteva finora, nonostante sia il client
HTTP condiviso usato da core/nlu/llm_classifier.py (il classificatore di intent, sul percorso
critico di OGNI comando vocale), core/jake_core.py, core/skill_forge.py, core/skill_registry.py e
skills/chitchat.py. urllib.request.urlopen e' sempre mockato.

F1: buco reale sistemico corretto in questa sessione, PIU' fondamentale delle singole skill gia'
corrette per lo stesso pattern - _post/_get restituivano json.loads(...) senza validare che fosse
un dizionario: un corpo JSON valido ma non nella forma attesa faceva sollevare AttributeError da
chat_text/embed/list_models (tutti chiamano subito payload.get(...)), MAI catturato dai chiamanti,
che si aspettano solo OllamaError. Corretto validando la forma dentro _post/_get stessi (cosi' la
protezione si applica a ogni chiamante attuale e futuro in un colpo solo), piu' due ulteriori
varianti trovate a valle: chat_text quando "message" non e' un dizionario, e list_models quando
"models" non e' una lista o contiene voci non-dizionario."""
import json
import unittest
from unittest import mock
from urllib import error

from core.ollama_client import OllamaClient, OllamaResponseError, OllamaUnavailable


def _fake_response(body_bytes: bytes) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = body_bytes
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _fake_json_response(payload) -> mock.MagicMock:
    return _fake_response(json.dumps(payload).encode("utf-8"))


class PostGetShapeValidationTests(unittest.TestCase):
    def test_malformed_json_root_raises_ollama_response_error(self):
        """Il buco reale trovato e corretto in questa sessione."""
        client = OllamaClient()
        for body in (b"null", b"[]", b"42"):
            with mock.patch("urllib.request.urlopen", return_value=_fake_response(body)):
                with self.assertRaises(OllamaResponseError, msg=body):
                    client._post("/api/chat", {})
            with mock.patch("urllib.request.urlopen", return_value=_fake_response(body)):
                with self.assertRaises(OllamaResponseError, msg=body):
                    client._get("/api/tags")

    def test_a_valid_dict_response_passes_through(self):
        client = OllamaClient()
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response({"ok": True})):
            self.assertEqual(client._post("/api/chat", {}), {"ok": True})

    def test_connection_failure_raises_ollama_unavailable(self):
        client = OllamaClient()
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
            with self.assertRaises(OllamaUnavailable):
                client._post("/api/chat", {})


class ChatTextTests(unittest.TestCase):
    def test_a_successful_response_returns_the_text(self):
        client = OllamaClient()
        payload = {"message": {"content": "ciao!"}}
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
            self.assertEqual(client.chat_text("m", [{"role": "user", "content": "hi"}]), "ciao!")

    def test_connection_failure_returns_none(self):
        client = OllamaClient()
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
            self.assertIsNone(client.chat_text("m", [{"role": "user", "content": "hi"}]))

    def test_malformed_root_returns_none_instead_of_crashing(self):
        client = OllamaClient()
        for body in (b"null", b"[]", b"42"):
            with mock.patch("urllib.request.urlopen", return_value=_fake_response(body)):
                self.assertIsNone(client.chat_text("m", [{"role": "user", "content": "hi"}]), msg=body)

    def test_malformed_message_field_returns_none_instead_of_crashing(self):
        client = OllamaClient()
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response({"message": "boh"})):
            self.assertIsNone(client.chat_text("m", [{"role": "user", "content": "hi"}]))

    def test_empty_content_returns_none(self):
        client = OllamaClient()
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response({"message": {"content": "   "}})):
            self.assertIsNone(client.chat_text("m", [{"role": "user", "content": "hi"}]))


class EmbedTests(unittest.TestCase):
    def test_no_inputs_returns_empty_list_without_a_request(self):
        client = OllamaClient()
        with mock.patch("urllib.request.urlopen") as urlopen:
            self.assertEqual(client.embed("m", []), [])
        urlopen.assert_not_called()

    def test_a_successful_response_returns_embeddings(self):
        client = OllamaClient()
        payload = {"embeddings": [[0.1, 0.2], [0.3, 0.4]]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
            self.assertEqual(client.embed("m", ["a", "b"]), [[0.1, 0.2], [0.3, 0.4]])

    def test_mismatched_length_returns_none(self):
        client = OllamaClient()
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response({"embeddings": [[0.1]]})):
            self.assertIsNone(client.embed("m", ["a", "b"]))

    def test_malformed_root_returns_none_instead_of_crashing(self):
        client = OllamaClient()
        for body in (b"null", b"[]", b"42"):
            with mock.patch("urllib.request.urlopen", return_value=_fake_response(body)):
                self.assertIsNone(client.embed("m", ["a"]), msg=body)


class ListModelsTests(unittest.TestCase):
    def test_a_successful_response_returns_model_names(self):
        client = OllamaClient()
        payload = {"models": [{"name": "qwen2.5:7b"}, {"name": "nomic-embed-text"}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
            self.assertEqual(client.list_models(), ["qwen2.5:7b", "nomic-embed-text"])

    def test_connection_failure_returns_none(self):
        client = OllamaClient()
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
            self.assertIsNone(client.list_models())

    def test_malformed_root_returns_none_instead_of_crashing(self):
        client = OllamaClient()
        for body in (b"null", b"[]", b"42"):
            with mock.patch("urllib.request.urlopen", return_value=_fake_response(body)):
                self.assertIsNone(client.list_models(), msg=body)

    def test_models_field_not_a_list_returns_none_instead_of_crashing(self):
        client = OllamaClient()
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response({"models": "boh"})):
            self.assertIsNone(client.list_models())

    def test_malformed_entries_are_skipped_instead_of_crashing(self):
        client = OllamaClient()
        payload = {"models": [1, "boh", {"name": "qwen2.5:7b"}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
            self.assertEqual(client.list_models(), ["qwen2.5:7b"])

    def test_is_available_reflects_list_models(self):
        client = OllamaClient()
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response({"models": []})):
            self.assertTrue(client.is_available())
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
            self.assertFalse(client.is_available())


class HasModelAndPickModelTests(unittest.TestCase):
    def test_has_model_matches_exact_or_bare_tag(self):
        client = OllamaClient()
        payload = {"models": [{"name": "qwen2.5:7b"}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
            self.assertTrue(client.has_model("qwen2.5:7b"))
            self.assertTrue(client.has_model("qwen2.5"))
            self.assertFalse(client.has_model("llama3"))

    def test_pick_model_falls_back_when_ollama_unavailable(self):
        client = OllamaClient()
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
            self.assertEqual(client.pick_model("qwen2.5-coder", "qwen2.5:7b"), "qwen2.5:7b")

    def test_pick_model_prefers_the_preferred_model_when_installed(self):
        client = OllamaClient()
        payload = {"models": [{"name": "qwen2.5-coder:7b"}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
            self.assertEqual(client.pick_model("qwen2.5-coder", "qwen2.5:7b"), "qwen2.5-coder")


if __name__ == "__main__":
    unittest.main()
