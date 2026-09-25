"""Regressione del blocco dopo "Jake, basta" (cancellazione del SINGOLO turno vocale).

Bug reale: durante un task lungo (Vision/Computer Use) "Jake, basta" rispondeva "Va bene, annullo."
ma il comando successivo ("Jake, che ore sono?") veniva spesso ignorato. Cause verificate:
1. il cancel era solo cooperativo: le chiamate al modello (NLU, planner, riassunto della cronologia,
   embedding) non lo guardavano, il worker restava dentro answer() e `_command_busy()` scartava il
   comando nuovo come "task ancora in corso";
2. gli hook di sessione del turno annullato potevano ancora parlare/cambiare stato;
3. un thread TTS sopravvissuto al join rimetteva mute/stato sopra la frase nuova;
4. i provider TTS restavano bloccati su future.result() dopo stop().

Questi test usano thread veri e i componenti veri (cancellable_call, OllamaClient, VisionProvider,
SkillRegistry, TaskAgent, PlanExecutor, WakeWordSession); finti solo microfono, altoparlanti, rete.
"""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from core.kill_switch import KillSwitch
from core.ollama_client import OllamaClient
from core.plan_executor import PlanExecutor
from core.planner import Plan, PlanStep
from core.policy_engine import PolicyEngine
from core.request_context import current_speaker_profile_id, current_stt_confidence
from core.session_hooks import SessionHooks
from core.skill_result import SkillResult
from core.turn_cancellation import (
    TurnCancelled,
    cancellable_call,
    cancellable_sleep,
    cancellation_suspended,
    current_turn_cancel_event,
    current_turn_cancelled,
    raise_if_cancelled,
    reset_current_turn_cancel_event,
    set_current_turn_cancel_event,
)
from core.vision_provider import VisionProvider
from core.voice.speaker_profile import SpeakerHint
from core.voice.wake_word_session import WakeWordSession
from tests.voice_session_support import VoiceSessionTestCase, track


class _TurnContext:
    """Imposta un evento di cancellazione del turno per la durata del blocco."""

    def __init__(self, event=None):
        self.event = event or threading.Event()

    def __enter__(self):
        self._token = set_current_turn_cancel_event(self.event)
        return self.event

    def __exit__(self, *exc):
        reset_current_turn_cancel_event(self._token)
        return False


