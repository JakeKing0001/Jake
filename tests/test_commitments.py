"""F6.4.1/F6.4.2: "devo X entro <giorno>" diventa una PROPOSTA di promemoria, confermata o rifiutata con il si'/no
normale del core. JakeCore reale (policy, conferma), SetReminderSkill e ReminderManager veri su database temporaneo."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from core.command import Command
from core.commitments import detect_commitment
from core.reminder_manager import ReminderManager
from skills.reminder import SetReminderSkill
from tests.test_jake_core_pipeline import FakeRegistry, FakeRouter, _bare_core

WEDNESDAY_10 = datetime(2026, 9, 30, 10, 0)


class DetectCommitmentTests(unittest.TestCase):
    def test_only_an_explicit_deadline_is_a_commitment(self):
        found = detect_commitment("Devo mandare il preventivo a Rossi entro venerdì.", WEDNESDAY_10)
        self.assertEqual((found.what, found.remind_at), ("mandare il preventivo a Rossi", datetime(2026, 10, 2, 9, 0)))
        self.assertEqual(detect_commitment("mi sono impegnato a chiamarla entro domani", WEDNESDAY_10).remind_at,
                         datetime(2026, 10, 1, 9, 0))
        self.assertEqual(detect_commitment("devo finire entro mercoledi", WEDNESDAY_10).remind_at.date(),
                         datetime(2026, 10, 7).date(), "detto di mercoledi': il prossimo")
        self.assertIsNone(detect_commitment("devo comprare il pane", WEDNESDAY_10))
        self.assertIsNone(detect_commitment("il report va consegnato entro venerdi", WEDNESDAY_10))


class CommitmentProposalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.reminders = ReminderManager(Path(tmp.name) / "reminders.db")
        self.addCleanup(getattr(self.reminders, "close", lambda: None))
        self.core = _bare_core(ledger_path=Path(tmp.name) / "ledger.jsonl",
                               skill_registry=FakeRegistry({"SET_REMINDER": SetReminderSkill(self.reminders)}),
                               router=FakeRouter(Command("UNKNOWN", {})))
        self.core.commitment_clock = lambda: WEDNESDAY_10

    def test_yes_creates_the_reminder_for_the_deadline_morning(self):
        reply = self.core.answer("devo mandare il preventivo entro venerdì")
        self.assertTrue(reply.endswith("Vuoi che te lo ricordi venerdì alle 9?"), reply)
        self.assertEqual(self.reminders.list_upcoming(), [], "niente prima del si'")
        self.core.answer("si")
        self.assertEqual([r["text"] for r in self.reminders.list_upcoming()], ["mandare il preventivo"])

    def test_no_creates_nothing_and_private_mode_or_no_deadline_ask_nothing(self):
        self.core.answer("devo mandare il preventivo entro venerdì")
        self.core.answer("no")
        self.assertEqual(self.reminders.list_upcoming(), [])

        self.assertNotIn("Vuoi che te lo ricordi", self.core.answer("devo comprare il pane"))
        self.core.private_mode = True
        self.assertNotIn("Vuoi che te lo ricordi", self.core.answer("devo pagare la bolletta entro domani"))


if __name__ == "__main__":
    unittest.main()
