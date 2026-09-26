"""Regressioni dei problemi reali emersi dal primo gate hardware F2 (laptop, 26/09/2026).

Un test per problema: partial che bloccavano la trascrizione finale, stop del barge-in che aspettava
il cleanup del thread TTS, "e spiegami ..." instradato alla calcolatrice, deriva verso il cinese,
parole senza senso eseguite nel follow-up, notifiche proattive durante la prova."""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from core.kill_switch import KillSwitch
from core.language_guard import keep_reply_language
from core.notification_center import NotificationCenter
from core.voice.language_normalizer import resolve_ellipsis
from core.voice.live_transcriber import SttModelLock, _LockedProvider
from core.voice.wake_word_session import WakeWordSession
from tests.voice_session_support import VoiceSessionTestCase, track


class FinalTranscriptionPriorityTests(unittest.TestCase):
    def test_no_partial_starts_while_the_final_is_waiting(self):
        lock = SttModelLock()
        self.assertTrue(lock.try_acquire_partial(), "modello libero: il partial parte")
        final_done = threading.Event()

        def final():
            with lock:
                final_done.set()

        worker = threading.Thread(target=final)
        worker.start()
        time.sleep(0.05)  # la finale ora aspetta il partial in corso
        lock.release_partial()
        self.assertTrue(final_done.wait(1), "la finale entra appena il partial in corso finisce")
        worker.join(1)

        with lock:  # durante una finale nessun partial parte, e il provider lo salta senza bloccare
            provider = _LockedProvider(mock.MagicMock(), lock)
            started = time.monotonic()
            self.assertIsNone(provider.transcribe_partial(np.zeros(160, dtype=np.float32), 16000))
            self.assertLess(time.monotonic() - started, 0.05)

    def test_partials_use_the_light_greedy_decoding(self):
        from core.voice.stt_provider import WhisperSttProvider

        provider = WhisperSttProvider.__new__(WhisperSttProvider)
        provider.language, provider.initial_prompt = "it", "Jake."
        provider._model = mock.MagicMock()
        provider._model.transcribe.return_value = ([SimpleNamespace(text=" ciao", avg_logprob=-0.2, start=0, end=1)], None)
        self.assertEqual(provider.transcribe_partial(np.zeros(160, dtype=np.float32))[0], "ciao")
        kwargs = provider._model.transcribe.call_args.kwargs
        self.assertEqual((kwargs["beam_size"], kwargs["vad_filter"]), (1, False))


class _LingeringTts:
    """stop() ferma l'audio subito, ma speak() impiega ancora un po' a tornare (come un provider reale)."""

    def __init__(self):
        self.spoken = []
        self.active = 0
        self.overlap = False
        self._stop = threading.Event()

    def speak(self, text):
        self.active += 1
        self.overlap = self.overlap or self.active > 1
        self.spoken.append(text)
        self._stop.clear()
        self._stop.wait(5)
        time.sleep(0.2)
        self.active -= 1

    def stop(self):
        self._stop.set()


class FastBargeInStopTests(VoiceSessionTestCase):
    def test_the_stop_does_not_wait_for_the_old_tts_thread_and_voices_never_overlap(self):
        core = SimpleNamespace(conversation_state=SimpleNamespace(has_pending_action=lambda: False),
                               kill_switch=KillSwitch(), EXIT_SENTINEL="__exit__")
        vad = SimpleNamespace(on_level=None, muted=False, SAMPLE_RATE=16000)
        tts = _LingeringTts()
        session = track(WakeWordSession(core, mock.MagicMock(), tts, vad_listener=vad, follow_up_seconds=0))
        self.addCleanup(tts.stop)
        session._speak_async("Una risposta lunga.")
        self.assertTrue(_wait(lambda: tts.active == 1))
        old = session._tts_thread

        started = time.monotonic()
        session.barge_in.speaker.cancel()  # quello che fa il barge-in
        self.assertLess(time.monotonic() - started, 0.1, "lo stop non aspetta il cleanup del thread TTS")
        self.assertFalse(vad.muted)
        session._set_state("listening", "")
        old.join(2)
        self.assertEqual(session.state, "listening", "il thread vecchio non tocca lo stato del turno nuovo")

        session._speak_async("Frase nuova.")
        self.assertTrue(_wait(lambda: "Frase nuova." in tts.spoken))
        tts.stop()
        session._tts_thread.join(2)
        self.assertFalse(tts.overlap, "mai due voci insieme")


class EllipsisRoutingTests(unittest.TestCase):
    def test_a_new_request_after_a_calculation_is_not_a_new_expression(self):
        self.assertIsNone(resolve_ellipsis("e spiegami le differenze tra Java e Python.", "CALCULATE", {"expression": "6*8"}))
        self.assertIsNotNone(resolve_ellipsis("e a Milano?", "GET_WEATHER", {"city": "Roma"}))

    def test_real_calculations_still_reach_the_calculator(self):
        from core.intent_provider import RuleBasedProvider

        command = RuleBasedProvider().detect_intent("quanto fa 4000 per 3?")
        self.assertEqual(command.intent, "CALCULATE")


