"""Test unitari per la pubblicazione di eventi da JakeCore sul bus condiviso (v4.9.1, HUD IPC
transport): _on_agent_step -> AGENT_STEP, notify() -> NOTIFICATION. Vedi tests/test_privacy.py
per answer() -> USER_MESSAGE/JAKE_MESSAGE/ERROR (li' e' gia' testata anche l'interazione con la
modalita' privata) e tests/test_companion_server.py per il trasporto in rete."""
import unittest

from core.event_bus import EventBus
from core.hud_protocol import EventType
from core.jake_core import JakeCore
from core.notification_center import NotificationCenter, NotificationMode


def _bare_core(mode=NotificationMode.NORMAL) -> JakeCore:
    core = JakeCore.__new__(JakeCore)
    core.event_bus = EventBus()
    core.notification_center = NotificationCenter(mode=mode)
    core.session_hooks = type("Hooks", (), {"call": lambda self, *a, **kw: None})()
    return core


class AgentStepEventTests(unittest.TestCase):
    def test_on_agent_step_publishes_agent_step_event(self):
        core = _bare_core()
        subscriber = core.event_bus.subscribe()

        core._on_agent_step(2, "Cerco il file tesi.pdf")

        event = subscriber.get_nowait()
        self.assertEqual(event.type, EventType.AGENT_STEP)
        self.assertEqual(event.payload, {"step": 2, "description": "Cerco il file tesi.pdf"})


class NotifyEventTests(unittest.TestCase):
    def test_allowed_notification_is_published(self):
        core = _bare_core(mode=NotificationMode.NORMAL)
        subscriber = core.event_bus.subscribe()

        message = core.notify("advisory", "batteria scarica")

        self.assertEqual(message, "batteria scarica")
        event = subscriber.get_nowait()
        self.assertEqual(event.type, EventType.NOTIFICATION)
        self.assertEqual(event.payload, {"kind": "advisory", "text": "batteria scarica"})

    def test_queued_notification_is_not_published_yet(self):
        """Se la modalita' corrente mette la notifica in coda (v4.3), non deve nemmeno
        comparire sul bus eventi: l'HUD/companion non deve vederla finche' non e' davvero
        ammessa (altrimenti la coda di NotificationCenter perderebbe senso)."""
        core = _bare_core(mode=NotificationMode.MEETING)
        subscriber = core.event_bus.subscribe()

        message = core.notify("advisory", "batteria scarica")

        self.assertIsNone(message)
        self.assertTrue(subscriber.empty())

    def test_a_trace_id_is_attached_to_the_event_when_given(self):
        """F1.7.2 ("collegare... notifica con lo stesso trace id"): un'automazione ha gia' un
        trace_id reale (PlanOutcome.trace_id) che correla ai passi gia' registrati nel ledger -
        deve arrivare fino all'evento HUD, non sparire. F4.1.1: campo di prima classe su
        HudEvent, non piu' infilato nel payload (era cosi' prima di questo incremento)."""
        core = _bare_core(mode=NotificationMode.NORMAL)
        subscriber = core.event_bus.subscribe()

        core.notify("trigger", "Ho eseguito automaticamente 'buonanotte'", trace_id="abc123")

        event = subscriber.get_nowait()
        self.assertEqual(event.trace_id, "abc123")
        self.assertNotIn("trace_id", event.payload)

    def test_trace_id_is_none_when_not_given(self):
        """Il comportamento esistente (nessun trace_id, es. un promemoria o un avviso senza
        un'esecuzione da correlare) non deve cambiare: nessun valore inventato."""
        core = _bare_core(mode=NotificationMode.NORMAL)
        subscriber = core.event_bus.subscribe()

        core.notify("advisory", "batteria scarica")

        event = subscriber.get_nowait()
        self.assertIsNone(event.trace_id)


