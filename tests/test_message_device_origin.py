"""F7.4.6 ("mostrare sempre quale dispositivo sta parlando"): un turno arrivato dal telefono lo dice nei messaggi che
l'HUD del PC mostra; un turno detto o scritto al PC no. Server companion vero, JakeCore con la pipeline reale."""
from core.command import Command
from core.companion_server import CompanionServer
from core.hud_protocol import EventType
from core.skill_result import SkillResult
from tests.test_companion_approvals import ApprovalEndToEndTestCase, _post
from tests.test_jake_core_pipeline import FakeRegistry, FakeRouter, FakeSkill


class MessageDeviceOriginTests(ApprovalEndToEndTestCase):
    def _start(self):
        core = self._core(skill_registry=FakeRegistry({"GET_TIME": FakeSkill(SkillResult(success=True, data={}))}),
                          router=FakeRouter(Command("GET_TIME", {})), device_credential_store=self.store)
        server = CompanionServer(command_handler=core.answer, credential_store=self.store,
                                 conversation_state=core.conversation_state, event_bus=core.event_bus)
        server.start()
        self.addCleanup(server.stop)
        core.companion_server = server
        return core, f"http://127.0.0.1:{server.port}", core.event_bus.subscribe()

    @staticmethod
    def _messages(events) -> list:
        found = []
        while not events.empty():
            event = events.get_nowait()
            if event.type in (EventType.USER_MESSAGE, EventType.JAKE_MESSAGE):
                found.append((event.type.value, event.payload.get("device")))
        return found

    def test_a_turn_from_the_phone_carries_the_phone_name(self):
        core, base, events = self._start()
        session_id = self._claim(base, name="Telefono di Davide")

        status, _ = _post(f"{base}/command", {"text": "che ore sono", "session_id": session_id}, headers=self._auth())

        self.assertEqual(status, 200)
        self.assertEqual(self._messages(events), [("USER_MESSAGE", "Telefono di Davide"), ("JAKE_MESSAGE", "Telefono di Davide")])

    def test_a_turn_at_the_pc_carries_no_device(self):
        core, _base, events = self._start()
        core.answer("che ore sono")
        self.assertEqual(self._messages(events), [("USER_MESSAGE", None), ("JAKE_MESSAGE", None)])