class LanguageDriftTests(unittest.TestCase):
    def test_a_drift_to_chinese_is_cut_at_the_last_italian_sentence(self):
        answer = "Internet nasce da ARPANET nel 1969. Poi arriva il web. 在1990年代，互联网迅速发展。"
        self.assertEqual(keep_reply_language(answer, "raccontami la storia di Internet"),
                         ("Internet nasce da ARPANET nel 1969. Poi arriva il web.", True))
        self.assertEqual(keep_reply_language("你好", "come si dice ciao in cinese?"), ("你好", False))

    def test_the_skill_retries_once_when_too_little_italian_is_left(self):
        from skills.ask_question import AskQuestionSkill

        skill = AskQuestionSkill()
        retry = "Internet nasce nel 1969 come rete militare e universitaria, poi diventa il web che conosciamo."
        with mock.patch.object(skill, "_ask", side_effect=["Certo. 互联网的历史很长。", retry]) as ask:
            result = skill.execute({"question": "raccontami la storia di Internet"})
        self.assertEqual(ask.call_count, 2)
        self.assertTrue(ask.call_args.kwargs["insist_language"])
        self.assertTrue(result.data["answer"].startswith("Internet nasce"))


class _Stt:
    def __init__(self):
        self.next = ("", None)

    def transcribe(self, audio, sample_rate):
        return self.next[0]

    def transcribe_detailed(self, audio, sample_rate):
        return self.next


class UncertainFollowUpTests(VoiceSessionTestCase):
    def _session(self):
        core = mock.MagicMock()
        core.conversation_state.has_pending_action.return_value = False
        core.answer.return_value = ""
        stt = _Stt()
        vad = SimpleNamespace(on_level=None, muted=False, SAMPLE_RATE=16000)
        session = track(WakeWordSession(core, stt, mock.MagicMock(), vad_listener=vad, follow_up_seconds=6))
        session.listening.open_follow_up()
        return session, core, stt

    def test_gibberish_in_the_follow_up_asks_to_repeat_instead_of_running(self):
        session, core, stt = self._session()
        with mock.patch.object(session, "_respond") as respond:
            stt.next = ("De Ica, despega, miro, miro, miro, miro, miro.", None)
            session._handle_utterance(np.zeros(320, dtype=np.float32))
            stt.next = ("Io risovo.", 0.31)
            session._handle_utterance(np.zeros(320, dtype=np.float32))
        core.answer.assert_not_called()
        self.assertEqual(respond.call_args.args[0], "Non ho capito bene, puoi ripetere?")

    def test_a_normal_follow_up_still_runs(self):
        session, core, stt = self._session()
        stt.next = ("e domani?", 0.82)
        with mock.patch.object(session, "_respond"):
            session._handle_utterance(np.zeros(320, dtype=np.float32))
            self.assertTrue(session.wait_for_commands(2))
        core.answer.assert_called_once_with("e domani?")


class ProactivitySuspensionTests(unittest.TestCase):
    def test_notifications_queue_while_suspended_and_come_back_after(self):
        center = NotificationCenter()
        center.suspend()
        self.assertIsNone(center.gate("advisory", "Batteria al 10%"))
        self.assertEqual(center.resume(), ["Batteria al 10%"])
        self.assertEqual(center.gate("advisory", "Disco quasi pieno"), "Disco quasi pieno")

    def test_the_core_pauses_only_the_running_proactive_components(self):
        from core.jake_core import JakeCore

        core = JakeCore.__new__(JakeCore)
        core.logger = mock.MagicMock()
        core.notification_center = NotificationCenter()
        core.kill_switch = KillSwitch()
        alive = SimpleNamespace(is_alive=lambda: True)
        core.scheduler = mock.MagicMock(_thread=alive)
        core.trigger_scheduler = mock.MagicMock(_thread=alive)
        core.system_advisor = mock.MagicMock(_thread=None)  # disattivato da config: resta spento
        with core.proactivity_suspended("test") as released:
            self.assertIsNone(core.notification_center.gate("reminder", "Promemoria: medicina"))
            core.scheduler.stop.assert_called_once()
            core.trigger_scheduler.stop.assert_called_once()
        core.scheduler.start.assert_called_once()
        core.trigger_scheduler.start.assert_called_once()
        core.system_advisor.stop.assert_not_called()
        core.system_advisor.start.assert_not_called()
        self.assertEqual(released, ["Promemoria: medicina"])


class NoInterruptionWhileSpeakingTests(VoiceSessionTestCase):
    def test_an_advisory_while_jake_speaks_is_queued_not_spoken_over_the_answer(self):
        """F6.3: prima l'avviso partiva subito e _speak_async tagliava la risposta in corso."""
        center = NotificationCenter()
        core = SimpleNamespace(conversation_state=SimpleNamespace(has_pending_action=lambda: False),
                               kill_switch=KillSwitch(), EXIT_SENTINEL="__exit__", notification_center=center,
                               notify=mock.MagicMock(side_effect=lambda kind, message: message))
        vad = SimpleNamespace(on_level=None, muted=False, SAMPLE_RATE=16000)
        tts = _LingeringTts()
        session = track(WakeWordSession(core, mock.MagicMock(), tts, vad_listener=vad, follow_up_seconds=0))
        self.addCleanup(tts.stop)
        session._speak_async("La risposta lunga che l'utente sta ascoltando.")
        self.assertTrue(_wait(lambda: tts.active == 1))

        session._on_advisory("Batteria al 10%")

        core.notify.assert_not_called()
        self.assertEqual(tts.spoken, ["La risposta lunga che l'utente sta ascoltando."])
        self.assertEqual(center.take_deferred(), [{"kind": "advisory", "message": "Batteria al 10%", "deferred": True}])
        tts.stop()
        session._tts_thread.join(2)
        session._on_advisory("Disco quasi pieno")
        core.notify.assert_called_once_with("advisory", "Disco quasi pieno")


def _wait(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


if __name__ == "__main__":
    unittest.main()
