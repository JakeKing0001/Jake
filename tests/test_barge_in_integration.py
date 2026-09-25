"""Collegamento del barge-in (F2.4.3-F2.4.5) e dell'AEC (F2.4.1) al percorso vocale reale: riferimento
audio pubblicato dai provider TTS, frame offerti mentre Jake parla, utterance seminata dal pre-roll,
e la decisione fermati/correggi/nuova richiesta dentro WakeWordSession. Tutto con finti: nessun
microfono, nessun altoparlante."""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from core.voice.audio_frontend import EchoCanceller
from core.voice.character_tts_provider import CharacterTtsProvider
from core.voice.edge_tts_provider import SAMPLE_RATE as EDGE_RATE
from core.voice.edge_tts_provider import EdgeTtsProvider
from core.voice.onecore_tts_provider import OneCoreTtsProvider
from core.voice.tts_provider import TtsProvider
from core.voice.utterance_segmenter import UtteranceSegmenter
from core.voice.vad_listener import VadListener
from core.voice.wake_word_session import WakeWordSession
from tests.voice_session_support import VoiceSessionTestCase, track
from core.turn_cancellation import current_turn_cancel_event

FRAME = VadListener.FRAME_SAMPLES

class CurrentTaskCancellationTests(VoiceSessionTestCase):
    def test_stop_is_heard_while_answer_is_still_running(self):
        core = _core()
        started = threading.Event()

        def blocked_answer(command):
            cancel_event = current_turn_cancel_event()
            self.assertIsNotNone(cancel_event)

            started.set()

            # Simula un task lungo ma cooperativo.
            self.assertTrue(cancel_event.wait(timeout=2))
            return "Questa risposta non deve essere pronunciata."

        core.answer.side_effect = blocked_answer

        session, _, _ = _session(core=core)
        session.stt_provider.transcribe.return_value = "Jake basta"

        with mock.patch.object(session, "_respond") as respond:
            session._process_command("fai un'operazione lunga")

            self.assertTrue(
                started.wait(timeout=1),
                "answer() non e' partito",
            )

            # Il listener deve poter processare questa frase anche se
            # answer() e' ancora attivo sul worker.
            session._handle_utterance(
                np.zeros(FRAME, dtype=np.float32)
            )

            worker = session._command_thread
            if worker is not None:
                worker.join(timeout=2)

        core.answer.assert_called_once_with(
            "fai un'operazione lunga"
        )

        # Solo il messaggio immediato di cancellazione.
        respond.assert_called_once_with(
            "Va bene, annullo."
        )

        self.assertEqual(session.state, "idle")


    def test_a_second_command_does_not_start_concurrently(self):
        core = _core()

        started = threading.Event()
        release = threading.Event()

        def blocked_answer(command):
            started.set()
            release.wait(timeout=2)
            return "Fatto."

        core.answer.side_effect = blocked_answer

        session, _, _ = _session(core=core)

        with mock.patch.object(session, "_respond"):
            session._process_command("primo comando")

            self.assertTrue(
                started.wait(timeout=1),
                "Il primo comando non e' partito",
            )

            session._process_command("secondo comando")

            # Il secondo non deve entrare nel core mentre il primo gira.
            self.assertEqual(core.answer.call_count, 1)
            core.answer.assert_called_once_with(
                "primo comando"
            )

            release.set()

            worker = session._command_thread
            if worker is not None:
                worker.join(timeout=2)

