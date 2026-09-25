"""Partial di trascrizione dentro WakeWordSession/VadListener (F2.2.1, F2.2.2, F2.2.3, F2.2.7): quando
ci sono, a chi arrivano (solo a cio' che e' rivolto a Jake) e cosa NON fanno (un partial non e' mai un
comando). Provider e listener finti, thread veri solo per il decodificatore."""
import time
import unittest
from unittest import mock

import numpy as np

from core.event_bus import EventBus
from core.voice.streaming_stt import KIND_FINAL, KIND_PARTIAL, TranscriptEvent
from core.voice.vad_listener import VadListener
from core.voice.wake_word_session import WakeWordSession
from tests.voice_session_support import VoiceSessionTestCase, track

FRAME = VadListener.FRAME_SAMPLES
TIMEOUT = 5.0


class ScriptedStt:
    """Provider finto: trascrive un testo fisso; con `detailed` riporta anche la confidenza (come Whisper)."""

    def __init__(self, text="jake che ore sono", detailed=False):
        self.text = text
        self.calls = 0
        if detailed:
            self.__class__ = DetailedStt

    def transcribe(self, audio, sample_rate):
        self.calls += 1
        return self.text


class DetailedStt(ScriptedStt):
    def transcribe_detailed(self, audio, sample_rate):
        self.calls += 1
        return self.text, 0.83


class FakeVad:
    SAMPLE_RATE = 16000
    FRAME_SAMPLES = FRAME

    def __init__(self):
        self.muted = False
        self.on_level = None
        self.on_speaking_frame = None
        self.on_utterance_frame = None
        self.on_utterance_end = None

    def is_speech_pcm(self, pcm):
        return True

    def begin_utterance(self, frames):
        self.muted = False


class FakeTts:
    def speak(self, text):
        pass

    def stop(self):
        pass


def _core():
    core = mock.MagicMock(EXIT_SENTINEL="ESCI")
    core.conversation_state.has_pending_action.return_value = False
    core.answer.return_value = "Fatto."
    core.event_bus = EventBus()
    return core


def _session(partials="on", stt=None, **kwargs):
    core = kwargs.pop("core", None) or _core()
    vad = FakeVad()
    stt = stt or ScriptedStt()
    session = track(WakeWordSession(core, stt, FakeTts(), vad_listener=vad, partials=partials, **kwargs))
    queue_ = core.event_bus.subscribe()
    return session, vad, queue_, core


def _transcripts(queue_):
    events = []
    while not queue_.empty():
        event = queue_.get_nowait()
        if event.type.value == "TRANSCRIPT":
            events.append(TranscriptEvent.from_payload(event.payload))
    return events


def _speech_frames(vad, seconds):
    for _ in range(int(seconds * 1000 / 30)):
        vad.on_utterance_frame(np.full(FRAME, 1000, dtype=np.int16))


class WiringTests(VoiceSessionTestCase):
    def test_partials_are_off_by_default_and_the_listener_is_not_hooked(self):
        session, vad, _, _ = _session(partials="off")
        self.assertIsNone(session.live_transcriber)
        self.assertIsNone(vad.on_utterance_frame)

    def test_on_creates_the_transcriber_and_hooks_the_listener(self):
        session, vad, _, _ = _session(partials="on")
        self.addCleanup(session.stop)
        self.assertIsNotNone(session.live_transcriber)
        self.assertEqual(vad.on_utterance_frame, session._on_utterance_frame)
        self.assertEqual(vad.on_utterance_end, session._on_utterance_end)

    def test_auto_enables_partials_only_on_a_gpu_provider(self):
        cpu = ScriptedStt()
        cpu.device = "cpu"
        session, _, _, _ = _session(partials="auto", stt=cpu)
        self.assertIsNone(session.live_transcriber)
        gpu = ScriptedStt()
        gpu.device = "cuda"
        session2, _, _, _ = _session(partials="auto", stt=gpu)
        self.addCleanup(session2.stop)
        self.assertIsNotNone(session2.live_transcriber)

    def test_stop_closes_the_decoder_thread(self):
        session, _, _, _ = _session(partials="on")
        worker = session.live_transcriber._worker
        session.stop()
        self.assertFalse(worker.is_alive())


