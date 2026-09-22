"""Companion Mobile MVP: notifiche proattive con task_id/session_id -> approve/deny sullo STESSO task.

`POST /approvals/<task_id>` non e' un secondo motore di esecuzione: risolve la STESSA decisione in sospeso
(F6.3/F6.7, `core/task_notification_bridge.py`) attraverso l'ESATTA pipeline di conferma gia' esistente
(`JakeCore.answer` -> `_process` -> `_handle_confirmation`) - "approve"/"deny" diventano "si'"/"no" sullo stesso
canale/dispositivo autenticato, mai una nuova conversazione. Server companion vero, `JakeCore` "spoglio" con la
pipeline reale (F6 incluso, tramite `_bare_core`).

`_StampingOrchestrator` (a differenza del `FakeOrchestrator` condiviso, che IGNORA il trace_id con cui viene
chiamato) rispecchia il comportamento REALE di `TaskAgent.run()`: l'outcome restituito porta SEMPRE il trace_id
generato da `_run_agent` per QUESTA chiamata - lo stesso che finisce nel `pending_action`. Senza questo, il
task_id che il ponte F6 traccia (`outcome.trace_id`, fisso nei test) e il trace_id della conferma vera (generato
ad ogni chiamata) potrebbero non coincidere mai, un problema del doppio di test, non del codice sotto test."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from urllib import error, request

from core.agent import AgentOutcome, AgentStep
from core.companion_server import CompanionServer
from core.device_credential_store import DeviceCredentialStore
from core.hud_protocol import EventType
from core.request_context import reset_current_device_id, set_current_device_id
from core.skill_result import SkillResult
from core.task_monitor import UnknownTaskError
from tests.test_jake_core_pipeline import FakeRegistry, FakeSkill, _JakeCoreTestCase


def _post(url: str, payload: dict, headers: dict = None, timeout: float = 5) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def step(intent, success=True) -> AgentStep:
    return AgentStep(intent=intent, parameters={}, thought="", result=SkillResult(success=success, data={}))


class _StampingOrchestrator:
    def __init__(self, build_outcome):
        self.build_outcome = build_outcome  # Callable[[str], AgentOutcome]
        self.calls: list[str] = []

    def run(self, request, history=None, trace_id=None, private=False):
        self.calls.append(request)
        return self.build_outcome(trace_id)


class ApprovalEndToEndTestCase(_JakeCoreTestCase):
    def setUp(self):
        super().setUp()
        # Cartella SEPARATA da self._tmp (di _JakeCoreTestCase, rimossa nel SUO tearDown - PRIMA che gli
        # addCleanup qui sotto girino): la connessione sqlite dello store deve chiudersi PRIMA che la sua
        # cartella venga rimossa, non dopo, altrimenti Windows rifiuta la cancellazione (file ancora aperto).
        self._device_tmp = tempfile.TemporaryDirectory(prefix="jake_companion_approvals_test_")
        self.addCleanup(self._device_tmp.cleanup)  # registrato PRIMA -> gira DOPO (LIFO)
        self.store = DeviceCredentialStore(db_path=Path(self._device_tmp.name) / "devices.db")
        self.addCleanup(self.store.close)  # registrato DOPO -> gira PRIMA
        self.credential = self.store.issue_credential("phone-1")

    def _core_and_server(self, build_outcome, skills=None):
        registry = FakeRegistry(skills or {})
        core = self._core(orchestrator=_StampingOrchestrator(build_outcome), skill_registry=registry,
                          device_credential_store=self.store)
        server = CompanionServer(
            command_handler=core.answer, credential_store=self.store, conversation_state=core.conversation_state,
            event_bus=core.event_bus,
        )
        server.start()
        self.addCleanup(server.stop)
        return core, f"http://127.0.0.1:{server.port}"

    def _claim(self, base: str, name: str = "Telefono") -> str:
        status, body = _post(f"{base}/devices/phone-1/claim", {"name": name}, headers=self._auth())
        self.assertEqual(status, 200)
        return body["session_id"]

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.credential.token}"}

    def _pending_action_for_phone1(self, core):
        """Letto dal thread del TEST, non da quello di una richiesta HTTP: current_device_id() qui e' None per
        default, quindi va impostato esplicitamente com'era durante la chiamata /command originale."""
        token = set_current_device_id("phone-1")
        try:
            return core.conversation_state.get_pending_action()
        finally:
            reset_current_device_id(token)


