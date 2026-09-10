"""Test unitari per skills/research.py: nessuna suite esisteva finora. web_search_skill/
search_files_skill sono finti; urllib.request.urlopen e' sempre mockato per la sintesi finale.

F1: buco reale corretto in questa sessione (stesso pattern di skills/ask_question.py e altre 5
skill) - un corpo JSON valido ma non nella forma attesa faceva sollevare un TypeError mai
catturato invece di degradare a None."""
import json
import unittest
from unittest import mock
from urllib import error

from core.skill_result import SkillResult
from skills.research import ResearchSkill


def _fake_json_response(payload) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _skill(web_success=True, file_success=True):
    web_search_skill = mock.MagicMock()
    web_search_skill.execute.return_value = (
        SkillResult(success=True, data={"summary": "un riassunto dal web"}) if web_success
        else SkillResult(success=False, data={}, error="NOT_FOUND")
    )
    search_files_skill = mock.MagicMock()
    search_files_skill.execute.return_value = (
        SkillResult(success=True, data={"results": [{"path": "C:/doc.txt"}]}) if file_success
        else SkillResult(success=False, data={}, error="NOT_FOUND")
    )
    return ResearchSkill(web_search_skill, search_files_skill, model="qwen2.5:7b"), web_search_skill, search_files_skill


class MissingParametersTests(unittest.TestCase):
    def test_missing_topic_fails(self):
        skill, _, _ = _skill()
        self.assertEqual(skill.execute({}).error, "MISSING_PARAMETERS")


class NoSourcesTests(unittest.TestCase):
    def test_no_sources_found_at_all_reports_not_found(self):
        skill, _, _ = _skill(web_success=False, file_success=False)
        result = skill.execute({"topic": "argomento oscuro"})
        self.assertEqual(result.error, "NOT_FOUND")


class SynthesisTests(unittest.TestCase):
    def test_successful_synthesis_is_returned(self):
        skill, _, _ = _skill()
        with mock.patch(
            "urllib.request.urlopen", return_value=_fake_json_response({"message": {"content": "sintesi"}}),
        ):
            result = skill.execute({"topic": "python"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["synthesis"], "sintesi")

    def test_ollama_failure_falls_back_to_raw_sources_instead_of_failing(self):
        """Anche se la sintesi fallisce, le fonti grezze restano un risultato utile: non deve
        diventare un fallimento totale."""
        skill, _, _ = _skill()
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("boom")):
            result = skill.execute({"topic": "python"})
        self.assertTrue(result.success)
        self.assertIn("Dal web", result.data["synthesis"])

    def test_malformed_json_root_does_not_crash(self):
        """Il buco reale trovato e corretto in questa sessione."""
        skill, _, _ = _skill()
        response = mock.MagicMock()
        response.read.return_value = b"null"
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with mock.patch("urllib.request.urlopen", return_value=response):
            result = skill.execute({"topic": "python"})
        self.assertTrue(result.success)  # ripiega sulle fonti grezze, non un crash


if __name__ == "__main__":
    unittest.main()
