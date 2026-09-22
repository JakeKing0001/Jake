"""Integrazione end-to-end F6.3 (Notification Intelligence) + F6.7 (Task Monitor) -> JakeCore/EventBus/HUD.

A differenza di tests/test_task_notification_bridge.py (il ponte da solo) e tests/test_notification_policy.py/
tests/test_task_monitor.py (i due sistemi da soli), questo file guida `JakeCore._run_agent()` VERO (lo stesso
metodo che companion_server/voce chiamano davvero) attraverso scenari realistici - un compito che incontra una
conferma rischiosa a meta' strada, una domanda di chiarimento, un completamento pulito - e verifica cio' che
esce dall'ALTRO capo: l'evento pubblicato su un vero EventBus, con task/session/device id, le azioni gia'
eseguite e la decisione richiesta, e lo stato del vero TaskMonitorRegistry/MonitorStore su disco. Nessun doppio
per i tre sistemi collegati: solo l'orchestratore/gli agenti restano finti (non e' questo il collegamento sotto
test - vedi tests/test_agent.py per quello)."""
import unittest

from core.agent import AgentOutcome, AgentStep
from core.hud_protocol import EventType
from core.notification_center import NotificationMode
from core.request_context import (
    reset_current_device_id, reset_current_session_id, set_current_device_id, set_current_session_id,
)
from core.skill_result import SkillResult
from core.task_monitor import MonitorStore, TaskStatus, UnknownTaskError
from tests.test_jake_core_pipeline import FakeOrchestrator, _JakeCoreTestCase


def step(intent, success=True) -> AgentStep:
    return AgentStep(intent=intent, parameters={}, thought="", result=SkillResult(success=success, data={}))