class ApproveFlowTests(ApprovalEndToEndTestCase):
    def test_approving_via_http_resolves_the_same_task_and_executes_the_original_action(self):
        skill = FakeSkill(SkillResult(success=True, data={}))
        core, base = self._core_and_server(
            lambda trace_id: AgentOutcome(
                trace_id=trace_id, request="cancella il file vecchio", agent_name="general", steps=[step("OPEN_APP")],
                pending_confirmation={"intent": "FAKE", "parameters": {}, "message": "Confermi la cancellazione?"},
            ),
            {"FAKE": skill},
        )
        session_id = self._claim(base)
        status, body = _post(f"{base}/command", {"text": "cancella il file vecchio", "session_id": session_id},
                             headers=self._auth())
        self.assertEqual((status, body["response"]), (200, "Confermi la cancellazione?"))
        task_id = self._pending_action_for_phone1(core)["trace_id"]

        status, body = _post(f"{base}/approvals/{task_id}", {"decision": "approve", "session_id": session_id},
                             headers=self._auth())

        self.assertEqual(status, 200)
        self.assertEqual(len(skill.calls), 1, "l'azione originale (FAKE) deve essere stata eseguita per davvero")
        self.assertIsNone(self._pending_action_for_phone1(core), "la decisione e' stata consumata")

    def test_no_new_conversation_is_created_the_same_history_grows(self):
        skill = FakeSkill(SkillResult(success=True, data={}))
        core, base = self._core_and_server(
            lambda trace_id: AgentOutcome(
                trace_id=trace_id, request="fai qualcosa", agent_name="general", steps=[],
                pending_confirmation={"intent": "FAKE", "parameters": {}, "message": "Confermi?"},
            ),
            {"FAKE": skill},
        )
        session_id = self._claim(base)
        _post(f"{base}/command", {"text": "fai qualcosa", "session_id": session_id}, headers=self._auth())
        task_id = self._pending_action_for_phone1(core)["trace_id"]
        history_before = core.conversation_state.get_short_term_history()

        _post(f"{base}/approvals/{task_id}", {"decision": "approve", "session_id": session_id}, headers=self._auth())

        history_after = core.conversation_state.get_short_term_history()
        # La stessa cronologia CRESCE (un nuovo turno utente "si'" + la risposta di Jake), non viene sostituita
        # da una seconda conversazione parallela: i turni originali restano tutti li', nello stesso ordine.
        self.assertEqual(history_after[: len(history_before)], history_before)
        self.assertGreater(len(history_after), len(history_before))

    def test_denying_never_executes_the_action(self):
        skill = FakeSkill(SkillResult(success=True, data={}))
        core, base = self._core_and_server(
            lambda trace_id: AgentOutcome(
                trace_id=trace_id, request="cancella tutto", agent_name="general", steps=[],
                pending_confirmation={"intent": "FAKE", "parameters": {}, "message": "Confermi?"},
            ),
            {"FAKE": skill},
        )
        session_id = self._claim(base)
        _post(f"{base}/command", {"text": "cancella tutto", "session_id": session_id}, headers=self._auth())
        task_id = self._pending_action_for_phone1(core)["trace_id"]

        status, body = _post(f"{base}/approvals/{task_id}", {"decision": "deny", "session_id": session_id}, headers=self._auth())

        self.assertEqual(status, 200)
        self.assertEqual(skill.calls, [])
        self.assertIn("annullato", body["response"].lower())


