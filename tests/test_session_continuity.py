"""F7.4.8 (continuita' di sessione PC -> companion -> PC): una conferma chiesta sul PC segue chi risponde.

Prima: un compito avviato sul PC che chiedeva conferma pubblicava la notifica (con il suo task_id) anche al telefono,
ma l'approvazione dal telefono cercava la conferma nel canale del telefono e rispondeva `no_matching_pending_decision`:
la decisione restava bloccata sul PC. Ora il claim del telefono la porta con se' (stesso task_id, stessa azione,
policy ricontrollata al "si'"), il rilascio o la scadenza del lease la riportano al PC, due claim simultanei la
lasciano sempre sul dispositivo attivo e l'azione parte una volta sola. Server companion vero, JakeCore con la
pipeline reale (`_bare_core`)."""
import threading

from core.agent import AgentOutcome
from core.companion_server import CompanionServer
from core.conversation_state import ConversationStateManager
from core.device_registry import DeviceRegistry
from core.hud_protocol import EventType
from core.request_context import reset_current_device_id, set_current_device_id
from core.risk import PACKAGE_RISK, RiskLevel, register_package_risk
from core.skill_result import SkillResult
from tests.test_companion_approvals import ApprovalEndToEndTestCase, _post, _StampingOrchestrator, step
from tests.test_jake_core_pipeline import FakeRegistry, FakeSkill

REQUEST = "cancella il file vecchio"


