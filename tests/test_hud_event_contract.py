"""F4.1.4: le STESSE fixture sono lette da unittest e dal client C++ via CTest."""
import json
import unittest
from pathlib import Path

from core.hud_protocol import EventType, HudEvent
from core.version import PROTOCOL_VERSION


FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "hud_events.json").read_text(encoding="utf-8"))


class HudEventContractTests(unittest.TestCase):
    def test_fixtures_cover_every_event_and_the_current_protocol(self):
        accepted = [json.loads(case["raw"]) for case in FIXTURES if case["accepted"]]
        self.assertEqual({event["type"] for event in accepted}, {member.value for member in EventType})
        self.assertTrue(all(event.get("schema_version", PROTOCOL_VERSION) == PROTOCOL_VERSION for event in accepted))
        self.assertEqual(len({case["name"] for case in FIXTURES}), len(FIXTURES))

    def test_validation_errors_do_not_echo_sensitive_values(self):
        for key in ("type", "schema_version", "trace_id", "sequence_id", "at"):
            raw = json.dumps({"type": "IDLE", key: {"secret-fixture-token": True}})
            with self.subTest(key=key), self.assertRaises(ValueError) as error:
                HudEvent.from_json(raw)
            self.assertNotIn("secret-fixture-token", str(error.exception))
        with self.assertRaises(ValueError) as error:
            HudEvent.from_json('{"type":"secret-fixture-token"}')
        self.assertNotIn("secret-fixture-token", str(error.exception))


def _fixture_test(case):
    def test(self):
        if not case["accepted"]:
            with self.assertRaises(ValueError):
                HudEvent.from_json(case["raw"])
            return
        event = HudEvent.from_json(case["raw"])
        original = json.loads(case["raw"])
        self.assertEqual(event.type.value, original["type"])
        self.assertEqual(event.payload, original.get("payload") or {})
        self.assertEqual(event.sequence_id, original.get("sequence_id", 0))
        self.assertEqual(event.trace_id, original.get("trace_id"))
        self.assertIsInstance(event.at, (int, float))
        self.assertEqual(HudEvent.from_json(event.to_json()), event)
    return test


for _case in FIXTURES:
    setattr(HudEventContractTests, "test_fixture_" + _case["name"].replace("-", "_"), _fixture_test(_case))