class SafetyTests(ApprovalEndToEndTestCase):
    def test_a_wrong_task_id_is_refused_and_never_resolves_a_different_pending_decision(self):
        skill = FakeSkill(SkillResult(success=True, data={}))
        core, base = self._core_and_server(
            lambda trace_id: AgentOutcome(
                trace_id=trace_id, request="fai qualcosa", agent_name="general", steps=[],
                pending_confirmation={"intent": "FAKE", "parameters": {}, "message": "Confermi?"},
            ),
            {"FAKE": skill},
        )
        session_id = self._claim(base)
        _post(f"{base}/command", {"text": "fai qualcosa", "session_id": session_id}, headers=self._auth())

        status, body = _post(f"{base}/approvals/task-diverso-da-quello-vero", {"decision": "approve"}, headers=self._auth())

        self.assertEqual((status, body["error"]), (404, "no_matching_pending_decision"))
        self.assertEqual(skill.calls, [], "il task VERO non deve essere risolto da un id sbagliato")
        self.assertIsNotNone(self._pending_action_for_phone1(core), "resta ancora in attesa")

    def test_a_legacy_unauthenticated_caller_without_a_device_identity_is_refused(self):
        """Senza credential_store NE' token globale configurati, _authenticate() lascia passare la richiesta
        (percorso legacy, comportamento invariato) ma SENZA alcuna identita' di dispositivo - _handle_approval
        non ha modo di sapere DI CHI e' la decisione da risolvere, quindi la rifiuta esplicitamente invece di
        indovinare (mai il device_id auto-dichiarato nel body, a differenza di /command)."""
        core = self._core(orchestrator=_StampingOrchestrator(
            lambda trace_id: AgentOutcome(trace_id=trace_id, request="x", agent_name="general", steps=[]),
        ), skill_registry=FakeRegistry({}))
        server = CompanionServer(command_handler=core.answer, conversation_state=core.conversation_state)
        server.start()
        self.addCleanup(server.stop)
        base = f"http://127.0.0.1:{server.port}"

        status, body = _post(f"{base}/approvals/qualunque-task", {"decision": "approve"})

        self.assertEqual((status, body["error"]), (403, "device_identity_required"))

    def test_a_second_devices_token_cannot_see_or_resolve_the_first_devices_pending_decision(self):
        other_credential = self.store.issue_credential("phone-2")
        skill = FakeSkill(SkillResult(success=True, data={}))
        core, base = self._core_and_server(
            lambda trace_id: AgentOutcome(
                trace_id=trace_id, request="fai qualcosa", agent_name="general", steps=[],
                pending_confirmation={"intent": "FAKE", "parameters": {}, "message": "Confermi?"},
            ),
            {"FAKE": skill},
        )
        session_id = self._claim(base)
        _post(f"{base}/command", {"text": "fai qualcosa", "session_id": session_id}, headers=self._auth())
        task_id = self._pending_action_for_phone1(core)["trace_id"]

        status, body = _post(f"{base}/approvals/{task_id}", {"decision": "approve"},
                             headers={"Authorization": f"Bearer {other_credential.token}"})

        self.assertEqual((status, body["error"]), (404, "no_matching_pending_decision"))
        self.assertEqual(skill.calls, [])

    def test_no_pending_decision_at_all_is_refused(self):
        core, base = self._core_and_server(
            lambda trace_id: AgentOutcome(trace_id=trace_id, request="x", agent_name="general", steps=[]), {},
        )
        self._claim(base)
        status, body = _post(f"{base}/approvals/qualunque-task", {"decision": "approve"}, headers=self._auth())
        self.assertEqual((status, body["error"]), (404, "no_matching_pending_decision"))

    def test_an_invalid_decision_value_is_rejected(self):
        core, base = self._core_and_server(
            lambda trace_id: AgentOutcome(trace_id=trace_id, request="x", agent_name="general", steps=[]), {},
        )
        status, body = _post(f"{base}/approvals/qualunque-task", {"decision": "maybe"}, headers=self._auth())
        self.assertEqual((status, body["error"]), (400, "invalid_decision"))