class SessionContinuityTestCase(ApprovalEndToEndTestCase):
    def setUp(self):
        super().setUp()
        # un intent non censito vale ADMIN per risk_of (e un ADMIN non passa al telefono): qui e' un'azione distruttiva
        # dichiarata, come dal manifest di un pacchetto - il caso reale di una conferma approvata dal telefono
        register_package_risk("FAKE", RiskLevel.DESTRUCTIVE)
        self.addCleanup(PACKAGE_RISK.pop, "FAKE", None)

    def _start(self, devices: DeviceRegistry | None = None):
        self.skill = FakeSkill(SkillResult(success=True, data={}))
        core = self._core(
            orchestrator=_StampingOrchestrator(lambda trace_id: AgentOutcome(
                trace_id=trace_id, request=REQUEST, agent_name="general", steps=[step("OPEN_APP")],
                pending_confirmation={"intent": "FAKE", "parameters": {}, "message": "Confermi la cancellazione?"},
            )),
            skill_registry=FakeRegistry({"FAKE": self.skill}), device_credential_store=self.store,
        )
        server = CompanionServer(
            command_handler=core.answer, credential_store=self.store, conversation_state=core.conversation_state,
            event_bus=core.event_bus, continuity_provider=core._continuity_snapshot,
        )
        core.conversation_state.on_pending_change = core._publish_pending_confirmation   # come in JakeCore.__init__
        if devices is not None:
            server.devices = devices
        server.start()
        self.addCleanup(server.stop)
        self.events = core.event_bus.subscribe()
        return core, server, f"http://127.0.0.1:{server.port}"

    def _ask_on_the_pc(self, core) -> str:
        """Il compito parte dalla voce/CLI del PC (canale locale): Jake chiede conferma li'."""
        self.assertEqual(core.answer(REQUEST), "Confermi la cancellazione?")
        return core.conversation_state.pending_channels()[None]["trace_id"]

    def _claim_as(self, base: str, device_id: str, token: str) -> dict:
        status, body = _post(f"{base}/devices/{device_id}/claim", {"name": device_id},
                             headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(status, 200, body)
        return body

    def _events(self) -> list:
        events = []
        while not self.events.empty():
            events.append(self.events.get_nowait())
        return events


class PcToPhoneTests(SessionContinuityTestCase):
    def test_a_confirmation_asked_on_the_pc_is_approved_from_the_phone_after_the_claim(self):
        core, _server, base = self._start()
        task_id = self._ask_on_the_pc(core)

        claim = self._claim_as(base, "phone-1", self.credential.token)

        pending = claim["continuity"]["pending"]
        self.assertEqual((pending["task_id"], pending["intent"], pending["handed_over_from"]), (task_id, "FAKE", "local"))
        self.assertIn({"role": "user", "text": REQUEST}, claim["continuity"]["recent"],
                      "il telefono vede da dove si era rimasti, senza rispiegare")
        self.assertNotIn(None, core.conversation_state.pending_channels(), "sul PC non resta una seconda copia")
        events = self._events()
        handoff = [e for e in events if e.type == EventType.NOTIFICATION and e.payload.get("kind") == "handoff"]
        self.assertEqual([(e.payload["task_id"], e.payload["to"]) for e in handoff], [(task_id, "phone-1")])
        confirmations = [e.payload["pending"] for e in events if e.type == EventType.CONFIRMATION]
        self.assertEqual(confirmations[-1], False, "la carta di conferma lascia l'HUD del PC: ora decide il telefono")

        status, body = _post(f"{base}/approvals/{task_id}", {"decision": "approve", "session_id": claim["session_id"]},
                             headers=self._auth())

        self.assertEqual(status, 200, body)
        self.assertEqual(len(self.skill.calls), 1, "la stessa azione chiesta sul PC, eseguita una volta")
        self.assertEqual(core.conversation_state.pending_channels(), {})

    def test_in_private_mode_the_phone_gets_the_decision_but_not_the_conversation(self):
        core, _server, base = self._start()
        task_id = self._ask_on_the_pc(core)
        core.private_mode = True

        claim = self._claim_as(base, "phone-1", self.credential.token)

        self.assertEqual(claim["continuity"]["recent"], [])
        self.assertEqual(claim["continuity"]["pending"]["task_id"], task_id)


class PhoneBackToPcTests(SessionContinuityTestCase):
    def test_after_the_release_the_confirmation_is_back_on_the_pc_and_runs_once(self):
        core, _server, base = self._start()
        task_id = self._ask_on_the_pc(core)
        claim = self._claim_as(base, "phone-1", self.credential.token)

        status, body = _post(f"{base}/devices/phone-1/release", {}, headers=self._auth())

        self.assertEqual((status, body), (200, {"released": True}))
        back = core.conversation_state.pending_channels()
        self.assertEqual((list(back), back[None]["trace_id"]), ([None], task_id))
        events = self._events()
        self.assertIn(("phone-1", ""), [(e.payload["from"], e.payload["to"]) for e in events
                                        if e.type == EventType.DEVICE_HANDOFF])
        self.assertTrue([e for e in events if e.type == EventType.CONFIRMATION and e.payload.get("pending")],
                        "la carta di conferma ricompare nell'HUD del PC")

        core.answer("sì")
        status, _ = _post(f"{base}/approvals/{task_id}", {"decision": "approve", "session_id": claim["session_id"]},
                          headers=self._auth())

        self.assertEqual(len(self.skill.calls), 1)
        self.assertEqual(status, 404, "gia' decisa sul PC: il telefono non la esegue una seconda volta")

    def test_an_expired_lease_brings_the_confirmation_back_to_the_pc(self):
        now = [0.0]
        devices = DeviceRegistry(lease_s=10, clock=lambda: now[0])
        core, server, base = self._start(devices)
        devices._on_expired = server._active_device_expired
        task_id = self._ask_on_the_pc(core)
        self._claim_as(base, "phone-1", self.credential.token)

        now[0] = 11.0   # il telefono tace oltre il lease (rete persa, app chiusa)
        self.assertIsNone(devices.active_device_id)

        self.assertEqual(core.conversation_state.pending_channels()[None]["trace_id"], task_id)

    def test_a_reconnecting_phone_does_not_take_a_confirmation_asked_on_the_pc_meanwhile(self):
        core, _server, base = self._start()
        self._claim_as(base, "phone-1", self.credential.token)
        task_id = self._ask_on_the_pc(core)   # l'utente e' tornato al PC e parla li'

        claim = self._claim_as(base, "phone-1", self.credential.token)   # il telefono si riconnette

        self.assertIsNone(claim["continuity"]["pending"])
        self.assertEqual(core.conversation_state.pending_channels()[None]["trace_id"], task_id)


class SimultaneousClaimTests(SessionContinuityTestCase):
    def test_two_simultaneous_claims_leave_the_confirmation_with_the_active_device_and_it_runs_once(self):
        core, server, base = self._start()
        other = self.store.issue_credential("phone-2")
        task_id = self._ask_on_the_pc(core)
        tokens = {"phone-1": self.credential.token, "phone-2": other.token}
        barrier = threading.Barrier(2)
        claims: dict = {}

        def claim(device_id):
            barrier.wait()
            claims[device_id] = self._claim_as(base, device_id, tokens[device_id])

        threads = [threading.Thread(target=claim, args=(device_id,)) for device_id in tokens]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)

        active = server.devices.active_device_id
        self.assertEqual(list(core.conversation_state.pending_channels()), [active],
                         "chiunque vinca la corsa, la conferma e' sul dispositivo attivo")
        results = []

        def approve(device_id):
            barrier.wait()
            results.append(_post(f"{base}/approvals/{task_id}",
                                 {"decision": "approve", "session_id": claims[device_id]["session_id"]},
                                 headers={"Authorization": f"Bearer {tokens[device_id]}"})[0])

        threads = [threading.Thread(target=approve, args=(device_id,)) for device_id in tokens]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)

        self.assertEqual(sorted(results), [200, 404])
        self.assertEqual(len(self.skill.calls), 1)


