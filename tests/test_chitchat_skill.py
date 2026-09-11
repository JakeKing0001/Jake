"""Test unitari per skills/chitchat.py: nessuna suite esisteva finora, nessun bug trovato. Il
client Ollama e' sempre un MagicMock (mai una vera chiamata al modello)."""
import unittest
from unittest import mock

from skills.chitchat import ChitChatSkill


class ChitChatTests(unittest.TestCase):
    def test_a_recognized_courtesy_phrase_uses_the_deterministic_reply(self):
        client = mock.MagicMock()
        result = ChitChatSkill(client=client).execute({"text": "grazie mille"})
        self.assertTrue(result.success)
        self.assertIn(result.data["reply"], ["Prego.", "Di nulla.", "Figurati.", "Quando vuoi.", "Sempre a disposizione."])
        client.chat_text.assert_not_called()

    def test_an_unrecognized_phrase_falls_back_to_the_model(self):
        client = mock.MagicMock()
        client.chat_text.return_value = "Risposta del modello."
        result = ChitChatSkill(client=client).execute({"text": "che tempo strano oggi"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["reply"], "Risposta del modello.")
        client.chat_text.assert_called_once()

    def test_model_unavailable_falls_back_to_a_default_reply(self):
        client = mock.MagicMock()
        client.chat_text.return_value = None
        result = ChitChatSkill(client=client).execute({"text": "che tempo strano oggi"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["reply"], "Ci sono. Dimmi pure.")

    def test_empty_text_does_not_call_the_model(self):
        client = mock.MagicMock()
        result = ChitChatSkill(client=client).execute({"text": ""})
        self.assertTrue(result.success)
        self.assertEqual(result.data["reply"], "Ci sono. Dimmi pure.")
        client.chat_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
