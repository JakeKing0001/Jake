"""F4.5.7: "nessun contenuto sensibile nelle preview in privacy mode". Prima la modalita' privata toglieva solo
la registrazione dello scambio: la trascrizione della voce, i passi degli agenti e le notifiche arrivavano
comunque in chiaro all'HUD, al telefono e al buffer di replay. Ora escono dal bus gia' redatti e l'HUD riceve
l'indicatore. Skill reale, produttori reali, server companion reale su loopback, stream SSE letto come lo
legge l'HUD nativo."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib import request

from core.companion_server import CompanionServer
from core.event_bus import EventBus
from core.hud_protocol import PRIVATE_PLACEHOLDER, EventType, HudEvent, redact_private
from core.notification_center import NotificationCenter
from core.notification_policy import FeedbackStore, NotificationPolicy
from core.proactive_gate import ProactiveGate
from core.session_hooks import SessionHooks
from core.voice.streaming_stt import TranscriptEvent, to_hud_event
from skills.session_control import PrivateModeSkill

SECRETS = ("Mario Rossi", "referto delle analisi", "iban IT60X054", "Invia a Mario")


class PrivateModeHudTests(unittest.TestCase):
    def setUp(self):
        from core.jake_core import JakeCore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        core = JakeCore.__new__(JakeCore)
        core.event_bus = EventBus()
        core.logger = mock.MagicMock()
        core.session_hooks = SessionHooks()
        core.notification_center = NotificationCenter()
        core.notification_policy = NotificationPolicy(feedback=FeedbackStore(Path(tmp.name) / "feedback.json"))
        core.proactive_gate = ProactiveGate(dedup_window_s=0, hourly_budget=100, feedback=core.notification_policy.feedback)
        core.last_notification = None
        self.core = core
        self.server = CompanionServer(event_bus=core.event_bus, command_handler=lambda text: "ok")
        self.server.start()
        self.addCleanup(self.server.stop)

    def _publish_content(self):
        """Tre produttori reali: la voce (trascrizione), un agente (passo), una notifica proattiva."""
        self.core.event_bus.publish(to_hud_event(TranscriptEvent("u1", 1, "final", "manda il referto delle analisi a Mario Rossi",
                                                                 "manda il referto delle analisi a Mario Rossi", 0.9)))
        self.core._on_agent_step(2, "Invia a Mario il file iban IT60X054")
        self.assertIsNotNone(self.core.notify("advisory", "Mario Rossi ti ha scritto"))

    def _stream(self, after: int, count: int) -> list[dict]:
        """Eventi dopo `after` letti dal replay SSE del server, come fa l'HUD dopo un reconnect."""
        events: list[dict] = []

        def read():
            req = request.Request(f"http://127.0.0.1:{self.server.port}/events", headers={"Last-Event-ID": str(after)})
            with request.urlopen(req, timeout=5) as response:
                while len(events) < count:
                    line = response.readline().decode("utf-8")
                    if not line:
                        return
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: "):]))

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(6)
        return events

    def test_in_private_mode_nothing_readable_leaves_jake_and_the_hud_is_told(self):
        self.core.event_bus.publish(HudEvent(EventType.IDLE))  # sequence 1: punto di ripresa del replay
        self.assertTrue(PrivateModeSkill(self.core).execute({"enabled": True}).success)
        self._publish_content()

        events = self._stream(after=1, count=4)
        self.assertEqual([e["type"] for e in events], ["PRIVACY_MODE", "TRANSCRIPT", "AGENT_STEP", "NOTIFICATION"])
        self.assertEqual(events[0]["payload"], {"enabled": True})
        raw = json.dumps(events, ensure_ascii=False)
        for secret in SECRETS:
            self.assertNotIn(secret, raw)
        transcript, step, notification = (e["payload"] for e in events[1:])
        # i metadati che servono all'HUD restano: tipo, revisione, passo, tipo di notifica
        self.assertEqual((transcript["kind"], transcript["revision"], transcript["text"]), ("final", 1, PRIVATE_PLACEHOLDER))
        self.assertEqual((step["step"], step["description"]), (2, PRIVATE_PLACEHOLDER))
        self.assertEqual((notification["kind"], notification["text"]), ("advisory", PRIVATE_PLACEHOLDER))
        self.assertTrue(all(e["payload"].get("private") for e in events[1:]))

    def test_turning_it_off_restores_content_but_never_unredacts_the_past(self):
        self.core.event_bus.publish(HudEvent(EventType.IDLE))
        PrivateModeSkill(self.core).execute({"enabled": True})
        self._publish_content()
        PrivateModeSkill(self.core).execute({"enabled": False})
        self.core._on_agent_step(3, "Apro il documento di Mario Rossi")

        events = self._stream(after=1, count=6)
        self.assertEqual(events[4]["payload"], {"enabled": False})
        self.assertEqual(events[5]["payload"]["description"], "Apro il documento di Mario Rossi")
        self.assertNotIn("iban IT60X054", json.dumps(events[:5], ensure_ascii=False), "il replay resta redatto")

    def test_enabling_twice_announces_once_and_the_producer_payload_is_untouched(self):
        announced = self.core.event_bus.subscribe()
        PrivateModeSkill(self.core).execute({"enabled": True})
        PrivateModeSkill(self.core).execute({"enabled": True})
        self.assertEqual(announced.get_nowait().type, EventType.PRIVACY_MODE)
        self.assertTrue(announced.empty())

        payload = {"text": "Mario Rossi", "empty": "", "nested": {"parameters": {"to": "mario@example.com"}}}
        redacted = redact_private(HudEvent(EventType.USER_MESSAGE, payload))
        self.assertEqual(payload["text"], "Mario Rossi", "l'originale del produttore non si tocca")
        self.assertEqual(redacted.payload, {"text": PRIVATE_PLACEHOLDER, "empty": "", "nested": {"parameters": None},
                                            "private": True})


if __name__ == "__main__":
    unittest.main()
