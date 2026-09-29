"""F6.4.3/F6.4.4: un obiettivo della todo list scomposto in passi, con il prossimo passo; l'ultimo passo fatto non
chiude l'obiettivo da solo. Database vero, skill e risposte vere."""
import tempfile
import unittest
from pathlib import Path

from core.response_formatter import format_skill_result
from core.todo_manager import TodoManager
from skills.todo import CompleteTodoSkill, ListTodosSkill, NextStepSkill, PlanGoalSkill


class GoalStepsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.todos = TodoManager(Path(tmp.name) / "todos.db")
        self.addCleanup(self.todos.close)
        self.todos.add("comprare il pane")

    def _say(self, skill, parameters):
        result = skill(self.todos).execute(parameters)
        return result, format_skill_result(result_intent(skill), result)

    def test_a_goal_with_steps_tells_the_next_one_and_its_progress(self):
        result, reply = self._say(PlanGoalSkill, {"goal": "finire la tesi",
                                                  "steps": ["scrivere l'introduzione", "fare gli esperimenti"]})
        self.assertEqual(reply, "Ok, «finire la tesi» ha 2 passi. Il prossimo è: scrivere l'introduzione.")

        _, reply = self._say(CompleteTodoSkill, {"text": "introduzione"})
        self.assertIn("Per «finire la tesi» il prossimo passo è: fare gli esperimenti.", reply)
        _, reply = self._say(NextStepSkill, {"goal": "tesi"})
        self.assertEqual(reply, "Per «finire la tesi» il prossimo passo è: fare gli esperimenti (1 di 2 fatti).")
        _, reply = self._say(ListTodosSkill, {})
        self.assertIn("finire la tesi (1/2 passi, prossimo: fare gli esperimenti)", reply)
        self.assertNotIn("scrivere l'introduzione", reply, "i passi stanno sotto il loro obiettivo, non in lista")

    def test_the_last_step_does_not_close_the_goal_by_itself(self):
        self._say(PlanGoalSkill, {"goal": "trasloco", "steps": "prenotare il furgone"})

        _, reply = self._say(CompleteTodoSkill, {"text": "furgone"})

        self.assertIn("Era l'ultimo passo di «trasloco»", reply)
        self.assertIn("trasloco", [t["text"] for t in self.todos.list_pending()], "fatto e' cio' che dice l'utente")


def result_intent(skill) -> str:
    return skill.metadata["intent"]


if __name__ == "__main__":
    unittest.main()
