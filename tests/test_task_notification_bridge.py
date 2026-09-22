"""Ponte Task Monitor <-> Notification Intelligence (integrazione F6.3/F6.7 su un vero EventBus). Le
`AgentOutcome`/`AgentStep` sono quelle vere di `core/agent.py` (non doppi): questo e' il contratto che
`core/jake_core.py::_on_agent_step_completed`/`_run_agent` passano davvero al ponte."""
import unittest

from core.agent import AgentOutcome, AgentStep
from core.event_bus import EventBus
from core.hud_protocol import EventType
from core.notification_center import NotificationMode
from core.notification_policy import NotificationPolicy
from core.skill_result import SkillResult
from core.task_monitor import TaskMonitorRegistry, TaskStatus, UnknownTaskError
from core.task_notification_bridge import TaskNotificationBridge


def step(intent="OPEN_APP", success=True) -> AgentStep:
    return AgentStep(intent=intent, parameters={}, thought="", result=SkillResult(success=success, data={}))


def outcome(trace_id="t-1", request="apri chrome", steps=None, agent_name="general") -> AgentOutcome:
    return AgentOutcome(trace_id=trace_id, request=request, steps=steps or [], agent_name=agent_name)


class BridgeTestCase(unittest.TestCase):
    def setUp(self):
        self.monitor = TaskMonitorRegistry()
        self.policy = NotificationPolicy()
        self.event_bus = EventBus()
        self.subscriber = self.event_bus.subscribe()
        self.bridge = TaskNotificationBridge(self.monitor, self.policy, self.event_bus)

    def drain(self) -> list:
        events = []
        while True:
            try:
                events.append(self.subscriber.get_nowait())
            except Exception:
                break
        return events


class TrackProgressTests(BridgeTestCase):
    def test_the_first_call_starts_a_monitor_and_publishes_nothing(self):
        self.bridge.track_progress(outcome(steps=[step()]))
        self.assertEqual(self.monitor.get("t-1").status, TaskStatus.RUNNING)
        self.assertEqual(self.drain(), [])

    def test_repeated_calls_keep_updating_the_same_task_silently(self):
        self.bridge.track_progress(outcome(steps=[step("OPEN_APP")]))
        self.bridge.track_progress(outcome(steps=[step("OPEN_APP"), step("CLIPBOARD_READ")]))
        self.assertEqual(len(self.monitor.active()), 1)
        self.assertEqual(self.drain(), [])

    def test_a_missing_trace_id_is_a_silent_no_op(self):
        self.bridge.track_progress(outcome(trace_id=None))
        self.assertEqual(self.monitor.active(), [])


class DecisionRequiredTests(BridgeTestCase):
    def test_a_destructive_intent_is_critical_and_delivered_now_even_in_a_quiet_mode(self):
        self.policy.mode = NotificationMode.DO_NOT_DISTURB
        self.bridge.track_progress(outcome(steps=[step("READ_FILE_TEXT")]))
        decision = self.bridge.decision_required(
            outcome(steps=[step("READ_FILE_TEXT")]), message="Confermi la cancellazione?",
            intent="DELETE_PATH", parameters={"path": "C:\\x"}, session_id="sess-1", device_id="dev-1",
        )
        self.assertEqual(decision.action, "deliver_now")
        self.assertEqual(self.monitor.get("t-1").status, TaskStatus.NEEDS_DECISION)

    def test_a_low_risk_intent_is_not_critical_and_can_be_queued_in_do_not_disturb(self):
        self.policy.mode = NotificationMode.DO_NOT_DISTURB
        decision = self.bridge.decision_required(
            outcome(steps=[]), message="Vuoi il meteo di domani?", intent="GET_WEATHER",
        )
        self.assertEqual(decision.action, "queue")

    def test_a_clarifying_question_has_no_intent_and_is_never_critical_by_default(self):
        decision = self.bridge.decision_required(outcome(steps=[]), message="Quale file, di preciso?")
        self.assertEqual(decision.action, "deliver_now")  # priorita' di base in modalita' normale
        events = self.drain()
        self.assertEqual(events[0].payload["decision_required"]["intent"], None)

    def test_an_explicit_critical_flag_always_wins_over_the_derived_risk(self):
        decision = self.bridge.decision_required(
            outcome(steps=[]), message="x", intent="GET_TIME", critical=True,
        )
        self.assertEqual(decision.reason, "evento critico")
        self.policy.mode = NotificationMode.MEETING
        decision = self.bridge.decision_required(
            outcome(trace_id="t-2", steps=[]), message="y", intent="DELETE_PATH", critical=False,
        )
        self.assertNotEqual(decision.reason, "evento critico")

    def test_the_published_event_carries_task_session_device_actions_and_the_decision_required(self):
        self.bridge.track_progress(outcome(steps=[step("OPEN_APP"), step("CLIPBOARD_READ", success=False)]))
        self.bridge.decision_required(
            outcome(steps=[step("OPEN_APP"), step("CLIPBOARD_READ", success=False)]),
            message="Confermi?", intent="DELETE_PATH", parameters={"path": "x"}, policy_reason="always_confirm",
            session_id="sess-42", device_id="phone-1",
        )
        events = self.drain()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.type, EventType.NOTIFICATION)
        self.assertEqual(event.trace_id, "t-1")
        payload = event.payload
        self.assertEqual(payload["origin"], "task_monitor")
        self.assertEqual(payload["task_id"], "t-1")
        self.assertEqual(payload["session_id"], "sess-42")
        self.assertEqual(payload["device_id"], "phone-1")
        self.assertEqual(payload["status"], "needs_decision")
        self.assertEqual(payload["actions_done"], [{"intent": "OPEN_APP", "success": True},
                                                    {"intent": "CLIPBOARD_READ", "success": False}])
        self.assertEqual(payload["decision_required"], {"intent": "DELETE_PATH", "parameters": {"path": "x"},
                                                         "message": "Confermi?", "policy_reason": "always_confirm"})
        self.assertIn("action", payload["decision"])
        self.assertIn("priority", payload["decision"])

    def test_meeting_mode_delivers_a_critical_event_silently_on_screen_not_voice(self):
        self.policy.mode = NotificationMode.MEETING
        decision = self.bridge.decision_required(outcome(steps=[]), message="x", intent="DELETE_PATH")
        self.assertEqual(decision.action, "deliver_now")
        self.assertEqual(decision.channel, "screen")

    def test_starting_directly_with_a_pending_confirmation_and_no_prior_progress_still_works(self):
        """Il primo passo dell'agente e' gia' quello che chiede conferma: track_progress non e' mai stato
        chiamato, decision_required deve avviare il monitor da sola."""
        with self.assertRaises(UnknownTaskError):
            self.monitor.get("t-1")
        decision = self.bridge.decision_required(outcome(steps=[]), message="Confermi?", intent="DELETE_PATH")
        self.assertEqual(decision.action, "deliver_now")
        self.assertEqual(self.monitor.get("t-1").status, TaskStatus.NEEDS_DECISION)

    def test_the_notification_policy_mode_is_read_live_from_the_mode_source_not_a_stale_copy(self):
        current = {"mode": NotificationMode.NORMAL}
        bridge = TaskNotificationBridge(self.monitor, self.policy, self.event_bus, mode_source=lambda: current["mode"])
        current["mode"] = NotificationMode.MEETING
        # self.policy.mode non viene mai toccato a mano: solo _sync_mode() (chiamato da decision_required)
        # puo' fargli vedere MEETING, leggendolo da current["mode"] al momento della decisione.
        decision = bridge.decision_required(outcome(trace_id="t-3", steps=[]), message="x", intent="DELETE_PATH")
        self.assertEqual(decision.channel, "screen")  # critico + meeting forza lo schermo invece della voce