class PartialDeliveryTests(VoiceSessionTestCase):
    def _wait_for_partial(self, queue_, session, predicate=lambda events: bool(events)):
        collected = []
        deadline = time.time() + TIMEOUT
        while time.time() < deadline:
            collected.extend(_transcripts(queue_))
            if predicate(collected):
                return collected
            time.sleep(0.02)
        return collected

    def test_partials_reach_the_bus_when_the_user_is_addressing_jake(self):
        session, vad, queue_, _ = _session(stt=ScriptedStt("jake apri spotify"))
        self.addCleanup(session.stop)
        session.listening.arm_command()  # finestra di comando aperta (bare "Jake" o click sull'orb)
        _speech_frames(vad, 1.0)
        events = self._wait_for_partial(queue_, session)
        self.assertTrue(events)
        self.assertTrue(all(e.kind == KIND_PARTIAL for e in events))
        self.assertEqual(events[0].text, "jake apri spotify")

    def test_partials_beginning_with_the_wake_word_are_published_even_in_the_idle_state(self):
        session, vad, queue_, _ = _session(stt=ScriptedStt("jake apri spotify"))
        self.addCleanup(session.stop)
        _speech_frames(vad, 1.0)
        self.assertTrue(self._wait_for_partial(queue_, session))

    def test_ambient_speech_without_the_wake_word_is_never_published(self):
        """Privacy: una TV o una conversazione in stanza NON deve finire sul bus (che il companion trasmette)."""
        session, vad, queue_, _ = _session(stt=ScriptedStt("nel frattempo il presidente ha dichiarato che"))
        self.addCleanup(session.stop)
        _speech_frames(vad, 1.5)
        time.sleep(0.6)
        self.assertEqual(_transcripts(queue_), [])
        self.assertGreater(session.live_transcriber._transcriber.revision, 0)  # il decodificatore ha lavorato...

    def test_dictation_is_never_published(self):
        session, vad, queue_, _ = _session(stt=ScriptedStt("la mia password segreta e' hunter due"))
        self.addCleanup(session.stop)
        session.start_dictation()
        _speech_frames(vad, 1.5)
        time.sleep(0.6)
        self.assertEqual(_transcripts(queue_), [])

    def test_a_partial_is_never_a_command(self):
        session, vad, queue_, core = _session(stt=ScriptedStt("jake apri spotify"))
        self.addCleanup(session.stop)
        session.listening.arm_command()
        _speech_frames(vad, 1.0)
        self._wait_for_partial(queue_, session)
        core.answer.assert_not_called()  # solo la frase finale, trascritta per intero, arriva a JakeCore

    def test_the_transcript_callback_receives_partials_too(self):
        received = []
        session, vad, _, _ = _session(stt=ScriptedStt("jake apri spotify"), on_transcript=received.append)
        self.addCleanup(session.stop)
        session.listening.arm_command()
        _speech_frames(vad, 1.0)
        deadline = time.time() + TIMEOUT
        while not received and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(received)

    def test_a_broken_transcript_callback_does_not_stop_anything(self):
        def broken(event):
            raise RuntimeError("HUD rotto")

        session, vad, queue_, _ = _session(stt=ScriptedStt("jake apri spotify"), on_transcript=broken)
        self.addCleanup(session.stop)
        session.listening.arm_command()
        _speech_frames(vad, 1.0)
        self.assertTrue(self._wait_for_partial(queue_, session))  # il bus l'ha ricevuto lo stesso


class FinalTranscriptTests(VoiceSessionTestCase):
    def test_a_final_event_is_published_for_an_addressed_phrase_with_real_confidence(self):
        session, _, queue_, core = _session(partials="off", stt=ScriptedStt("jake che ore sono", detailed=True))
        with mock.patch.object(session, "_respond"):
            session._handle_utterance(object())
        (event,) = _transcripts(queue_)
        self.assertEqual((event.kind, event.text, event.confidence), (KIND_FINAL, "jake che ore sono", 0.83))
        core.answer.assert_called_once_with("che ore sono")

    def test_a_provider_without_confidence_reports_none_not_an_invented_value(self):
        session, _, queue_, _ = _session(partials="off", stt=ScriptedStt("jake che ore sono"))
        with mock.patch.object(session, "_respond"):
            session._handle_utterance(object())
        (event,) = _transcripts(queue_)
        self.assertIsNone(event.confidence)

    def test_ambient_speech_produces_no_final_event(self):
        session, _, queue_, core = _session(partials="off", stt=ScriptedStt("nel frattempo il presidente ha detto"))
        session._handle_utterance(object())
        self.assertEqual(_transcripts(queue_), [])
        core.answer.assert_not_called()

    def test_final_and_partials_share_the_utterance_id_and_the_revision_continues(self):
        session, vad, queue_, _ = _session(stt=ScriptedStt("jake che ore sono"))
        self.addCleanup(session.stop)
        _speech_frames(vad, 1.0)
        deadline = time.time() + TIMEOUT
        partials = []
        while not partials and time.time() < deadline:
            partials.extend(_transcripts(queue_))
            time.sleep(0.02)
        vad.on_utterance_end()
        with mock.patch.object(session, "_respond"):
            session._handle_utterance(object())
        finals = [e for e in _transcripts(queue_) if e.kind == KIND_FINAL]
        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0].utterance_id, partials[0].utterance_id)
        self.assertGreater(finals[0].revision, partials[-1].revision)

    def test_an_empty_transcription_publishes_nothing_and_drops_the_pending_ids(self):
        session, _, queue_, _ = _session(partials="off", stt=ScriptedStt(""))
        session._handle_utterance(object())
        self.assertEqual(_transcripts(queue_), [])

    def test_transcription_holds_the_model_lock(self):
        held = []

        class Probing(ScriptedStt):
            def transcribe(inner, audio, sample_rate):
                held.append(session._stt_lock.locked())
                return "jake che ore sono"

        session, _, _, _ = _session(partials="off", stt=Probing())
        with mock.patch.object(session, "_respond"):
            session._handle_utterance(object())
        self.assertEqual(held, [True])


