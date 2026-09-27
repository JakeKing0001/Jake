"""F4.6.2 ("retry sicuro") sul JakeCore vero (policy, dialogo, conferme): "riprova" ripete solo l'ultima azione
fallita e recente, dalla stessa pipeline di un comando nuovo; per un'azione che potrebbe essere stata applicata
in parte chiede prima, e non riusa le conferme del tentativo precedente."""
import tempfile
import unittest
from pathlib import Path

from core.command import Command
from core.skill_result import SkillResult
from skills.retry_last import RETRY_WINDOW_S, RetryLastActionSkill
from tests.test_jake_core_pipeline import FakeRegistry, FakeRouter, _bare_core


class _FlakySkill:
    """Fallisce le prime `failures` volte, poi riesce."""
    metadata = {"intent": "FAKE", "description": "", "parameters": {}}

    def __init__(self, failures=1, error="OPERATION_FAILED"):
        self.failures, self.error, self.calls = failures, error, []

    def execute(self, parameters=None):
        self.calls.append(dict(parameters or {}))
        if len(self.calls) <= self.failures:
            return SkillResult(success=False, data={}, error=self.error)
        return SkillResult(success=True, data={})


class RetryLastActionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.now = [1000.0]
        # LIST_PROCESSES e' READ_ONLY (sicuro da ritentare); ADD_NOTE no (un secondo appunto sarebbe un doppione)
        self.read = _FlakySkill()
        self.write = _FlakySkill()
        self.registry = FakeRegistry({"LIST_PROCESSES": self.read, "ADD_NOTE": self.write})
        self.core = _bare_core(ledger_path=Path(tmp.name) / "ledger.jsonl", skill_registry=self.registry)
        self.registry._skills["RETRY_LAST_ACTION"] = RetryLastActionSkill(self.core, clock=lambda: self.now[0])
        # i turni del dialogo usano lo stesso orologio della skill
        self.core._get_dialogue_runtime().planner._clock = lambda: self.now[0]

    def say(self, text, command):
        self.core.router = FakeRouter(command)
        return self.core.answer(text)

    def test_a_failed_safe_action_is_retried_once_through_the_pipeline(self):
        self.say("mostra i processi", Command("LIST_PROCESSES", {"name": "python"}))
        self.say("riprova", Command("RETRY_LAST_ACTION", {}))
        self.assertEqual(self.read.calls, [{"name": "python"}, {"name": "python"}])
        # riuscita: non c'e' piu' nulla da riprovare, niente effetto doppio
        reply = self.say("riprova", Command("RETRY_LAST_ACTION", {}))
        self.assertEqual(len(self.read.calls), 2)
        self.assertEqual(reply, "Non c'è nessuna azione fallita da riprovare.")

    def test_an_action_that_may_have_partially_applied_asks_first_and_forgets_old_confirmations(self):
        self.say("aggiungi un appunto", Command("ADD_NOTE", {"text": "latte", "confirmed": True}))
        question = self.say("riprova", Command("RETRY_LAST_ACTION", {}))
        self.assertIn("potrebbe essere stata applicata in parte", question)
        self.assertEqual(len(self.write.calls), 1, "nessun secondo tentativo prima del si'")
        self.core.answer("si")
        self.assertEqual(self.write.calls[1], {"text": "latte"}, "la vecchia conferma non vale per il nuovo tentativo")

    def test_nothing_to_retry_after_success_misunderstanding_or_too_late(self):
        self.read.failures = 0
        self.say("mostra i processi", Command("LIST_PROCESSES", {}))
        self.assertEqual(self.say("riprova", Command("RETRY_LAST_ACTION", {})), "Non c'è nessuna azione fallita da riprovare.")

        self.read.failures = 5
        self.say("mostra i processi", Command("LIST_PROCESSES", {}))
        self.now[0] += RETRY_WINDOW_S + 1
        self.assertIn("troppo tempo fa", self.say("riprova", Command("RETRY_LAST_ACTION", {})))
        self.assertEqual(len(self.read.calls), 2)


if __name__ == "__main__":
    unittest.main()