class ReferenceSinkTests(VoiceSessionTestCase):
    def test_edge_publishes_exactly_what_it_plays(self):
        provider = EdgeTtsProvider()
        provider.set_speech_params(0.5, 0)
        seen = []
        provider.reference_sink = lambda samples, rate: seen.append((samples.copy(), rate))
        with mock.patch("sounddevice.play") as play, mock.patch("sounddevice.wait"):
            provider._play(np.array([1000, -1000, 2000], dtype=np.int16))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][1], EDGE_RATE)
        played = play.call_args.args[0]
        np.testing.assert_array_equal(seen[0][0], played)  # il riferimento e' il segnale DOPO il volume
        self.assertEqual(played.tolist(), [500, -500, 1000])

    def test_no_sink_is_a_no_op(self):
        provider = EdgeTtsProvider()
        with mock.patch("sounddevice.play"), mock.patch("sounddevice.wait"):
            provider._play(np.array([1, 2, 3], dtype=np.int16))  # non solleva

    def test_a_failing_sink_never_stops_the_voice(self):
        provider = EdgeTtsProvider()
        provider.reference_sink = mock.MagicMock(side_effect=RuntimeError("osservatore rotto"))
        with mock.patch("sounddevice.play") as play, mock.patch("sounddevice.wait"):
            provider._play(np.array([1, 2, 3], dtype=np.int16))
        play.assert_called_once()

    def test_the_sink_is_called_before_playback_starts(self):
        provider = EdgeTtsProvider()
        order = []
        provider.reference_sink = lambda samples, rate: order.append("sink")
        with mock.patch("sounddevice.play", side_effect=lambda *a, **k: order.append("play")), mock.patch("sounddevice.wait"):
            provider._play(np.array([1, 2, 3], dtype=np.int16))
        self.assertEqual(order, ["sink", "play"])

    def test_onecore_publishes_with_its_own_sample_rate(self):
        import io
        import wave

        fake_synth = mock.MagicMock()
        fake_synth.all_voices = []
        with mock.patch("winsdk.windows.media.speechsynthesis.SpeechSynthesizer", fake_synth), \
                mock.patch("winsdk.windows.media.speechsynthesis.VoiceGender", mock.MagicMock()):
            provider = OneCoreTtsProvider()
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(np.array([100, 200], dtype=np.int16).tobytes())
        seen = []
        provider.reference_sink = lambda samples, rate: seen.append((samples.tolist(), rate))
        with mock.patch("sounddevice.play"), mock.patch("sounddevice.wait"):
            provider._play(buffer.getvalue())
        self.assertEqual(seen, [([100, 200], 22050)])

    def test_character_voice_publishes_what_it_plays(self):
        import io
        import wave

        provider = CharacterTtsProvider(mock.MagicMock(), mock.MagicMock())
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(np.array([7, 8], dtype=np.int16).tobytes())
        seen = []
        provider.reference_sink = lambda samples, rate: seen.append((samples.tolist(), rate))
        with mock.patch("sounddevice.play"), mock.patch("sounddevice.wait"):
            provider._play(buffer.getvalue())
        self.assertEqual(seen, [([7, 8], 16000)])

    def test_the_base_class_default_is_no_sink(self):
        self.assertIsNone(TtsProvider.reference_sink)


class SeekTests(VoiceSessionTestCase):
    def test_seek_moves_the_microphone_position(self):
        canceller = EchoCanceller()
        canceller.seek(1000)
        canceller.push_reference(np.ones(2000, dtype=np.float32))
        canceller.process(np.zeros(100, dtype=np.float32))
        self.assertEqual(canceller._pos, 1100)
        canceller.seek(-5)
        self.assertEqual(canceller._pos, 0)


class SegmenterSeedTests(VoiceSessionTestCase):
    def test_a_seeded_utterance_includes_the_preroll_and_ends_on_silence(self):
        segmenter = UtteranceSegmenter(silence_frames_needed=2, max_frames=100)
        segmenter.seed([np.full((FRAME, 1), 500, dtype=np.int16)] * 3)
        self.assertTrue(segmenter.in_speech)
        self.assertEqual(segmenter.buffered_frames, 3)
        self.assertIsNone(segmenter.feed(np.zeros((FRAME, 1), dtype=np.int16), False))
        utterance = segmenter.feed(np.zeros((FRAME, 1), dtype=np.int16), False)
        assert utterance is not None
        self.assertEqual(len(utterance), FRAME * 5)

    def test_seeding_with_nothing_is_not_speech(self):
        segmenter = UtteranceSegmenter(2, 100)
        segmenter.seed([])
        self.assertFalse(segmenter.in_speech)

    def test_seed_copies_the_frames(self):
        segmenter = UtteranceSegmenter(1, 100)
        frame = np.full((FRAME, 1), 5, dtype=np.int16)
        segmenter.seed([frame])
        frame[:] = 0
        utterance = segmenter.flush()
        assert utterance is not None
        self.assertGreater(float(np.max(utterance)), 0)


