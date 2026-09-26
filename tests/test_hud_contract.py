"""F4.1: il riduttore di riferimento (core/hud_view_state.py) supera la suite condivisa di fixture
che esegue anche il client C++ (hud/native/tests/contract_tests.cpp, nella CI del job HUD)."""
import json
import unittest
from pathlib import Path

from core.hud_protocol import PROTOCOL_VERSION, EventType, HudEvent
from core.hud_view_state import HudViewState

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hud_contract.json"


def run_scenario(scenario: dict) -> HudViewState:
    view = HudViewState()
    for step in scenario["steps"]:
        if step.get("connect"):
            view.connection_started()
        elif "event" in step:
            event = dict(step["event"])
            event.setdefault("schema_version", PROTOCOL_VERSION)
            view.apply_line(json.dumps(event))
        else:
            view.apply_line(step["line"])
    return view


class HudContractFixtureTests(unittest.TestCase):
    def test_every_scenario_matches_its_expectation(self):
        scenarios = json.loads(FIXTURES.read_text(encoding="utf-8"))["scenarios"]
        self.assertGreaterEqual(len(scenarios), 10)
        for scenario in scenarios:
            with self.subTest(scenario=scenario["name"]):
                snapshot = run_scenario(scenario).snapshot()
                for key, expected in scenario["expect"].items():
                    self.assertIn(key, snapshot, f"campo sconosciuto nella fixture: {key}")
                    self.assertEqual(snapshot[key], expected, key)

    def test_the_fixtures_cover_every_event_type(self):
        """Un tipo aggiunto a EventType senza una regola e una fixture fa fallire qui."""
        text = FIXTURES.read_text(encoding="utf-8")
        missing = [t.value for t in EventType if f'"type": "{t.value}"' not in text]
        self.assertEqual(missing, [])

    def test_real_events_from_the_core_are_understood(self):
        view = HudViewState()
        view.connection_started()
        event = HudEvent(EventType.NOTIFICATION, {"kind": "advisory", "text": "Batteria al 10%"}, sequence_id=1)
        self.assertEqual(view.apply_line(event.to_json()), "applied")
        legacy = HudEvent.from_legacy_state("speaking", "Ciao")
        legacy.sequence_id = 2
        view.apply_line(legacy.to_json())
        self.assertEqual((view.notifications, view.state), ([["advisory", "Batteria al 10%"]], "SPEAKING"))


class ConfirmationEventTests(unittest.TestCase):
    def test_a_pending_confirmation_is_published_without_its_parameters(self):
        from core.conversation_state import ConversationStateManager
        from core.event_bus import EventBus
        from core.jake_core import JakeCore

        core = JakeCore.__new__(JakeCore)
        core.event_bus = EventBus()
        subscriber = core.event_bus.subscribe()
        state = ConversationStateManager()
        state.on_pending_change = core._publish_pending_confirmation
        state.set_pending_action({"intent": "SEND_EMAIL", "parameters": {"text": "testo privato"},
                                  "reason": "confirmation_required", "trace_id": "t-1"})
        self.assertIsNotNone(state.take_pending_action())
        opened, closed = subscriber.get(timeout=1), subscriber.get(timeout=1)
        self.assertEqual((opened.type, opened.payload["pending"], opened.payload["intent"]),
                         (EventType.CONFIRMATION, True, "SEND_EMAIL"))
        self.assertEqual(opened.payload["risk"], "external_action")
        self.assertNotIn("testo privato", opened.to_json())
        self.assertEqual(opened.trace_id, "t-1")
        self.assertEqual(closed.payload, {"pending": False})


if __name__ == "__main__":
    unittest.main()
