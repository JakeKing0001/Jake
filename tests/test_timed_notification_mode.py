"""F6.5.6: "non disturbarmi per mezz'ora" - finito il tempo si torna da soli alla modalita' di prima (solo se
l'utente non ne ha scelta un'altra) e cio' che era stato trattenuto arriva in un riepilogo dalla pipeline unica.
NotificationCenter reale; il timer e' finto per non aspettare davvero."""
import unittest
from datetime import datetime
from unittest import mock

from core.notification_center import NotificationCenter, NotificationMode
from core.response_formatter import format_skill_result
from skills.notification_mode import SetNotificationModeSkill


class _ManualTimer:
    created: list = []

    def __init__(self, interval, function, args=()):
        self.interval, self.function, self.args, self.cancelled = interval, function, args, False
        _ManualTimer.created.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.function(*self.args)


class TimedNotificationModeTests(unittest.TestCase):
    def setUp(self):
        from core.jake_core import JakeCore

        _ManualTimer.created = []
        self.center = NotificationCenter()
        self.core = JakeCore.__new__(JakeCore)
        self.core.present_notification = mock.MagicMock()
        self.skill = SetNotificationModeSkill(self.center, on_restored=self.core._timed_notification_mode_ended,
                                              timer_factory=_ManualTimer, clock=lambda: datetime(2026, 9, 27, 18, 0))

    def test_the_mode_ends_by_itself_and_what_was_held_back_arrives(self):
        result = self.skill.execute({"mode": "do_not_disturb", "minutes": 30})
        self.assertEqual(format_skill_result("SET_NOTIFICATION_MODE", result), "Modalità non disturbare fino alle 18:30 attiva.")
        self.assertEqual(_ManualTimer.created[0].interval, 1800)
        self.assertIsNone(self.center.gate("advisory", "Batteria al 15%"), "trattenuta durante il non disturbare")

        _ManualTimer.created[0].fire()
        self.assertEqual(self.center.mode, NotificationMode.NORMAL)
        kind, message = self.core.present_notification.call_args.args
        self.assertEqual(kind, "reminder")
        self.assertEqual(message, "Tempo scaduto: torno alla modalità normale. Nel frattempo: Batteria al 15%")

    def test_a_choice_made_meanwhile_is_never_overridden(self):
        self.skill.execute({"mode": "do_not_disturb", "minutes": 30})
        self.skill.execute({"mode": "meeting"})
        self.assertTrue(_ManualTimer.created[0].cancelled, "la nuova scelta annulla il ritorno automatico")
        _ManualTimer.created[0].fire()
        self.assertEqual(self.center.mode, NotificationMode.MEETING)

        self.center.set_mode(NotificationMode.NORMAL)
        self.skill.execute({"mode": "study", "minutes": 60})
        self.center.set_mode(NotificationMode.GAMING)  # cambiata senza passare dalla skill
        _ManualTimer.created[-1].fire()
        self.assertEqual(self.center.mode, NotificationMode.GAMING)
        self.core.present_notification.assert_not_called()


if __name__ == "__main__":
    unittest.main()