def _make_listener(is_speech_sequence):
    fake_vad = mock.MagicMock()
    fake_vad.is_speech.side_effect = is_speech_sequence
    with mock.patch("webrtcvad.Vad", return_value=fake_vad):
        return VadListener(silence_ms=30)


def _input_stream(frames):
    def factory(**kwargs):
        callback = kwargs["callback"]
        for frame in frames:
            callback(frame, frame.shape[0], None, None)
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.__exit__.return_value = False
        return stream

    return factory


def _continue(n):
    state = {"c": 0}

    def check():
        state["c"] += 1
        return state["c"] <= n

    return check


class VadListenerSpeakingFramesTests(VoiceSessionTestCase):
    def test_frames_while_muted_are_offered_to_the_callback_but_never_yield_an_utterance(self):
        speech = np.full((FRAME, 1), 3000, dtype=np.int16)
        listener = _make_listener([True, True, True])
        listener.muted = True
        seen = []
        listener.on_speaking_frame = lambda frame, is_speech, level: seen.append((frame.shape, is_speech, round(level, 3)))
        with mock.patch("sounddevice.InputStream", side_effect=_input_stream([speech] * 3)):
            gen = listener.listen_for_utterances(_continue(3))
            with self.assertRaises(StopIteration):
                next(gen)
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen[0][0], (FRAME,))
        self.assertTrue(seen[0][1])
        self.assertAlmostEqual(seen[0][2], 3000 / 32768, places=3)

    def test_without_a_callback_muted_frames_are_dropped_exactly_as_before(self):
        speech = np.full((FRAME, 1), 3000, dtype=np.int16)
        listener = _make_listener([True, True])
        listener.muted = True
        with mock.patch("sounddevice.InputStream", side_effect=_input_stream([speech] * 2)):
            with self.assertRaises(StopIteration):
                next(listener.listen_for_utterances(_continue(2)))

    def test_a_broken_callback_does_not_interrupt_listening(self):
        speech = np.full((FRAME, 1), 3000, dtype=np.int16)
        listener = _make_listener([True, True])
        listener.muted = True
        listener.on_speaking_frame = mock.MagicMock(side_effect=RuntimeError("boom"))
        with mock.patch("sounddevice.InputStream", side_effect=_input_stream([speech] * 2)):
            with self.assertRaises(StopIteration):
                next(listener.listen_for_utterances(_continue(2)))
        self.assertEqual(listener.on_speaking_frame.call_count, 2)

    def test_begin_utterance_from_the_callback_turns_the_rest_of_the_speech_into_an_utterance(self):
        speech = np.full((FRAME, 1), 3000, dtype=np.int16)
        silence = np.zeros((FRAME, 1), dtype=np.int16)
        listener = _make_listener([True, True, False])
        listener.muted = True
        preroll = [np.full((FRAME, 1), 111, dtype=np.int16)] * 2

        def on_frame(frame, is_speech, level):
            listener.begin_utterance(preroll)  # il barge-in scatta al primo frame

        listener.on_speaking_frame = on_frame
        with mock.patch("sounddevice.InputStream", side_effect=_input_stream([speech, speech, silence])):
            gen = listener.listen_for_utterances(_continue(3))
            utterance = next(gen)
            gen.close()
        self.assertFalse(listener.muted)
        self.assertEqual(utterance.dtype, np.float32)
        # 2 frame di pre-roll + il frame corrente + il secondo + la coda di silenzio
        self.assertEqual(len(utterance), FRAME * 5)
        self.assertAlmostEqual(float(utterance[0]), 111 / 32768, places=5)  # inizia dal pre-roll

    def test_is_speech_pcm_uses_the_vad(self):
        listener = _make_listener([True, False])
        self.assertTrue(listener.is_speech_pcm(b"\x00" * FRAME * 2))
        self.assertFalse(listener.is_speech_pcm(b"\x00" * FRAME * 2))

    def test_begin_utterance_before_listening_only_unmutes(self):
        listener = _make_listener([])
        listener.muted = True
        listener.begin_utterance([np.zeros((FRAME, 1), dtype=np.int16)])
        self.assertFalse(listener.muted)


