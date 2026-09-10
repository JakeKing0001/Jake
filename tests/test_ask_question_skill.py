"""Test unitari per skills/ask_question.py: nessuna suite esisteva finora. urllib.request.urlopen
e' sempre mockato, nessuna vera chiamata a Ollama.

F1: buco reale corretto in questa sessione (insieme a 5 skill con lo stesso pattern
copiaincollato, e a core/vision_provider.py in precedenza). Un corpo JSON valido ma non nella
forma attesa faceva sollevare un TypeError mai catturato invece di degradare a None."""
import json
import unittest
from unittest import mock
from urllib import error

from skills.ask_question import AskQuestionSkill


def _fake_response(body_bytes: bytes) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = body_bytes
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _fake_json_response(payload) -> mock.MagicMock:
    return _fake_response(json.dumps(payload).encode("utf-8"))


class MissingParametersTests(unittest.TestCase):
    def test_missing_question_fails(self):
        result = AskQuestionSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")


class SuccessTests(unittest.TestCase):
    def test_a_successful_response_returns_the_answer(self):
        with mock.patch(
            "urllib.request.urlopen", return_value=_fake_json_response({"message": {"content": "Parigi."}}),
        ):
            result = AskQuestionSkill().execute({"question": "qual e' la capitale della Francia?"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["answer"], "Parigi.")

    def test_recent_history_is_included_in_the_request(self):
        conversation_state = mock.MagicMock()
        conversation_state.get_short_term_history.return_value = [
            {"role": "user", "text": "ciao"}, {"role": "jake", "text": "ciao a te"},
        ]
        with mock.patch(
            "urllib.request.urlopen", return_value=_fake_json_response({"message": {"content": "ok"}}),
        ) as urlopen:
            AskQuestionSkill(conversation_state=conversation_state).execute({"question": "come stai?"})
        sent_request = urlopen.call_args[0][0]
        body = json.loads(sent_request.data.decode("utf-8"))
        contents = [m["content"] for m in body["messages"]]
        self.assertIn("ciao", contents)
        self.assertIn("ciao a te", contents)


class DegradesGracefullyTests(unittest.TestCase):
    def test_connection_failure_reports_ollama_unavailable(self):
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
            result = AskQuestionSkill().execute({"question": "ciao"})
        self.assertEqual(result.error, "OLLAMA_UNAVAILABLE")

    def test_malformed_json_root_does_not_crash(self):
        """Il buco reale trovato e corretto in questa sessione."""
        for body in (b"null", b"[]", b"42", b'{"message": null}'):
            with mock.patch("urllib.request.urlopen", return_value=_fake_response(body)):
                result = AskQuestionSkill().execute({"question": "ciao"})
            self.assertEqual(result.error, "OLLAMA_UNAVAILABLE", msg=body)


if __name__ == "__main__":
    unittest.main()