def _cancel_after(event: threading.Event, delay: float) -> threading.Thread:
    thread = threading.Thread(target=lambda: (time.sleep(delay), event.set()), daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------------------------


class CancellablePrimitivesTests(unittest.TestCase):
    def test_outside_a_turn_the_call_is_direct_and_never_raises(self):
        caller = threading.current_thread()
        seen = []
        self.assertEqual(cancellable_call(lambda: seen.append(threading.current_thread()) or 7), 7)
        self.assertIs(seen[0], caller)
        cancellable_sleep(0)
        raise_if_cancelled()

    def test_inside_a_live_turn_the_value_and_errors_are_returned(self):
        with _TurnContext():
            self.assertEqual(cancellable_call(lambda: "ok"), "ok")
            with self.assertRaises(ValueError):
                cancellable_call(self._raise_value_error)

    @staticmethod
    def _raise_value_error():
        raise ValueError("boom")

    def test_a_blocked_call_is_abandoned_quickly_when_the_turn_is_cancelled(self):
        release = threading.Event()
        self.addCleanup(release.set)
        with _TurnContext() as event:
            _cancel_after(event, 0.05)
            started = time.monotonic()
            with self.assertRaises(TurnCancelled):
                cancellable_call(lambda: release.wait(5))
            self.assertLess(time.monotonic() - started, 0.5)

    def test_a_result_arriving_after_the_cancellation_is_discarded(self):
        with _TurnContext() as event:
            def work():
                event.set()  # l'utente dice "basta" mentre la chiamata sta finendo
                return "risultato tardivo"
            with self.assertRaises(TurnCancelled):
                cancellable_call(work)

    def test_turn_cancelled_is_not_swallowed_by_except_exception(self):
        self.assertFalse(issubclass(TurnCancelled, Exception))
        with _TurnContext() as event:
            event.set()
            with self.assertRaises(TurnCancelled):
                try:
                    raise_if_cancelled()
                except Exception:  # i ripieghi di degrado del progetto
                    self.fail("un annullamento non deve finire in un ripiego")

    def test_sleep_is_interrupted(self):
        with _TurnContext() as event:
            _cancel_after(event, 0.05)
            started = time.monotonic()
            with self.assertRaises(TurnCancelled):
                cancellable_sleep(5)
            self.assertLess(time.monotonic() - started, 0.5)

    def test_suspension_is_scoped_to_the_block(self):
        with _TurnContext() as event:
            event.set()
            with cancellation_suspended():
                self.assertFalse(current_turn_cancelled())
                raise_if_cancelled()
            self.assertTrue(current_turn_cancelled())


# ---------------------------------------------------------------------------------------------
# Modello (Ollama) e visione
# ---------------------------------------------------------------------------------------------


def _json_response(payload):
    response = mock.MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class ModelCallCancellationTests(unittest.TestCase):
    def test_a_blocked_ollama_chat_is_abandoned_within_the_poll(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def blocking_urlopen(*args, **kwargs):
            release.wait(5)
            return _json_response({"message": {"content": "tardi"}})

        client = OllamaClient(base_url="http://127.0.0.1:9")
        with mock.patch("core.ollama_client.request.urlopen", side_effect=blocking_urlopen), _TurnContext() as event:
            _cancel_after(event, 0.05)
            started = time.monotonic()
            with self.assertRaises(TurnCancelled):
                client.chat("m", [{"role": "user", "content": "x"}])
            self.assertLess(time.monotonic() - started, 0.5)

    def test_chat_text_does_not_turn_a_cancellation_into_a_none_fallback(self):
        client = OllamaClient(base_url="http://127.0.0.1:9")
        with mock.patch("core.ollama_client.request.urlopen", return_value=_json_response({})), _TurnContext() as event:
            event.set()
            with self.assertRaises(TurnCancelled):
                client.chat_text("m", [])

    def test_outside_a_voice_turn_ollama_behaves_as_before(self):
        client = OllamaClient(base_url="http://127.0.0.1:9")
        with mock.patch("core.ollama_client.request.urlopen",
                        return_value=_json_response({"message": {"content": "ciao"}})):
            self.assertEqual(client.chat_text("m", []), "ciao")


class HttpReadCancellationTests(unittest.TestCase):
    """Planner, riassunto della cronologia, embedding e skill LLM/web leggono via core.network.read_url."""

    def test_a_blocked_http_read_is_abandoned_and_plain_reads_are_unchanged(self):
        from core.network import read_url

        release = threading.Event()
        self.addCleanup(release.set)

        def blocking_urlopen(*args, **kwargs):
            release.wait(5)
            return _json_response({})

        with mock.patch("urllib.request.urlopen", side_effect=blocking_urlopen), _TurnContext() as event:
            _cancel_after(event, 0.05)
            started = time.monotonic()
            with self.assertRaises(TurnCancelled):
                read_url("http://127.0.0.1:9/x", 5)
            self.assertLess(time.monotonic() - started, 0.5)
        with mock.patch("urllib.request.urlopen", return_value=_json_response({"ok": 1})):
            self.assertEqual(json.loads(read_url("http://127.0.0.1:9/x", 5)), {"ok": 1})

    def test_the_planner_model_call_is_interruptible(self):
        from core.planner_provider import PlannerProvider

        release = threading.Event()
        self.addCleanup(release.set)
        with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: (release.wait(5), _json_response({}))[1]),              _TurnContext() as event:
            _cancel_after(event, 0.05)
            planner = PlannerProvider.__new__(PlannerProvider)
            planner.model, planner.base_url, planner.timeout = "m", "http://127.0.0.1:9", 60
            with mock.patch.object(planner, "_build_output_schema", return_value={}),                  mock.patch.object(planner, "_build_system_prompt", return_value=""),                  self.assertRaises(TurnCancelled):
                planner._request_ollama("apri il blocco note e scrivi ciao")


class VisionCancellationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.image = Path(tmp.name) / "probe.png"
        self.image.write_bytes(b"\x89PNG fake")

    def test_a_blocked_vision_request_returns_quickly_and_the_skill_never_clicks(self):
        from skills.screen_click import ClickElementSkill

        release = threading.Event()
        self.addCleanup(release.set)

        def blocking_urlopen(*args, **kwargs):
            release.wait(5)
            return _json_response({"message": {"content": '{"found": true, "x": 10, "y": 10}'}})

        agent = mock.MagicMock()
        agent.locate_text.return_value = None
        skill = ClickElementSkill(VisionProvider(), computer_agent=agent)
        image = mock.MagicMock(size=(800, 600))
        image.save.side_effect = lambda path: Path(path).write_bytes(b"\x89PNG fake")
        with mock.patch("urllib.request.urlopen", side_effect=blocking_urlopen), \
             mock.patch("core.vision.screen.capture_screenshot_image", return_value=image), \
             mock.patch("core.vision.screen.SCREENSHOTS_DIR", self.image.parent), \
             _TurnContext() as event:
            _cancel_after(event, 0.05)
            started = time.monotonic()
            result = skill.execute({"description": "il pulsante blu"})
            elapsed = time.monotonic() - started

        self.assertEqual(result.error, "CANCELLED")
        self.assertLess(elapsed, 0.5)
        agent.click_point.assert_not_called()

    def test_a_late_vision_answer_never_becomes_a_click(self):
        """La risposta del modello arriva, ma DOPO il "basta": nessun click."""
        from skills.screen_click import ClickElementSkill

        vision = mock.MagicMock()
        agent = mock.MagicMock()
        agent.locate_text.return_value = None
        skill = ClickElementSkill(vision, computer_agent=agent)
        image = mock.MagicMock(size=(800, 600))
        with _TurnContext() as event, \
             mock.patch("core.vision.screen.capture_screenshot_image", return_value=image), \
             mock.patch("core.vision.screen.SCREENSHOTS_DIR", self.image.parent):
            vision.describe.side_effect = lambda *a, **k: (event.set(), '{"found": true, "x": 5, "y": 5}')[1]
            result = skill.execute({"description": "la x del popup"})
        self.assertEqual(result.error, "CANCELLED")
        agent.click_point.assert_not_called()

    def test_the_computer_agent_itself_refuses_to_click_after_cancellation(self):
        from core.computer_agent import ComputerAgent

        with mock.patch("core.vision.screen.capture_screenshot_image", return_value=None), \
             mock.patch("pyautogui.click") as click, mock.patch("pyautogui.doubleClick") as double, \
             _TurnContext() as event:
            event.set()
            with self.assertRaises(TurnCancelled):
                ComputerAgent().click_point(10, 10)
        click.assert_not_called()
        double.assert_not_called()


# ---------------------------------------------------------------------------------------------
# Skill, retry, agente, piano
# ---------------------------------------------------------------------------------------------


class _RecordingSkill:
    metadata = {"intent": "ADD_NOTE", "description": "x", "parameters": {}}

    def __init__(self):
        self.calls = []

    def execute(self, parameters=None):
        self.calls.append(parameters)
        return SkillResult(success=True, data={})


class SkillDispatchCancellationTests(unittest.TestCase):
    def test_no_skill_starts_once_the_turn_is_cancelled(self):
        from tests.test_skill_registry import _bare_registry

        skill = _RecordingSkill()
        registry = _bare_registry({"ADD_NOTE": skill})
        with _TurnContext() as event:
            event.set()
            result = registry.execute("ADD_NOTE", {"text": "x"}, policy_engine=PolicyEngine())
        self.assertEqual(result.error, "CANCELLED")
        self.assertEqual(skill.calls, [])

    def test_compensations_still_run_under_suspension(self):
        from tests.test_skill_registry import _bare_registry

        skill = _RecordingSkill()
        registry = _bare_registry({"ADD_NOTE": skill})
        with _TurnContext() as event:
            event.set()
            with cancellation_suspended():
                result = registry.execute("ADD_NOTE", {"text": "x"}, policy_engine=PolicyEngine())
        self.assertTrue(result.success)
        self.assertEqual(len(skill.calls), 1)

    def test_no_retry_after_cancellation_and_a_cancelled_skill_is_reported_as_cancelled(self):
        from core.execution_safety import execute_action_with_retry

        calls = []

        def flaky(intent, parameters):
            calls.append(intent)
            current_turn_cancel_event().set()  # "basta" durante il primo tentativo
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        with _TurnContext():
            execution, attempts = execute_action_with_retry(flaky, "CREATE_PATH", {"path": "x"})
        self.assertEqual((calls, attempts), (["CREATE_PATH"], 1))

        def cancelled_inside(intent, parameters):
            raise TurnCancelled()

        with _TurnContext():
            execution, attempts = execute_action_with_retry(cancelled_inside, "CREATE_PATH", {"path": "x"})
        self.assertEqual(execution.result.error, "CANCELLED")

    def test_the_ledger_classifies_cancelled_as_a_user_cancellation(self):
        from core.action_ledger import ERROR_CATEGORY_USER_CANCELLED, error_category_of

        self.assertEqual(error_category_of("error:CANCELLED"), ERROR_CATEGORY_USER_CANCELLED)


class PlanAndAgentCancellationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.target = Path(tmp.name) / "creato.txt"
        patches = [mock.patch("core.plan_executor.log_action"), mock.patch("core.execution_safety.log_action"),
                   mock.patch("core.agent.log_action")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_cancel_between_plan_steps_stops_and_compensates(self):
        from tests.test_plan_executor import FakeRegistry

        class CancellingRegistry(FakeRegistry):
            def execute(self, intent, parameters=None, policy_engine=None, *, action_id=None, private=False):
                if current_turn_cancelled() and intent != "DELETE_PATH":
                    raise AssertionError("nessun passo in avanti dopo il cancel")
                result = super().execute(intent, parameters, policy_engine=policy_engine, action_id=action_id, private=private)
                if intent == "CREATE_PATH":
                    current_turn_cancel_event().set()
                return result

        registry = CancellingRegistry()
        plan = Plan(steps=[
            PlanStep(intent="CREATE_PATH", parameters={"path": str(self.target)}),
            PlanStep(intent="ADD_NOTE", parameters={"text": "mai"}),
        ])
        kill_switch = KillSwitch()
        executor = PlanExecutor(registry)
        executor.kill_switch = kill_switch
        with _TurnContext():
            outcome = executor.execute(plan, policy_engine=PolicyEngine())
        self.assertEqual([c[0] for c in registry.calls], ["CREATE_PATH", "DELETE_PATH"])
        self.assertEqual(outcome.stopped_step.result.error, "CANCELLED")
        self.assertFalse(self.target.exists())
        self.assertFalse(kill_switch.is_active())

    def test_cancel_inside_a_plan_step_is_a_cancelled_step_not_a_crash(self):
        from tests.test_plan_executor import FakeRegistry

        class RaisingRegistry(FakeRegistry):
            def execute(self, intent, parameters=None, policy_engine=None, *, action_id=None, private=False):
                if intent == "ADD_NOTE":
                    self.calls.append((intent, dict(parameters or {})))
                    current_turn_cancel_event().set()
                    raise TurnCancelled()
                return super().execute(intent, parameters, policy_engine=policy_engine, action_id=action_id, private=private)

        registry = RaisingRegistry()
        plan = Plan(steps=[
            PlanStep(intent="CREATE_PATH", parameters={"path": str(self.target)}),
            PlanStep(intent="ADD_NOTE", parameters={"text": "lento"}),
            PlanStep(intent="ADD_NOTE", parameters={"text": "mai"}),
        ])
        with _TurnContext():
            outcome = PlanExecutor(registry).execute(plan, policy_engine=PolicyEngine())
        self.assertEqual([c[0] for c in registry.calls], ["CREATE_PATH", "ADD_NOTE", "DELETE_PATH"])
        self.assertEqual(outcome.stopped_step.result.error, "CANCELLED")
        self.assertFalse(self.target.exists())

    def test_cancel_between_agent_steps_stops_and_compensates(self):
        from tests.test_agent import FakeRegistry, ScriptedOllamaClient, _agent

        class CancellingRegistry(FakeRegistry):
            def execute(self, intent, parameters=None, policy_engine=None, *, action_id=None, private=False):
                result = super().execute(intent, parameters, policy_engine=policy_engine, action_id=action_id, private=private)
                if intent == "CREATE_PATH":
                    current_turn_cancel_event().set()
                return result

        registry = CancellingRegistry()
        client = ScriptedOllamaClient([
            {"thought": "Creo il file", "action": {"intent": "CREATE_PATH", "parameters": {"path": str(self.target)}},
             "final_answer": "", "ask_user": ""},
            {"thought": "Poi una nota", "action": {"intent": "ADD_NOTE", "parameters": {"text": "mai"}},
             "final_answer": "", "ask_user": ""},
        ])
        agent = _agent(registry, client)
        agent.kill_switch = KillSwitch()
        with _TurnContext():
            outcome = agent.run("crea un file e poi una nota")
        self.assertEqual(outcome.error, "CANCELLED")
        self.assertEqual(client.calls, 1, "il modello non va piu' interrogato dopo il cancel")
        self.assertNotIn("ADD_NOTE", [c[0] for c in registry.calls])
        self.assertFalse(self.target.exists(), "il passo gia' fatto viene compensato")
        self.assertFalse(agent.kill_switch.is_active())


# ---------------------------------------------------------------------------------------------
# Hook di sessione e JakeCore
# ---------------------------------------------------------------------------------------------


class SessionHooksCancellationTests(unittest.TestCase):
    def test_a_cancelled_turn_can_no_longer_speak_or_change_the_hud_state(self):
        hooks = SessionHooks()
        hooks.speak = mock.MagicMock()
        hooks.set_state = mock.MagicMock()
        with _TurnContext() as event:
            hooks.call("speak", "sto ancora lavorando")
            event.set()
            hooks.call("speak", "ho finito")
            hooks.call("set_state", "working", "passo 3")
        hooks.speak.assert_called_once_with("sto ancora lavorando")
        hooks.set_state.assert_not_called()


class JakeCoreCancelledTurnTests(unittest.TestCase):
    def test_a_cancelled_turn_leaves_no_trace_in_history_memory_or_bus(self):
        from tests.test_jake_core_pipeline import _bare_core

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        core = _bare_core(ledger_path=Path(tmp.name) / "ledger.jsonl")
        subscriber = core.event_bus.subscribe()
        before = list(core.conversation_state.get_short_term_history())

        def slow_process(text):
            current_turn_cancel_event().set()
            return "risposta vecchia"

        with mock.patch.object(core, "_process", side_effect=slow_process), \
             mock.patch.object(core, "_resolve_pronouns", side_effect=lambda text: text), \
             _TurnContext():
            with self.assertRaises(TurnCancelled):
                core._answer_inner("clicca il pulsante", "clicca il pulsante")

        self.assertTrue(subscriber.empty(), "nessun USER/JAKE_MESSAGE di un turno annullato sul bus")
        core.context_summarizer.assert_not_called()
        self.assertNotEqual(core.last_response, "risposta vecchia")
        self.assertEqual(core.conversation_state.get_short_term_history(), before)


# ---------------------------------------------------------------------------------------------
# WakeWordSession end-to-end (thread veri)
# ---------------------------------------------------------------------------------------------


class _QuietTts:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)

    def stop(self):
        pass


class _ScriptedStt:
    def __init__(self):
        self.next_text = ""

    def transcribe(self, audio, sample_rate):
        return self.next_text


class _BlockingCore:
    """JakeCore finto con il comportamento che conta: il primo comando resta dentro una "chiamata
    al modello" (cancellable_call su un evento mai rilasciato); gli altri rispondono subito.
    Conta quante answer() sono attive insieme."""

    EXIT_SENTINEL = "__exit__"

    def __init__(self, unwind_delay: float = 0.0):
        self.conversation_state = SimpleNamespace(has_pending_action=lambda: False)
        self.kill_switch = KillSwitch()
        self.unwind_delay = unwind_delay
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.contexts = []
        self._lock = threading.Lock()

    def answer(self, text):
        text = text.strip(" ?!.").lower()
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append(text)
            self.contexts.append((text, current_speaker_profile_id(), current_stt_confidence()))
        try:
            if len(self.calls) == 1:
                self.started.set()
                try:
                    cancellable_call(lambda: self.release.wait(5))
                finally:
                    # un pezzo non interrompibile che impiega un po' a chiudersi
                    time.sleep(self.unwind_delay)
                return "risposta del task lungo"
            if text == "che ore sono":
                return "Sono le dieci."
            return f"eseguito: {text}"
        finally:
            with self._lock:
                self.active -= 1


class VoiceSessionCancellationTests(VoiceSessionTestCase):
    def _session(self, core, **kwargs):
        stt = _ScriptedStt()
        tts = _QuietTts()
        vad = SimpleNamespace(on_level=None, muted=False, SAMPLE_RATE=16000)
        session = track(WakeWordSession(core, stt, tts, vad_listener=vad, **kwargs))
        self.addCleanup(core.release.set)
        return session, stt, tts

    @staticmethod
    def _hear(session, stt, text):
        stt.next_text = text
        session._handle_utterance(np.zeros(320, dtype=np.float32))

    def _start_long_task(self, session, stt, core):
        self._hear(session, stt, "Jake, trova il pulsante blu e cliccalo")
        self.assertTrue(core.started.wait(2), "il task lungo non e' partito")

    def test_stop_while_answer_is_blocked_cancels_quickly_and_only_acknowledges(self):
        core = _BlockingCore()
        session, stt, tts = self._session(core)
        with mock.patch.object(session, "_respond") as respond:
            self._start_long_task(session, stt, core)
            started = time.monotonic()
            self._hear(session, stt, "Jake, basta")
            self.assertTrue(session.wait_for_commands(1), "il worker annullato non si e' chiuso")
            elapsed = time.monotonic() - started
        respond.assert_called_once_with("Va bene, annullo.")
        self.assertLess(elapsed, 0.6)
        # _respond e' finto (nessuna voce ha preso lo stato): il turno chiuso riporta a idle.
        self.assertEqual(session.state, "idle")
        self.assertFalse(core.kill_switch.is_active(), "basta non e' il kill switch globale")

    def test_a_command_right_after_the_stop_runs_exactly_once(self):
        core = _BlockingCore()
        session, stt, tts = self._session(core)
        with mock.patch.object(session, "_respond") as respond:
            self._start_long_task(session, stt, core)
            self._hear(session, stt, "Jake, basta")
            self._hear(session, stt, "Jake, che ore sono?")
            self.assertTrue(session.wait_for_commands(2))
        self.assertEqual(core.calls, ["trova il pulsante blu e cliccalo", "che ore sono"])
        self.assertEqual(core.max_active, 1, "mai due answer() concorrenti")
        self.assertEqual([c.args[0] for c in respond.call_args_list], ["Va bene, annullo.", "Sono le dieci."])
        self.assertFalse(core.kill_switch.is_active())

    def test_a_slow_unwinding_worker_queues_the_next_command_instead_of_racing(self):
        core = _BlockingCore(unwind_delay=0.4)
        session, stt, tts = self._session(core)
        with mock.patch.object(session, "_respond") as respond:
            self._start_long_task(session, stt, core)
            self._hear(session, stt, "Jake, basta")
            self.assertTrue(session._command_busy(), "il vecchio worker sta ancora chiudendo")
            self._hear(session, stt, "Jake, che ore sono?")
            self.assertEqual(core.calls, ["trova il pulsante blu e cliccalo"], "il nuovo comando aspetta")
            self.assertTrue(session.wait_for_commands(3))
        self.assertEqual(core.calls[-1], "che ore sono")
        self.assertEqual(len(core.calls), 2)
        self.assertEqual(core.max_active, 1)
        spoken = [c.args[0] for c in respond.call_args_list]
        self.assertNotIn("risposta del task lungo", spoken)
        self.assertEqual(spoken, ["Va bene, annullo.", "Sono le dieci."])

    def test_only_the_last_queued_command_runs_and_a_second_stop_drops_it(self):
        core = _BlockingCore(unwind_delay=0.4)
        session, stt, tts = self._session(core)
        with mock.patch.object(session, "_respond"):
            self._start_long_task(session, stt, core)
            self._hear(session, stt, "Jake, basta")
            self._hear(session, stt, "Jake, apri la calcolatrice")
            self._hear(session, stt, "Jake, fermati")
            self.assertTrue(session.wait_for_commands(3))
        self.assertEqual(core.calls, ["trova il pulsante blu e cliccalo"])

    def test_a_non_stop_command_during_a_live_task_is_still_not_run_concurrently(self):
        core = _BlockingCore()
        session, stt, tts = self._session(core)
        with mock.patch.object(session, "_respond"):
            self._start_long_task(session, stt, core)
            self._hear(session, stt, "Jake, che ore sono?")
            self.assertEqual(core.calls, ["trova il pulsante blu e cliccalo"])
            core.release.set()
            self.assertTrue(session.wait_for_commands(2))
        self.assertEqual(core.max_active, 1)

    def test_a_stale_queued_command_is_dropped_with_an_explanation(self):
        core = _BlockingCore(unwind_delay=0.3)
        session, stt, tts = self._session(core)
        session.PENDING_COMMAND_MAX_AGE_SECONDS = 0.05
        with mock.patch.object(session, "_respond") as respond:
            self._start_long_task(session, stt, core)
            self._hear(session, stt, "Jake, basta")
            self._hear(session, stt, "Jake, che ore sono?")
            self.assertTrue(session.wait_for_commands(3))
        self.assertEqual(len(core.calls), 1)
        self.assertIn("ripeti", respond.call_args_list[-1].args[0])

    def test_the_second_turn_gets_its_own_speaker_and_confidence(self):
        core = _BlockingCore()
        session, stt, tts = self._session(core, speaker_store=mock.MagicMock())
        hints = [SpeakerHint(profile_id="davide", confidence="high"), SpeakerHint(profile_id=None, confidence="none")]
        with mock.patch.object(session, "_respond"), \
             mock.patch("core.voice.wake_word_session.extract_features", return_value=object()), \
             mock.patch("core.voice.wake_word_session.identify", side_effect=hints):
            session.last_confidence = 0.91
            stt.next_text = "Jake, trova il pulsante blu e cliccalo"
            with mock.patch.object(session, "_transcribe", side_effect=lambda u: stt.next_text):
                session._handle_utterance(np.zeros(320, dtype=np.float32))
                self.assertTrue(core.started.wait(2))
                session.last_confidence = None
                stt.next_text = "Jake, basta"
                session._handle_utterance(np.zeros(320, dtype=np.float32))
                session.last_confidence = 0.42
                stt.next_text = "Jake, che ore sono?"
                session._handle_utterance(np.zeros(320, dtype=np.float32))
            self.assertTrue(session.wait_for_commands(2))
        self.assertEqual(core.contexts, [
            ("trova il pulsante blu e cliccalo", "davide", 0.91),
            ("che ore sono", None, 0.42),
        ])

    def test_an_explicit_stop_resets_the_wake_cooldown(self):
        core = _BlockingCore()
        core.calls.append("warm-up")  # il primo comando non deve bloccarsi in questo test
        session, stt, tts = self._session(core)
        clock = {"t": 1000.0}
        session.wake_cooldown._clock = lambda: clock["t"]
        with mock.patch.object(session, "_respond"):
            self._hear(session, stt, "Jake, basta")
            self.assertTrue(session.wait_for_commands(2))
            clock["t"] += 0.5  # ben dentro il cooldown di 1,5 s
            self._hear(session, stt, "Jake, che ore sono?")
            self.assertTrue(session.wait_for_commands(2))
        self.assertEqual(core.calls[-1], "che ore sono")

    def test_a_stale_tts_thread_does_not_unmute_or_reset_the_newer_speech(self):
        core = _BlockingCore()
        stuck = threading.Event()
        first_inside = threading.Event()
        self.addCleanup(stuck.set)

        class StuckTts(_QuietTts):
            def speak(self, text):
                super().speak(text)
                if text.startswith("Prima"):
                    first_inside.set()
                    stuck.wait(5)  # uno stop() che non ferma subito il provider

        session, stt, _ = self._session(core)
        session.tts_provider = StuckTts()
        with mock.patch.object(session, "_interrupt_speech"):
            session._speak_async("Prima frase molto lunga.")
            first = session._tts_thread
            # Il primo thread deve essere DENTRO il provider prima di sostituire speak (su una CI
            # lenta arrivava dopo e finiva nella funzione della seconda frase).
            self.assertTrue(first_inside.wait(5))
            second_started = threading.Event()
            original = session.tts_provider.speak

            second_release = threading.Event()
            self.addCleanup(second_release.set)

            def second_speak(text):
                original(text)
                second_started.set()
                second_release.wait(5)

            session.tts_provider.speak = second_speak
            session._speak_async("Seconda frase.")
            self.assertTrue(second_started.wait(2))
            stuck.set()
            first.join(2)
            self.assertFalse(first.is_alive())
            self.assertTrue(session.vad_listener.muted, "la frase nuova sta ancora parlando: microfono in mute")
            self.assertEqual(session.state, "speaking")
            second_release.set()
            session._tts_thread.join(2)
        self.assertFalse(session.vad_listener.muted)


class TtsStopTests(unittest.TestCase):
    def test_character_speak_returns_promptly_when_stopped_while_a_chunk_is_converting(self):
        from core.voice.character_tts_provider import CharacterTtsProvider

        release = threading.Event()
        self.addCleanup(release.set)
        base = mock.MagicMock()
        manager = mock.MagicMock()
        manager.ensure_running.return_value = True
        manager.client.convert.side_effect = lambda data: (release.wait(5), b"")[1]
        provider = CharacterTtsProvider(base, manager)
        self.addCleanup(provider._executor.shutdown, wait=False)
        with mock.patch.object(provider, "_synthesize_to_bytes", return_value=b"wav"):
            thread = threading.Thread(target=provider.speak, args=("Una frase da convertire.",), daemon=True)
            thread.start()
            time.sleep(0.1)
            started = time.monotonic()
            provider.stop()
            thread.join(1)
            self.assertFalse(thread.is_alive(), "speak() e' rimasto bloccato sulla conversione dopo stop()")
            self.assertLess(time.monotonic() - started, 0.5)

            # La frase successiva non resta in coda dietro la conversione ormai inutile.
            manager.client.convert.side_effect = lambda data: b"converted"
            played = []
            with mock.patch.object(provider, "_play", side_effect=played.append):
                started = time.monotonic()
                provider.speak("Va bene, annullo.")
            self.assertEqual(played, [b"converted"])
            self.assertLess(time.monotonic() - started, 0.5)

    def test_edge_speak_returns_promptly_when_stopped_during_network_synthesis(self):
        from core.voice.edge_tts_provider import EdgeTtsProvider

        release = threading.Event()
        self.addCleanup(release.set)
        provider = EdgeTtsProvider()
        self.addCleanup(provider._executor.shutdown, wait=False)
        with mock.patch("core.voice.edge_tts_provider.is_online", return_value=True), \
             mock.patch.object(provider, "_synthesize", side_effect=lambda text: (release.wait(5), None)[1]), \
             mock.patch.object(provider, "_play") as play:
            thread = threading.Thread(target=provider.speak, args=("Una frase lenta.",), daemon=True)
            thread.start()
            time.sleep(0.1)
            started = time.monotonic()
            provider.stop()
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertLess(time.monotonic() - started, 0.5)
        play.assert_not_called()


if __name__ == "__main__":
    unittest.main()
