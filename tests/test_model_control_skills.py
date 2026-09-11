"""Test unitari per skills/model_control.py: nessuna suite esisteva finora. urllib.request.urlopen
e' sempre mockato.

F1: buco reale corretto in questa sessione (stesso pattern sistemico gia' corretto per 7 file che
parlano con Ollama direttamente) - LIST_MODELS non validava la forma della risposta prima di
chiamare payload.get("models"): un corpo JSON valido ma non un dizionario ("null", "[]", un
numero) faceva sollevare un AttributeError mai catturato, invece di degradare a
OLLAMA_UNAVAILABLE come promesso."""
import json
import unittest
from unittest import mock
from urllib import error

from skills.model_control import ListModelsSkill, SetModelSkill


def _fake_response(body_bytes: bytes) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = body_bytes
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _fake_json_response(payload) -> mock.MagicMock:
    return _fake_response(json.dumps(payload).encode("utf-8"))


class ListModelsTests(unittest.TestCase):
    def test_a_successful_response_lists_model_names(self):
        payload = {"models": [{"name": "qwen2.5:7b"}, {"name": "nomic-embed-text"}]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
            result = ListModelsSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["models"], ["qwen2.5:7b", "nomic-embed-text"])

    def test_no_models_installed_reports_not_found(self):
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response({"models": []})):
            result = ListModelsSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_connection_failure_reports_ollama_unavailable(self):
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
            result = ListModelsSkill().execute({})
        self.assertEqual(result.error, "OLLAMA_UNAVAILABLE")

    def test_invalid_json_reports_ollama_unavailable(self):
        with mock.patch("urllib.request.urlopen", return_value=_fake_response(b"non e' json")):
            result = ListModelsSkill().execute({})
        self.assertEqual(result.error, "OLLAMA_UNAVAILABLE")

    def test_malformed_json_root_does_not_crash(self):
        """Il buco reale trovato e corretto in questa sessione."""
        for body in (b"null", b"[]", b"42"):
            with mock.patch("urllib.request.urlopen", return_value=_fake_response(body)):
                result = ListModelsSkill().execute({})
            self.assertEqual(result.error, "OLLAMA_UNAVAILABLE", msg=body)

    def test_models_field_not_a_list_does_not_crash(self):
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response({"models": "boh"})):
            result = ListModelsSkill().execute({})
        self.assertEqual(result.error, "OLLAMA_UNAVAILABLE")

    def test_malformed_model_entries_are_skipped_not_a_crash(self):
        payload = {"models": [{"name": "qwen2.5:7b"}, "stringa inattesa", {"senza_name": True}, 42]}
        with mock.patch("urllib.request.urlopen", return_value=_fake_json_response(payload)):
            result = ListModelsSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["models"], ["qwen2.5:7b"])


class SetModelTests(unittest.TestCase):
    def test_missing_model_fails(self):
        result = SetModelSkill(updatable_targets=[], config=mock.MagicMock()).execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_updates_every_target_and_persists_the_choice(self):
        classifier = mock.MagicMock(model="old-model")
        planner = mock.MagicMock(model="old-model")
        config = mock.MagicMock()

        result = SetModelSkill(updatable_targets=[classifier, planner], config=config).execute({"model": "qwen2.5:7b"})

        self.assertTrue(result.success)
        self.assertEqual(classifier.model, "qwen2.5:7b")
        self.assertEqual(planner.model, "qwen2.5:7b")
        config.set.assert_called_once_with("ollama_model", "qwen2.5:7b")

    def test_no_targets_still_persists_the_choice(self):
        config = mock.MagicMock()
        result = SetModelSkill(updatable_targets=[], config=config).execute({"model": "qwen2.5:7b"})
        self.assertTrue(result.success)
        config.set.assert_called_once_with("ollama_model", "qwen2.5:7b")


if __name__ == "__main__":
    unittest.main()