class FakeTts:
    def __init__(self):
        self.spoken = []
        self.stopped = threading.Event()
        self.started = threading.Event()
        self.reference_sink = None

    def speak(self, text):
        self.spoken.append(text)
        self.started.set()
        self.stopped.wait(5)

    def stop(self):
        self.stopped.set()


class FakeVad:
    """VadListener finto: registra begin_utterance e risponde al VAD del residuo con un valore fisso."""

    SAMPLE_RATE = 16000
    FRAME_SAMPLES = FRAME

    def __init__(self, speech=True):
        self.muted = False
        self.on_level = None
        self.on_speaking_frame = None
        self.speech = speech
        self.begun = []

    def is_speech_pcm(self, pcm):
        return self.speech

    def begin_utterance(self, frames):
        self.muted = False
        self.begun.append(frames)


def _core():
    core = mock.MagicMock(EXIT_SENTINEL="ESCI")
    core.conversation_state.has_pending_action.return_value = False
    core.answer.return_value = "Fatto."
    return core


def _session(barge_in="on", **kwargs):
    tts = kwargs.pop("tts", None) or FakeTts()
    vad = kwargs.pop("vad", None) or FakeVad()
    core = kwargs.pop("core", None) or _core()
    stt = kwargs.pop("stt", None) or mock.MagicMock()
    session = track(WakeWordSession(core, stt, tts, vad_listener=vad, barge_in=barge_in, **kwargs))
    return session, tts, vad


def _speaking_session(barge_in="on", **kwargs):
    session, tts, vad = _session(barge_in, **kwargs)
    session._speak_async("Questa e' una risposta abbastanza lunga da far parlare Jake per un po'.")
    assert tts.started.wait(5)
    return session, tts, vad


def _frame(level=0.2):
    return (np.full(FRAME, level * 32768, dtype=np.float32)).astype(np.int16)


class BargeInSessionTests(VoiceSessionTestCase):
    def tearDown(self):
        pass

    def test_off_by_default_nothing_happens(self):
        session, tts, vad = _speaking_session(barge_in="off")
        for _ in range(30):
            vad.on_speaking_frame(_frame(), True, 0.2)
        self.assertFalse(tts.stopped.is_set())
        self.assertEqual(vad.begun, [])
        tts.stop()

    def test_the_session_hooks_the_listener_and_the_provider(self):
        session, tts, vad = _session()
        self.assertEqual(vad.on_speaking_frame, session._on_speaking_frame)
        self.assertEqual(tts.reference_sink, session.playback_aec.push_reference)

    def test_sustained_user_speech_stops_jake_and_starts_capturing(self):
        session, tts, vad = _speaking_session()
        for _ in range(12):
            vad.on_speaking_frame(_frame(), True, 0.2)
            if tts.stopped.is_set():
                break
        self.assertTrue(tts.stopped.is_set())
        self.assertEqual(len(vad.begun), 1)
        self.assertGreaterEqual(len(vad.begun[0]), 8)  # il pre-roll con l'inizio delle parole
        self.assertIsNotNone(session._interrupted_turn)
        self.assertGreater(session.listening.command_until, time.time())  # la frase che segue non vuole "Jake"

    def test_silence_or_quiet_echo_does_not_interrupt(self):
        session, tts, vad = _speaking_session()
        for _ in range(60):
            vad.on_speaking_frame(_frame(0.002), False, 0.002)
        self.assertFalse(tts.stopped.is_set())
        tts.stop()

    def test_a_frame_when_jake_is_not_speaking_never_interrupts(self):
        session, _, vad = _session()
        for _ in range(60):
            vad.on_speaking_frame(_frame(), True, 0.2)
        self.assertEqual(vad.begun, [])

    def test_auto_mode_needs_headphones(self):
        speakers, _, vad = _speaking_session(barge_in="auto", output_device_name="Speakers (Realtek)")
        for _ in range(30):
            vad.on_speaking_frame(_frame(), True, 0.2)
        self.assertEqual(vad.begun, [])
        speakers._tts_thread.join(0.01)

        headphones, tts2, vad2 = _speaking_session(barge_in="auto", output_device_name="Cuffie USB")
        for _ in range(12):
            vad2.on_speaking_frame(_frame(), True, 0.2)
            if tts2.stopped.is_set():
                break
        self.assertEqual(len(vad2.begun), 1)

    def test_an_unknown_mode_falls_back_to_off(self):
        session, _, _ = _session(barge_in="sempre")
        self.assertEqual(session.barge_in_mode, "off")

    def test_with_a_published_reference_the_grace_period_ignores_the_echo(self):
        """Riferimento presente ma AEC non ancora calibrata: un eco forte NON deve interrompere."""
        session, tts, vad = _speaking_session()
        session.playback_aec.push_reference(np.full(16000, 8000, dtype=np.int16), 16000)
        for _ in range(20):  # 0,6 s: ancora nel periodo di calibrazione (~1,3 s)
            vad.on_speaking_frame(_frame(0.3), True, 0.3)
        self.assertFalse(tts.stopped.is_set())
        self.assertFalse(session.playback_aec.ready)
        tts.stop()

    def test_a_new_answer_resets_the_aec_and_the_detector(self):
        session, tts, _ = _speaking_session()
        session.playback_aec.push_reference(np.ones(100, dtype=np.int16), 16000)
        self.assertTrue(session.playback_aec.has_reference)
        tts.stop()
        session._tts_thread.join(timeout=5)
        session._speak_async("Una nuova risposta abbastanza lunga da parlare.")
        time.sleep(0.05)
        self.assertFalse(session.playback_aec.has_reference)
        session._interrupt_speech()