class FinishTaskTests(BridgeTestCase):
    def test_finishing_a_task_never_tracked_is_a_no_op_returning_none(self):
        self.assertIsNone(self.bridge.finish_task(outcome(trace_id="mai-tracciato", steps=[])))
        self.assertEqual(self.drain(), [])

    def test_a_successful_completion_closes_the_task_and_publishes_a_low_priority_event(self):
        self.bridge.track_progress(outcome(steps=[step("OPEN_APP")]))
        decision = self.bridge.finish_task(outcome(steps=[step("OPEN_APP")]), message="Fatto.", success=True)
        self.assertEqual(decision.action, "deliver_now")  # priorita' di base 30 >= soglia 0 in modalita' normale
        with self.assertRaises(UnknownTaskError):
            self.monitor.get("t-1")  # chiuso: non piu' nel registro attivo
        events = self.drain()
        self.assertEqual(events[0].payload["status"], "completed")
        self.assertEqual(events[0].payload["decision_required"], None)

    def test_a_failed_completion_is_marked_as_an_error(self):
        self.bridge.track_progress(outcome(steps=[step("OPEN_APP", success=False)]))
        self.bridge.finish_task(outcome(steps=[step("OPEN_APP", success=False)]), message="errore rete", success=False)
        events = self.drain()
        self.assertEqual(events[-1].payload["status"], "error")

    def test_finishing_a_task_already_awaiting_a_decision_does_not_touch_it(self):
        self.bridge.decision_required(outcome(steps=[]), message="Confermi?", intent="DELETE_PATH")
        self.drain()
        result = self.bridge.finish_task(outcome(steps=[]))
        self.assertIsNone(result)
        self.assertEqual(self.monitor.get("t-1").status, TaskStatus.NEEDS_DECISION)

    def test_finishing_twice_the_second_call_is_a_no_op(self):
        self.bridge.track_progress(outcome(steps=[]))
        self.bridge.finish_task(outcome(steps=[]))
        self.assertIsNone(self.bridge.finish_task(outcome(steps=[])))

    def test_completion_is_never_critical_even_for_a_destructive_agent_run(self):
        """F6.7.2: solo decisione richiesta/errore/anomalia interrompono per urgenza - un completamento
        riuscito non deve mai forzarsi oltre la modalita' corrente."""
        self.policy.mode = NotificationMode.MEETING
        self.bridge.track_progress(outcome(steps=[step("DELETE_PATH")]))
        decision = self.bridge.finish_task(outcome(steps=[step("DELETE_PATH")]), message="Cancellato.", success=True)
        self.assertEqual(decision.action, "queue")  # non critico, MEETING blocca tutto tranne i critici


class MonitorHousekeepingTests(BridgeTestCase):
    def test_the_monitors_own_pending_events_are_drained_and_never_grow_unbounded(self):
        for index in range(5):
            self.bridge.decision_required(outcome(trace_id=f"t-{index}", steps=[]), message="x", intent="GET_TIME")
        # Se il ponte non drenasse la coda interna del monitor, _pending_events crescerebbe senza limite.
        self.assertEqual(self.monitor.drain_events(), [])


if __name__ == "__main__":
    unittest.main()