class EffectProofEventTests(unittest.TestCase):
    """F1.3.8 ("esporre undo e prove a HUD/companion tramite eventi versionati"): prima di
    questo, un rollback o una verifica indipendente dell'effetto erano visibili SOLO nel ledger -
    un HUD/companion non aveva modo di saperlo in tempo reale."""

    def test_a_rolled_back_intent_publishes_an_undo_event(self):
        core = _bare_core()
        subscriber = core.event_bus.subscribe()

        core._publish_effect_proof_events([], ["CREATE_PATH"])

        event = subscriber.get_nowait()
        self.assertEqual(event.type, EventType.UNDO)
        self.assertEqual(event.payload, {"intent": "CREATE_PATH"})

    def test_a_verified_step_publishes_a_verification_event(self):
        core = _bare_core()
        subscriber = core.event_bus.subscribe()

        core._publish_effect_proof_events([("CREATE_PATH", "verified")], [])

        event = subscriber.get_nowait()
        self.assertEqual(event.type, EventType.VERIFICATION)
        self.assertEqual(event.payload, {"intent": "CREATE_PATH", "verified": "verified"})

    def test_nothing_to_report_publishes_no_event(self):
        """Il caso comune (nessun intent verificabile in questo turno, nessun rollback) non deve
        aggiungere rumore sul bus."""
        core = _bare_core()
        subscriber = core.event_bus.subscribe()

        core._publish_effect_proof_events([], [])

        self.assertTrue(subscriber.empty())

    def test_a_given_trace_id_is_attached_to_both_undo_and_verification_events(self):
        """F4.1.1 ("aggiungere... trace id"): lo stesso trace_id che gia' correla questi eventi
        alle ricevute nel ledger (F1.3.8) deve arrivare anche sull'evento HUD, non solo su disco."""
        core = _bare_core()
        subscriber = core.event_bus.subscribe()

        core._publish_effect_proof_events(
            [("CREATE_PATH", "verified")], ["DELETE_PATH"], trace_id="abc123",
        )

        events = [subscriber.get_nowait(), subscriber.get_nowait()]
        self.assertTrue(all(event.trace_id == "abc123" for event in events))

    def test_plan_outcomes_own_trace_id_is_forwarded_automatically(self):
        """`_publish_plan_outcome_effect_proof_events()` non richiede un trace_id esplicito da chi
        la chiama (`_try_plan`/`_default_on_trigger_fired`): lo legge gia' da `outcome.trace_id`
        (F1.7.2)."""
        from core.plan_executor import PlanOutcome, PlanStep, StepOutcome
        from core.skill_result import SkillResult

        outcome = PlanOutcome(
            rolled_back=[StepOutcome(
                step=PlanStep(intent="DELETE_PATH", parameters={}),
                result=SkillResult(success=True, data={}), attempts=1,
            )],
            trace_id="def456",
        )
        core = _bare_core()
        subscriber = core.event_bus.subscribe()

        core._publish_plan_outcome_effect_proof_events(outcome)

        self.assertEqual(subscriber.get_nowait().trace_id, "def456")

    def test_plan_outcome_extraction_covers_completed_stopped_and_rolled_back_steps(self):
        """_publish_plan_outcome_effect_proof_events() estrae da un PlanOutcome vero (non solo
        dalle due liste gia' pronte sopra) - completed, stopped_step (se presente) e
        rolled_back, ciascuno con la propria forma StepOutcome.step.intent/StepOutcome.verified."""
        from core.plan_executor import PlanOutcome, PlanStep, StepOutcome
        from core.skill_result import SkillResult

        outcome = PlanOutcome(
            completed=[StepOutcome(
                step=PlanStep(intent="CREATE_PATH", parameters={}),
                result=SkillResult(success=True, data={}), attempts=1, verified="verified",
            )],
            stopped_step=StepOutcome(
                step=PlanStep(intent="RENAME_PATH", parameters={}),
                result=SkillResult(success=False, data={}, error="VERIFICATION_FAILED"), attempts=1,
                verified="verification_failed",
            ),
            rolled_back=[StepOutcome(
                step=PlanStep(intent="DELETE_PATH", parameters={}),
                result=SkillResult(success=True, data={}), attempts=1,
            )],
        )
        core = _bare_core()
        subscriber = core.event_bus.subscribe()

        core._publish_plan_outcome_effect_proof_events(outcome)

        events = []
        while not subscriber.empty():
            events.append(subscriber.get_nowait())
        undo_events = [e for e in events if e.type == EventType.UNDO]
        verification_events = [e for e in events if e.type == EventType.VERIFICATION]
        self.assertEqual(undo_events[0].payload, {"intent": "DELETE_PATH"})
        self.assertEqual(
            {(e.payload["intent"], e.payload["verified"]) for e in verification_events},
            {("CREATE_PATH", "verified"), ("RENAME_PATH", "verification_failed")},
        )


if __name__ == "__main__":
    unittest.main()
