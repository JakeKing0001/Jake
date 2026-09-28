"""L'HUD nativo e' una superficie di questo PC: conferme e correzioni valgono fra voce e HUD.

Bug reale: i comandi scritti nella barra dell'HUD arrivano dal server companion con il device_id dell'HUD
(`native-hud-local`), che aveva un canale di conversazione suo. La carta di conferma dell'HUD diceva "rispondi si' o
no", ma il "si'" scritto li' non trovava la domanda fatta a voce e diventava un comando nuovo; e una conferma chiesta
da un comando scritto nell'HUD non si confermava a voce. Server companion vero, JakeCore con la pipeline reale."""
from core.agent import AgentOutcome
from core.companion_server import CompanionServer
from core.request_context import (
    NATIVE_HUD_DEVICE_ID, current_conversation_channel, reset_current_device_id, set_current_device_id,
)
from core.skill_result import SkillResult
from tests.test_companion_approvals import ApprovalEndToEndTestCase, _post, _StampingOrchestrator, step
from tests.test_jake_core_pipeline import FakeRegistry, FakeSkill

REQUEST = "cancella il file vecchio"


class NativeHudChannelTests(ApprovalEndToEndTestCase):
    def _start(self):
        self.skill = FakeSkill(SkillResult(success=True, data={}))
        core = self._core(
            orchestrator=_StampingOrchestrator(lambda trace_id: AgentOutcome(
                trace_id=trace_id, request=REQUEST, agent_name="general", steps=[step("OPEN_APP")],
                pending_confirmation={"intent": "FAKE", "parameters": {}, "message": "Confermi la cancellazione?"},
            )),
            skill_registry=FakeRegistry({"FAKE": self.skill}), device_credential_store=self.store,
        )
        server = CompanionServer(command_handler=core.answer, credential_store=self.store,
                                 conversation_state=core.conversation_state, event_bus=core.event_bus)
        server.start()
        self.addCleanup(server.stop)
        self.hud = self.store.issue_credential(NATIVE_HUD_DEVICE_ID)
        return core, f"http://127.0.0.1:{server.port}"

    def _type_in_the_hud(self, base: str, text: str) -> str:
        status, body = _post(f"{base}/command", {"text": text}, headers={"Authorization": f"Bearer {self.hud.token}"})
        self.assertEqual(status, 200, body)
        return body["response"]

    def test_a_question_asked_by_voice_is_confirmed_by_typing_yes_in_the_hud(self):
        core, base = self._start()
        self.assertEqual(core.answer(REQUEST), "Confermi la cancellazione?")   # voce/CLI del PC

        self._type_in_the_hud(base, "sì")

        self.assertEqual(len(self.skill.calls), 1)
        self.assertFalse(core.conversation_state.has_pending_action(), "la domanda e' stata consumata, non duplicata")

    def test_a_question_asked_by_a_command_typed_in_the_hud_is_confirmed_by_voice(self):
        core, base = self._start()
        self.assertEqual(self._type_in_the_hud(base, REQUEST), "Confermi la cancellazione?")

        core.answer("sì")   # a voce, sul PC

        self.assertEqual(len(self.skill.calls), 1)

    def test_a_paired_phone_keeps_its_own_channel(self):
        token = set_current_device_id(NATIVE_HUD_DEVICE_ID)
        try:
            self.assertIsNone(current_conversation_channel())
        finally:
            reset_current_device_id(token)
        token = set_current_device_id("phone-1")
        try:
            self.assertEqual(current_conversation_channel(), "phone-1")
        finally:
            reset_current_device_id(token)