class InterruptionSeedsThePartialsTests(VoiceSessionTestCase):
    def test_a_barge_in_starts_a_live_utterance_from_the_preroll(self):
        session, vad, _, _ = _session(barge_in="on")
        self.addCleanup(session.stop)
        seeded = []
        original_feed = session.live_transcriber.feed
        session.live_transcriber.feed = lambda frame: (seeded.append(len(frame)), original_feed(frame))[1]
        session.barge_in.preroll.push(np.full(FRAME, 0.2, dtype=np.float32))
        session.barge_in.preroll.push(np.full(FRAME, 0.2, dtype=np.float32))
        session._start_interruption(mock.MagicMock(turn_id=2))
        self.assertTrue(session._live_active)
        self.assertEqual(seeded, [FRAME, FRAME])


class VadListenerUtteranceCallbacksTests(VoiceSessionTestCase):
    @staticmethod
    def _listener(sequence):
        fake = mock.MagicMock()
        fake.is_speech.side_effect = sequence
        with mock.patch("webrtcvad.Vad", return_value=fake):
            return VadListener(silence_ms=30)

    @staticmethod
    def _stream(frames):
        def factory(**kwargs):
            for f in frames:
                kwargs["callback"](f, f.shape[0], None, None)
            stream = mock.MagicMock()
            stream.__enter__.return_value = stream
            stream.__exit__.return_value = False
            return stream

        return factory

    @staticmethod
    def _continue(n):
        state = {"c": 0}

        def check():
            state["c"] += 1
            return state["c"] <= n

        return check

    def test_frames_of_the_utterance_are_offered_but_not_the_silence_before_it(self):
        silence = np.zeros((FRAME, 1), dtype=np.int16)
        speech = np.full((FRAME, 1), 1000, dtype=np.int16)
        listener = self._listener([False, False, True, True, False])
        seen, ended = [], []
        listener.on_utterance_frame = lambda frame: seen.append(frame.shape)
        listener.on_utterance_end = lambda: ended.append(1)
        with mock.patch("sounddevice.InputStream", side_effect=self._stream([silence, silence, speech, speech, silence])):
            gen = listener.listen_for_utterances(self._continue(5))
            utterance = next(gen)
            gen.close()
        self.assertEqual(len(seen), 3)  # 2 di parlato + 1 di coda: mai i 2 frame di silenzio iniziale
        self.assertEqual(ended, [1])
        self.assertEqual(len(utterance), FRAME * 3)

    def test_broken_callbacks_do_not_interrupt_listening(self):
        speech = np.full((FRAME, 1), 1000, dtype=np.int16)
        silence = np.zeros((FRAME, 1), dtype=np.int16)
        listener = self._listener([True, False])
        listener.on_utterance_frame = mock.MagicMock(side_effect=RuntimeError("x"))
        listener.on_utterance_end = mock.MagicMock(side_effect=RuntimeError("y"))
        with mock.patch("sounddevice.InputStream", side_effect=self._stream([speech, silence])):
            gen = listener.listen_for_utterances(self._continue(2))
            utterance = next(gen)
            gen.close()
        self.assertEqual(len(utterance), FRAME * 2)

    def test_without_callbacks_behaviour_is_unchanged(self):
        speech = np.full((FRAME, 1), 1000, dtype=np.int16)
        silence = np.zeros((FRAME, 1), dtype=np.int16)
        listener = self._listener([True, False])
        with mock.patch("sounddevice.InputStream", side_effect=self._stream([speech, silence])):
            gen = listener.listen_for_utterances(self._continue(2))
            utterance = next(gen)
            gen.close()
        self.assertEqual(len(utterance), FRAME * 2)


if __name__ == "__main__":
    unittest.main()