class HandOverRulesTests(SessionContinuityTestCase):
    def _state_with(self, action: dict) -> ConversationStateManager:
        state = ConversationStateManager()
        state.set_pending_action(action)
        return state

    def test_an_authentication_request_stays_on_the_pc(self):
        state = self._state_with({"intent": "SHUTDOWN", "parameters": {}, "reason": "auth_required", "trace_id": "t"})
        self.assertIsNone(state.hand_over_pending(None, "phone-1"))
        self.assertIn(None, state.pending_channels())

    def test_approving_a_new_device_stays_on_the_pc(self):
        """Il pairing di un nuovo dispositivo (ADMIN) lo approva solo il PC: un telefono che reclama la sessione non
        deve portarsi via la domanda e autorizzare un altro dispositivo."""
        state = self._state_with({"intent": "APPROVE_PAIRING", "parameters": {"challenge_id": "c"},
                                  "reason": "confirmation_required"})
        self.assertIsNone(state.hand_over_pending(None, "phone-1"))
        self.assertEqual(state.pending_channels()[None]["intent"], "APPROVE_PAIRING")

    def test_an_old_confirmation_is_not_handed_over(self):
        state = self._state_with({"intent": "FAKE", "parameters": {}, "reason": "confirmation_required"})
        state.HANDOVER_MAX_AGE_S = -1   # piu' vecchia di qualunque limite
        self.assertIsNone(state.hand_over_pending(None, "phone-1"))

    def test_a_device_with_its_own_confirmation_keeps_it(self):
        state = self._state_with({"intent": "FAKE", "parameters": {}, "reason": "confirmation_required", "trace_id": "pc"})
        token = set_current_device_id("phone-1")
        try:
            state.set_pending_action({"intent": "OTHER", "parameters": {}, "reason": "confirmation_required",
                                      "trace_id": "phone"})
        finally:
            reset_current_device_id(token)
        self.assertIsNone(state.hand_over_pending(None, "phone-1"))
        self.assertEqual({k: v["trace_id"] for k, v in state.pending_channels().items()}, {None: "pc", "phone-1": "phone"})