class TaskNotificationEndToEndTests(_JakeCoreTestCase):
    def setUp(self):
        super().setUp()
        self.device_token = set_current_device_id("phone-1")
        self.session_token = set_current_session_id("sess-42")
        self.addCleanup(self._reset_context)

    def _reset_context(self):
        reset_current_device_id(self.device_token)
        reset_current_session_id(self.session_token)

    def _set_context(self, device_id, session_id):
        """Sovrascrive il contesto per-thread per IL RESTO del test: il cleanup registrato in setUp resetta
        sempre il token PIU' RECENTE (letto da self.device_token/self.session_token a fine test, non quello
        catturato subito), cosi' una singola richiesta di reset resta valida anche dopo questa sostituzione."""
        self.device_token = set_current_device_id(device_id)
        self.session_token = set_current_session_id(session_id)

    def notification_events(self, subscriber) -> list:
        events = []
        while True:
            try:
                events.append(subscriber.get_nowait())
            except Exception:
                break
        return [e for e in events if e.type == EventType.NOTIFICATION]

    def test_a_risky_pending_confirmation_reaches_the_bus_with_full_context_and_interrupts_now(self):
        """Lo scenario del task: apre un'app (riuscito), poi legge un file (riuscito), poi prova a
        cancellarlo e Jake chiede conferma - un evento davvero importante A META' di un compito."""
        outcome = AgentOutcome(
            trace_id="task-1", request="pulisci i download vecchi", agent_name="general",
            steps=[step("OPEN_APP"), step("READ_FILE_TEXT")],
            pending_confirmation={
                "intent": "DELETE_PATH", "parameters": {"path": "C:\\Download\\vecchio.zip"},
                "message": "Confermi la cancellazione di vecchio.zip?", "policy_reason": "always_confirm_intents",
            },
        )
        core = self._core(orchestrator=FakeOrchestrator(outcome))
        core.notification_center.mode = NotificationMode.DO_NOT_DISTURB  # anche qui un evento critico deve interrompere
        subscriber = core.event_bus.subscribe()
        core._on_agent_step_completed(outcome)  # cio' che TaskAgent.run() avrebbe gia' chiamato dopo i due passi riusciti

        response = core._run_agent("pulisci i download vecchi")

        self.assertEqual(response, "Confermi la cancellazione di vecchio.zip?")
        events = self.notification_events(subscriber)
        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(events[0].trace_id, "task-1")
        self.assertEqual(payload["task_id"], "task-1")
        self.assertEqual(payload["session_id"], "sess-42")
        self.assertEqual(payload["device_id"], "phone-1")
        self.assertEqual(payload["status"], "needs_decision")
        self.assertEqual(payload["actions_done"], [{"intent": "OPEN_APP", "success": True},
                                                    {"intent": "READ_FILE_TEXT", "success": True}])
        self.assertEqual(payload["decision_required"]["intent"], "DELETE_PATH")
        self.assertEqual(payload["decision_required"]["policy_reason"], "always_confirm_intents")
        self.assertEqual(payload["decision"]["action"], "deliver_now")
        self.assertIn("evento critico", payload["decision"]["reason"])
        # Il compito resta aperto in attesa della risposta dell'utente - non chiuso ne' perso.
        self.assertEqual(core.task_monitor.get("task-1").status, TaskStatus.NEEDS_DECISION)

    def test_a_low_risk_decision_in_meeting_mode_is_queued_not_interrupting(self):
        outcome = AgentOutcome(
            trace_id="task-2", request="che tempo fa domani", agent_name="general", steps=[],
            pending_confirmation={"intent": "GET_WEATHER", "parameters": {}, "message": "Serve conferma?"},
        )
        core = self._core(orchestrator=FakeOrchestrator(outcome))
        core.notification_center.mode = NotificationMode.MEETING
        subscriber = core.event_bus.subscribe()

        core._run_agent("che tempo fa domani")

        events = self.notification_events(subscriber)
        self.assertEqual(events[0].payload["decision"]["action"], "queue")

    def test_a_clarifying_question_also_publishes_a_contextual_decision_required_event(self):
        outcome = AgentOutcome(trace_id="task-3", request="cancella il file", agent_name="general", steps=[],
                               question="Quale file, di preciso?")
        core = self._core(orchestrator=FakeOrchestrator(outcome))
        subscriber = core.event_bus.subscribe()

        core._run_agent("cancella il file")

        events = self.notification_events(subscriber)
        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(payload["message"], "Quale file, di preciso?")
        self.assertIsNone(payload["decision_required"]["intent"])
        self.assertEqual(core.task_monitor.get("task-3").status, TaskStatus.NEEDS_DECISION)

    def test_a_clean_completion_closes_the_task_and_publishes_a_low_priority_completed_event(self):
        outcome = AgentOutcome(trace_id="task-4", request="apri chrome", agent_name="general",
                               steps=[step("OPEN_APP")], final_answer="Ho aperto Chrome.")
        core = self._core(orchestrator=FakeOrchestrator(outcome))
        subscriber = core.event_bus.subscribe()
        core._on_agent_step_completed(outcome)  # cio' che TaskAgent.run() avrebbe gia' chiamato dopo il passo riuscito

        response = core._run_agent("apri chrome")

        self.assertEqual(response, "Ho aperto Chrome.")
        events = self.notification_events(subscriber)
        self.assertEqual(events[-1].payload["status"], "completed")
        self.assertEqual(events[-1].payload["actions_done"], [{"intent": "OPEN_APP", "success": True}])
        with self.assertRaises(UnknownTaskError):
            core.task_monitor.get("task-4")  # chiuso: F6.7.7, un compito concluso non resta "attivo"

    def test_a_task_that_never_runs_a_single_step_never_touches_the_monitor(self):
        """Un turno breve (nessun passo eseguito, es. una risposta immediata dell'agente) non deve aprire un
        monitor ne' pubblicare un evento inutile - solo un compito che ha davvero fatto qualcosa conta."""
        outcome = AgentOutcome(trace_id="task-5", request="ciao", agent_name="general", steps=[],
                               final_answer="Ciao!")
        core = self._core(orchestrator=FakeOrchestrator(outcome))
        subscriber = core.event_bus.subscribe()

        core._run_agent("ciao")

        self.assertEqual(self.notification_events(subscriber), [])
        with self.assertRaises(UnknownTaskError):
            core.task_monitor.get("task-5")

    def test_the_task_monitor_store_persists_a_still_running_task_across_a_restart(self):
        """F6.7.7 collegato per davvero: un compito ancora in corso quando Jake si e' fermato in modo anomalo
        (qui simulato: nessuna risposta finale arriva mai) sopravvive su disco per un futuro lettore - senza
        alcuna ripresa automatica (si legge lo stesso file da un MonitorStore appena costruito)."""
        outcome = AgentOutcome(trace_id="task-6", request="indicizza i documenti", agent_name="general",
                               steps=[step("BUILD_SEMANTIC_INDEX")])
        core = self._core(orchestrator=FakeOrchestrator(outcome))
        # Un passo completato aggiorna il monitor E lo store (vedi _on_agent_step_completed), senza che il
        # compito arrivi mai a una conclusione - esattamente cio' che un crash a meta' produrrebbe.
        core._on_agent_step_completed(outcome)

        reopened = MonitorStore(path=core.task_monitor_store.path)
        candidates = reopened.resume_candidates()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["task_id"], "task-6")
        self.assertEqual(candidates[0]["status"], "running")

    def test_device_and_session_id_come_from_the_real_request_context_not_a_hardcoded_default(self):
        """Un secondo dispositivo, senza sessione companion (es. il canale voce locale): il payload deve
        riflettere ESATTAMENTE quel contesto, non quello del test precedente ne' un valore fisso."""
        self._set_context(device_id=None, session_id=None)

        outcome = AgentOutcome(trace_id="task-7", request="spegni il pc", agent_name="general", steps=[],
                               pending_confirmation={"intent": "SHUTDOWN_PC", "parameters": {}, "message": "Confermi?"})
        core = self._core(orchestrator=FakeOrchestrator(outcome))
        subscriber = core.event_bus.subscribe()

        core._run_agent("spegni il pc")

        events = self.notification_events(subscriber)
        self.assertIsNone(events[0].payload["session_id"])
        self.assertIsNone(events[0].payload["device_id"])

    def test_a_bridge_failure_never_breaks_the_users_answer(self):
        """Il ponte notifiche/monitor e' un canale ACCESSORIO: un suo errore non deve mai impedire a Jake di
        rispondere all'utente (stesso principio gia' applicato al salvataggio del checkpoint)."""
        def boom(*args, **kwargs):
            raise RuntimeError("bus rotto")
        outcome = AgentOutcome(trace_id="task-8", request="apri chrome", agent_name="general",
                               steps=[step("OPEN_APP")], final_answer="Fatto.")
        core = self._core(orchestrator=FakeOrchestrator(outcome))
        core.task_bridge.finish_task = boom

        response = core._run_agent("apri chrome")

        self.assertEqual(response, "Fatto.")
        self.assertTrue(core.logger.exceptions)


if __name__ == "__main__":
    unittest.main()