class InterruptionOutcomeTests(VoiceSessionTestCase):
    def _interrupted(self, text):
        session, tts, vad = _session()
        session._interrupted_turn = SimpleNamespace(turn_id=2)
        session._interrupted_deadline = time.time() + 10
        session.stt_provider.transcribe.return_value = text
        with mock.patch.object(session, "_respond") as respond:
            session._handle_utterance(object())
            worker = session._command_thread

            if worker is not None:
                worker.join(timeout=2)
        return session, respond

    def test_a_bare_stop_stops_and_executes_nothing(self):
        for text in ("basta", "no", "Jake fermati", "aspetta"):
            with self.subTest(text=text):
                session, respond = self._interrupted(text)
                session.jake_core.answer.assert_not_called()
                respond.assert_not_called()
                self.assertIsNone(session._interrupted_turn)

    def test_a_correction_runs_only_the_useful_text(self):
        session, _ = self._interrupted("no, intendevo apri chrome")
        session.jake_core.answer.assert_called_once_with("apri chrome")

    def test_a_new_request_runs_without_the_wake_word(self):
        session, _ = self._interrupted("che ore sono")
        session.jake_core.answer.assert_called_once_with("che ore sono")

    def test_a_wake_word_prefix_is_stripped_from_the_request(self):
        session, _ = self._interrupted("Jake che ore sono")
        session.jake_core.answer.assert_called_once_with("che ore sono")

    def test_an_expired_interruption_is_handled_like_any_other_phrase(self):
        session, tts, vad = _session()
        session._interrupted_turn = SimpleNamespace(turn_id=2)
        session._interrupted_deadline = time.time() - 1
        session.stt_provider.transcribe.return_value = "basta"
        session._handle_utterance(object())
        session.jake_core.answer.assert_not_called()  # niente wake word, niente finestra: ignorata come sempre
        self.assertIsNone(session._interrupted_turn)

    def test_an_empty_transcription_clears_the_pending_interruption(self):
        session, _, _ = _session()
        session._interrupted_turn = SimpleNamespace(turn_id=2)
        session._interrupted_deadline = time.time() + 10
        session.stt_provider.transcribe.return_value = ""
        session._handle_utterance(object())
        self.assertIsNone(session._interrupted_turn)

    def test_without_a_pending_interruption_a_stop_word_is_not_special(self):
        session, _, _ = _session()
        session.stt_provider.transcribe.return_value = "basta"
        session._handle_utterance(object())
        session.jake_core.answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
