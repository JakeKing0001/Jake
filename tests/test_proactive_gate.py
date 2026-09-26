"""F6.1/F6.3: promemoria, automazioni e avvisi passano dagli stessi freni in JakeCore.notify."""
import unittest
from unittest import mock

from core.event_bus import EventBus
from core.notification_center import NotificationCenter
from core.proactive_gate import DEFER, DELIVER, DUPLICATE, ProactiveGate


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ProactiveGateTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.quiet = False
        self.busy = False
        self.gate = ProactiveGate(clock=self.clock, dedup_window_s=600, hourly_budget=3,
                                  in_quiet_hours=lambda: self.quiet, conversation_active=lambda: self.busy)

    def test_the_same_message_is_not_repeated_within_the_window(self):
        self.assertEqual(self.gate.check("advisory", "Batteria al 10%")[0], DELIVER)
        self.assertEqual(self.gate.check("advisory", "batteria  al 10%")[0], DUPLICATE)
        self.clock.now += 601
        self.assertEqual(self.gate.check("advisory", "Batteria al 10%")[0], DELIVER)

    def test_advisories_have_an_hourly_budget_and_the_excess_is_deferred(self):
        results = [self.gate.check("advisory", f"avviso {n}")[0] for n in range(4)]
        self.assertEqual(results, [DELIVER, DELIVER, DELIVER, DEFER])
        self.clock.now += 3600
        self.assertEqual(self.gate.check("trigger", "automazione")[0], DELIVER)

    def test_quiet_hours_and_an_ongoing_conversation_defer_non_reminders(self):
        self.quiet = True
        self.assertEqual(self.gate.check("advisory", "Disco quasi pieno"), (DEFER, "quiet hours"))
        self.quiet, self.busy = False, True
        self.assertEqual(self.gate.check("trigger", "Ho eseguito il backup"), (DEFER, "conversazione in corso"))

    def test_reminders_and_declared_critical_events_are_not_held_back(self):
        self.quiet = self.busy = True
        for n in range(5):
            self.assertEqual(self.gate.check("reminder", f"Promemoria {n}")[0], DELIVER)
        self.assertEqual(self.gate.check("advisory", "Temperatura CPU critica", critical=True)[0], DELIVER)
        self.assertEqual(self.gate.check("reminder", "Promemoria 0")[0], DUPLICATE)


class NotifyPipelineTests(unittest.TestCase):
    def _core(self, gate):
        from core.jake_core import JakeCore

        core = JakeCore.__new__(JakeCore)
        core.notification_center = NotificationCenter()
        core.event_bus = EventBus()
        core.logger = mock.MagicMock()
        core.proactive_gate = gate
        return core

    def test_deferred_notifications_are_queued_and_duplicates_dropped(self):
        busy = {"value": True}
        core = self._core(ProactiveGate(conversation_active=lambda: busy["value"]))
        subscriber = core.event_bus.subscribe()
        self.assertIsNone(core.notify("advisory", "Batteria al 10%"))
        self.assertEqual(core.notification_center.pending_count(), 1, "rimandata, non persa")
        self.assertTrue(subscriber.empty(), "nessuna notifica mostrata durante la conversazione")
        busy["value"] = False
        self.assertEqual(core.notify("reminder", "Promemoria: medicina"), "Promemoria: medicina")
        self.assertIsNone(core.notify("reminder", "Promemoria: medicina"))
        self.assertEqual(core.notification_center.pending_count(), 1, "il duplicato non va neanche in coda")
        self.assertEqual(subscriber.get(timeout=1).payload["text"], "Promemoria: medicina")
        self.assertTrue(subscriber.empty())


if __name__ == "__main__":
    unittest.main()
