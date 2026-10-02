"""Baseline pre-sperimentazione: un turno lascia UNA riga ricostruibile in jake_actions.jsonl (stesso trace_id delle
azioni), una timeline di soli nomi di stato, e niente testo dell'utente, valori di ricordi o tracce in modalita'
privata."""
import json
import logging
import tempfile
import unittest
from pathlib import Path

from core.command import Command
from core.event_bus import EventBus
from core.hud_protocol import EventType, HudEvent, redact_private
from core.skill_result import SkillResult
from tests.test_jake_core_pipeline import FakeMemoryManager, FakeRegistry, FakeRouter, FakeSkill, _JakeCoreTestCase


class TurnDiagnosticsTests(_JakeCoreTestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log_path = Path(tmp.name) / "actions.jsonl"
        handler = logging.FileHandler(self.log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("jake.actions")
        logger.addHandler(handler)
        self.addCleanup(lambda: (logger.removeHandler(handler), handler.close()))

    def _lines(self):
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _time_core(self, **overrides):
        registry = FakeRegistry({"GET_TIME": FakeSkill(SkillResult(success=True, data={"time": "10:00"}))})
        return self._core(registry=registry, skill_registry=registry, memory_manager=FakeMemoryManager(),
                          router=FakeRouter(Command("GET_TIME", {}), route="exact"), **overrides)

    def test_one_turn_is_traceable_end_to_end_without_user_text(self):
        core = self._time_core()
        core.answer("che ore sono")
        lines = self._lines()
        turn = [line for line in lines if line.get("kind") == "turn"][-1]
        self.assertEqual((turn["route"], turn["intent"], turn["outcome"]), ("exact", "GET_TIME", "ok"))
        self.assertIn("routing", turn["timings_ms"])
        self.assertIn("total", turn["timings_ms"])
        self.assertFalse(turn["memory_used"])
        actions = [line for line in lines if line.get("skill") == "GET_TIME"]
        self.assertEqual(actions[-1]["trace_id"], turn["trace_id"])  # stessa interazione, stesso id
        self.assertEqual(core.last_trace_id, turn["trace_id"])
        self.assertNotIn("che ore sono", self.log_path.read_text(encoding="utf-8"))

    def test_private_mode_leaves_only_an_anonymous_marker(self):
        core = self._time_core(private_mode=True)
        core.answer("che ore sono")
        turns = [line for line in self._lines() if line.get("kind") == "turn"]
        self.assertEqual([set(t) for t in turns], [{"ts", "kind", "private"}])
        self.assertIsNone(core.last_trace_id)

    def test_state_timeline_records_names_only_and_nothing_while_private(self):
        core = self._time_core()
        bus = EventBus()
        bus.observer = core._record_state_event
        for event_type in (EventType.LISTENING, EventType.TRANSCRIBING, EventType.THINKING, EventType.EXECUTING):
            bus.publish(HudEvent(event_type, {}))
        bus.publish(HudEvent(EventType.JAKE_MESSAGE, {}))                       # voce in corso = SPEAKING
        bus.publish(HudEvent(EventType.JAKE_MESSAGE, {"text": "sono le 10"}))   # contenuto: mai nella timeline
        bus.publish(HudEvent(EventType.ERROR, {"detail": "segreto"}))
        bus.publish(HudEvent(EventType.IDLE, {}))
        bus.redactor = redact_private                                           # modalita' privata
        bus.publish(HudEvent(EventType.LISTENING, {}))
        states = [line["state"] for line in self._lines() if line.get("kind") == "state"]
        self.assertEqual(states, ["LISTENING", "TRANSCRIBING", "THINKING", "EXECUTING", "SPEAKING", "ERROR", "IDLE"])
        self.assertNotIn("segreto", self.log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
