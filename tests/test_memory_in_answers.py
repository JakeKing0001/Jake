"""F5.5/F5.6: le risposte libere (ASK_QUESTION) usano i ricordi pertinenti e dichiarano la fonte.
Prima la memoria entrava solo nel comando RECALL: a "quando e' il compleanno di Giulia?" il modello rispondeva
senza ricordi. Database temporaneo, dati sintetici, modello finto."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.memory_manager import MemoryManager
from core.memory_privacy import MemoryPrivacyDashboard
from skills.ask_question import AskQuestionSkill


def _response(content):
    return json.dumps({"message": {"content": content}}).encode("utf-8")


class MemoryInAnswersTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.memory = MemoryManager(Path(tmp.name) / "memory.db")
        self.addCleanup(self.memory.close)
        self.memory.remember("compleanno di Giulia", "12 marzo", source="user")
        self.memory.remember("colore preferito", "verde", source="inferred")
        self.memory.remember("targa auto", "AB123CD", source="user")
        self.dashboard = MemoryPrivacyDashboard(self.memory)
        self.skill = AskQuestionSkill(memory_manager=self.memory, dashboard=self.dashboard)

    def _ask(self, question, content):
        sent = []

        def fake_read(request, timeout):
            sent.append(json.loads(request.data.decode("utf-8")))
            return _response(content)

        with mock.patch("skills.ask_question.read_url", side_effect=fake_read):
            result = self.skill.execute({"question": question})
        return result, sent[0]["messages"]

    def test_relevant_memories_reach_the_model_and_the_answer_cites_them(self):
        result, messages = self._ask("quando e' il compleanno di Giulia?", "Il compleanno di Giulia e' il 12 marzo [M1].")
        memory_block = " ".join(m["content"] for m in messages if m["role"] == "system")
        self.assertIn("[M1] compleanno di Giulia: 12 marzo", memory_block)
        self.assertNotIn("targa", memory_block, "solo i ricordi pertinenti, non tutta la memoria")
        answer = result.data["answer"]
        self.assertNotIn("[M1]", answer, "il marcatore non si legge ad alta voce")
        self.assertIn("(Dai miei ricordi: compleanno di Giulia (me l'hai detto tu il ", answer)
        self.assertEqual(self.dashboard.get("compleanno di Giulia").use_count, 1)

    def test_an_inference_is_never_presented_as_something_the_user_said(self):
        result, _ = self._ask("qual e' il mio colore preferito?", "Credo sia il verde.")
        self.assertIn("colore preferito (l'ho dedotto io, non me l'hai detto tu)", result.data["answer"])

    def test_no_memory_no_citation_and_private_mode_leaves_no_trace(self):
        result, messages = self._ask("spiegami la fotosintesi", "La fotosintesi trasforma la luce in energia.")
        self.assertNotIn("Dai miei ricordi", result.data["answer"])
        self.assertFalse(any("Ricordi dell'utente" in m["content"] for m in messages))
        self.skill.private_mode_provider = lambda: True
        self._ask("quando e' il compleanno di Giulia?", "Il 12 marzo.")
        self.assertEqual(self.dashboard.get("compleanno di Giulia").use_count, 0)

    def test_the_context_budget_is_respected(self):
        for n in range(20):
            self.memory.remember(f"nota compleanno {n}", "x" * 120, source="user")
        chosen = self.memory.relevant_for("compleanno", budget_chars=400)
        self.assertLessEqual(sum(len(m["key"]) + len(m["value"]) for m in chosen), 400)
        self.assertLessEqual(len(chosen), 4)


if __name__ == "__main__":
    unittest.main()