class FullMobileMvpFlagshipTest(ApprovalEndToEndTestCase):
    """Lo scenario completo richiesto dal Companion Mobile MVP: connessione autenticata, chat che avvia un
    compito, una notifica SSE con lo stesso task_id/session_id che mostra le azioni gia' fatte e la decisione
    richiesta, approve via HTTP che risolve lo STESSO task senza una nuova conversazione."""

    def test_pair_claim_command_notification_approve_same_task_end_to_end(self):
        skill = FakeSkill(SkillResult(success=True, data={}))
        core, base = self._core_and_server(
            lambda trace_id: AgentOutcome(
                trace_id=trace_id, request="elimina i download vecchi", agent_name="general",
                steps=[step("OPEN_APP"), step("READ_FILE_TEXT")],
                pending_confirmation={"intent": "FAKE", "parameters": {"path": "x"}, "message": "Confermi la cancellazione?",
                                     "policy_reason": "always_confirm_intents"},
            ),
            {"FAKE": skill},
        )
        session_id = self._claim(base, name="iPhone di Davide")

        # Sottoscrive lo stream PRIMA del comando: e' cosi' che un vero companion riceverebbe la notifica.
        events_req = request.Request(f"{base}/events", headers=self._auth())
        connection = request.urlopen(events_req, timeout=5)
        self.addCleanup(connection.close)
        time.sleep(0.2)  # da' tempo al thread del server di sottoscriversi al bus prima di pubblicare (vedi
        # tests/test_companion_server.py::EventStreamTests per lo stesso identico bisogno: subscribe_with_replay()
        # gira DOPO l'invio degli header di risposta, non prima - un client che procede subito rischierebbe di
        # pubblicare mentre il server e' ancora tra le due cose).

        status, body = _post(f"{base}/command", {"text": "elimina i download vecchi", "session_id": session_id},
                             headers=self._auth())
        self.assertEqual(status, 200)

        # Legge finche' non trova l'evento NOTIFICATION del task bridge (F6).
        notification_payload = None
        for _ in range(20):
            line = connection.readline().decode("utf-8").strip()
            if line.startswith("data: "):
                event = json.loads(line[len("data: "):])
                if event["type"] == EventType.NOTIFICATION.value and event["payload"].get("origin") == "task_monitor":
                    notification_payload = event["payload"]
                    break
        self.assertIsNotNone(notification_payload, "nessun evento di notifica del task ricevuto via SSE")
        self.assertEqual(notification_payload["session_id"], session_id)
        self.assertEqual(notification_payload["actions_done"], [{"intent": "OPEN_APP", "success": True},
                                                                 {"intent": "READ_FILE_TEXT", "success": True}])
        self.assertEqual(notification_payload["decision_required"]["message"], "Confermi la cancellazione?")
        task_id = notification_payload["task_id"]
        self.assertEqual(self._pending_action_for_phone1(core)["trace_id"], task_id, "e' davvero lo STESSO task")

        # Il telefono mostra "cosa Jake stava facendo" leggendo le azioni gia' fatte, poi l'utente approva -
        # tornando ESATTAMENTE allo stesso task, con lo stesso task_id/session_id usati per aprirlo.
        status, body = _post(f"{base}/approvals/{task_id}", {"decision": "approve", "session_id": notification_payload["session_id"]},
                             headers=self._auth())

        self.assertEqual(status, 200)
        self.assertEqual(len(skill.calls), 1)
        # Il task monitor (F6.7) riflette la conclusione dello STESSO task, non uno nuovo.
        with self.assertRaises(UnknownTaskError):
            core.task_monitor.get(task_id)  # chiuso a fine esecuzione (F6.7.7)


if __name__ == "__main__":
    unittest.main()
