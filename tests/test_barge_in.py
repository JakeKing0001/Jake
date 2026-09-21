"""Test per core/voice/barge_in.py (F2.4.3-F2.4.5) e per il simulatore di benchmarks/bench_barge_in.py
(F2.4.6, F2.4.7). Il rilevatore e il controllore ricevono caratteristiche gia' calcolate; il
simulatore usa percorsi d'eco parametrici e il webrtcvad VERO."""
import threading
import time
import unittest

import numpy as np

from benchmarks.bench_barge_in import SCENARIOS, simulate_trial
from core.voice.barge_in import (
    BargeInController, BargeInDetector, PreRollBuffer, TurnLog, classify_interruption,
)
from core.voice.chunked_speaker import ChunkedSpeaker


class DetectorTests(unittest.TestCase):
    def _feed(self, detector, frames, speaking=True, ref=0.1):
        """frames: lista di (is_speech, level). Ritorna l'indice del frame che ha fatto scattare."""
        for index, (is_speech, level) in enumerate(frames):
            if detector.update(is_speech, level, ref, speaking):
                return index
        return None

    def test_sustained_user_speech_over_the_threshold_triggers(self):
        self.assertEqual(self._feed(BargeInDetector(min_frames=8), [(True, 0.08)] * 12), 7)

    def test_residual_echo_below_the_margin_never_triggers(self):
        # VAD dice "parlato" (e' la voce di Jake) ma il livello e' quello dell'eco residuo
        self.assertIsNone(self._feed(BargeInDetector(), [(True, 0.02)] * 200, ref=0.1))

    def test_a_loud_click_without_speech_does_not_trigger(self):
        self.assertIsNone(self._feed(BargeInDetector(), [(False, 0.5)] * 50))

    def test_never_triggers_when_jake_is_not_speaking(self):
        self.assertIsNone(self._feed(BargeInDetector(), [(True, 0.5)] * 50, speaking=False))

    def test_short_pauses_between_words_are_tolerated(self):
        frames = [(True, 0.08)] * 4 + [(False, 0.0)] * 2 + [(True, 0.08)] * 6
        # 4 + 6 frame di parlato = 10 utili: la pausa non azzera il conteggio, l'ottavo utile e' l'indice 9
        self.assertEqual(self._feed(BargeInDetector(min_frames=8, max_gap=2), frames), 9)

    def test_a_longer_gap_resets_the_count(self):
        frames = [(True, 0.08)] * 6 + [(False, 0.0)] * 3 + [(True, 0.08)] * 6
        self.assertIsNone(self._feed(BargeInDetector(min_frames=8, max_gap=2), frames))

    def test_a_short_burst_below_the_minimum_does_not_trigger(self):
        self.assertIsNone(self._feed(BargeInDetector(min_frames=8), [(True, 0.08)] * 5 + [(False, 0.0)] * 20))

    def test_the_threshold_follows_the_reference_level(self):
        d = BargeInDetector(echo_ratio=0.35)
        self.assertIsNone(self._feed(d, [(True, 0.05)] * 30, ref=0.4))  # eco forte: 0.05 e' sotto 0.14
        self.assertIsNotNone(self._feed(BargeInDetector(echo_ratio=0.35), [(True, 0.05)] * 30, ref=0.05))

    def test_the_absolute_floor_protects_a_silent_reference(self):
        self.assertIsNone(self._feed(BargeInDetector(min_level=0.01), [(True, 0.004)] * 50, ref=0.0))

    def test_it_can_trigger_again_after_a_reset(self):
        d = BargeInDetector(min_frames=3)
        self.assertEqual(self._feed(d, [(True, 0.08)] * 5), 2)
        self.assertEqual(self._feed(d, [(True, 0.08)] * 5), 2)


class PreRollTests(unittest.TestCase):
    def test_keeps_only_the_latest_frames(self):
        buf = PreRollBuffer(max_ms=90, frame_ms=30)
        for value in range(6):
            buf.push(np.full(4, value, dtype=np.float32))
        self.assertEqual(len(buf), 3)
        self.assertEqual(buf.drain().tolist(), [3.0] * 4 + [4.0] * 4 + [5.0] * 4)

    def test_drain_empties_the_buffer(self):
        buf = PreRollBuffer()
        buf.push(np.ones(10, dtype=np.float32))
        buf.drain()
        self.assertEqual(len(buf), 0)
        self.assertEqual(buf.drain().size, 0)

    def test_stores_a_copy_not_a_view(self):
        buf = PreRollBuffer()
        frame = np.ones(4, dtype=np.float32)
        buf.push(frame)
        frame[:] = 9
        self.assertEqual(buf.drain().tolist(), [1.0] * 4)


class FakeTts:
    def __init__(self):
        self.spoken = []
        self.started = threading.Event()
        self._stop = threading.Event()

    def speak(self, text):
        self.spoken.append(text)
        self.started.set()
        while not self._stop.is_set():
            time.sleep(0.005)

    def stop(self):
        self._stop.set()


class ControllerTests(unittest.TestCase):
    def _controller(self):
        tts = FakeTts()
        speaker = ChunkedSpeaker(tts)
        speaker.feed("Questa e' la prima frase abbastanza lunga da parlare. Questa e' la seconda frase in coda qui. E questa la terza.")
        self.assertTrue(tts.started.wait(5))
        now = {"t": 10.0}
        controller = BargeInController(speaker, detector=BargeInDetector(min_frames=3), clock=lambda: now["t"])
        controller.begin_response()
        return controller, speaker, tts

    def _speak_over(self, controller, frames=3, level=0.08):
        turn = None
        for _ in range(frames):
            turn = controller.on_frame(np.full(480, 0.1, dtype=np.float32), True, level, 0.1, True) or turn
        return turn

    def test_a_barge_in_stops_the_speech_and_drops_the_queue(self):
        controller, speaker, tts = self._controller()
        turn = self._speak_over(controller)
        self.assertIsNotNone(turn)
        self.assertTrue(speaker.cancelled)
        self.assertGreaterEqual(speaker.units_dropped, 1)
        self.assertTrue(speaker.wait(5))
        self.assertEqual(len(tts.spoken), 1)  # nessuna unita' accodata detta dopo l'interruzione

    def test_the_new_turn_is_correlated_to_the_interrupted_one(self):
        controller, _, _ = self._controller()
        first = controller.turns.current
        turn = self._speak_over(controller)
        assert turn is not None and first is not None
        self.assertEqual((turn.kind, turn.parent_id), ("interruption", first.turn_id))
        self.assertGreater(turn.turn_id, first.turn_id)
        self.assertGreaterEqual(turn.interrupted_dropped, 1)

    def test_no_barge_in_when_nothing_crosses_the_threshold(self):
        controller, speaker, _ = self._controller()
        self.assertIsNone(self._speak_over(controller, frames=20, level=0.01))
        self.assertFalse(speaker.cancelled)
        self.assertEqual(controller.barge_in_count, 0)

    def test_the_preroll_keeps_the_start_of_the_users_words(self):
        controller, _, _ = self._controller()
        self._speak_over(controller)
        audio = controller.take_preroll()
        self.assertGreaterEqual(audio.size, 3 * 480)
        self.assertEqual(controller.take_preroll().size, 0)  # consumato, non resta in memoria

    def test_stop_latency_is_measured_and_fast(self):
        controller, _, _ = self._controller()
        self._speak_over(controller)
        assert controller.last_stop_latency_s is not None
        self.assertLess(controller.last_stop_latency_s, 0.3)

    def test_callback_receives_the_interruption_turn(self):
        controller, _, _ = self._controller()
        seen = []
        controller.on_barge_in = seen.append
        self._speak_over(controller)
        self.assertEqual([t.kind for t in seen], ["interruption"])

    def test_it_never_triggers_when_jake_is_silent(self):
        controller, speaker, _ = self._controller()
        for _ in range(20):
            self.assertIsNone(controller.on_frame(np.zeros(480, dtype=np.float32), True, 0.5, 0.1, False))
        self.assertFalse(speaker.cancelled)

    def test_turn_log_numbers_turns_and_tracks_the_current_one(self):
        log = TurnLog()
        self.assertIsNone(log.current)
        a = log.start(1.0)
        b = log.start(2.0, parent=a, kind="interruption")
        self.assertEqual((a.turn_id, b.turn_id, b.parent_id), (1, 2, 1))
        self.assertIs(log.current, b)


class ClassifyInterruptionTests(unittest.TestCase):
    def test_stop_phrases(self):
        for text in ("basta", "Basta così", "Jake, stop", "fermati", "zitto", "silenzio", "lascia stare", "ok basta", "never mind", "shut up"):
            with self.subTest(text=text):
                self.assertEqual(classify_interruption(text).kind, "stop")

    def test_a_lone_no_or_wait_is_a_stop_not_a_correction(self):
        for text in ("no", "No.", "aspetta", "un attimo", "wait", "hold on"):
            with self.subTest(text=text):
                self.assertEqual(classify_interruption(text), classify_interruption("basta"))

    def test_a_bare_wake_word_is_a_stop(self):
        self.assertEqual(classify_interruption("Jake").kind, "stop")
        self.assertEqual(classify_interruption("").kind, "stop")

    def test_corrections_keep_the_new_content(self):
        cases = {
            "no, intendevo Spotify": "Spotify",
            "No no, apri Chrome": "apri Chrome",
            "scusa, volevo dire domani": "domani",
            "anzi metti un timer di cinque minuti": "metti un timer di cinque minuti",
            "cioe' il volume al cinquanta": "il volume al cinquanta",
            "I meant tomorrow": "tomorrow",
            "actually open Chrome": "open Chrome",
        }
        for text, remainder in cases.items():
            with self.subTest(text=text):
                result = classify_interruption(text)
                self.assertEqual((result.kind, result.remainder), ("correction", remainder))

    def test_continue(self):
        for text in ("continua", "vai avanti", "Jake, riprendi", "go on"):
            with self.subTest(text=text):
                self.assertEqual(classify_interruption(text).kind, "continue")

    def test_everything_else_is_a_new_request_without_the_wake_word(self):
        result = classify_interruption("Jake che ore sono")
        self.assertEqual((result.kind, result.remainder), ("new_request", "che ore sono"))
        self.assertEqual(classify_interruption("apri spotify").kind, "new_request")

    def test_a_request_that_merely_starts_with_a_stop_word_is_not_a_stop(self):
        self.assertEqual(classify_interruption("stop al timer di cucina").kind, "new_request")
        self.assertEqual(classify_interruption("basta guardare le email, apri il calendario").kind, "new_request")


class SimulatorTests(unittest.TestCase):
    """Le condizioni note del benchmark (F2.4.6/F2.4.7) diventano regressioni: se il codice cambia e
    il comportamento su un profilo cambia, un test lo dice."""

    def _by_name(self, name):
        return next(s for s in SCENARIOS if s.name == name)

    def test_the_four_device_families_exist(self):
        self.assertEqual({s.name for s in SCENARIOS}, {"headphones", "laptop_speaker", "bluetooth_180ms", "tv_background"})

    def test_laptop_speaker_with_aec_hears_the_user_and_ignores_its_own_echo(self):
        scenario = self._by_name("laptop_speaker")
        hit = simulate_trial(scenario, seed=0, user_speaks=True, aec=True)
        self.assertTrue(hit["triggered"])
        self.assertLess(hit["latency_ms"], 1000)
        self.assertFalse(simulate_trial(scenario, seed=1000, user_speaks=False, aec=True)["false_barge_in"])

    def test_without_aec_the_laptop_speaker_barges_in_on_itself(self):
        """La ragione d'essere dell'AEC: senza, Jake si interrompe da solo."""
        result = simulate_trial(self._by_name("laptop_speaker"), seed=1000, user_speaks=False, aec=False)
        self.assertTrue(result["false_barge_in"])

    def test_bluetooth_delay_is_handled_by_the_delay_estimate(self):
        scenario = self._by_name("bluetooth_180ms")
        self.assertTrue(simulate_trial(scenario, seed=0, user_speaks=True, aec=True)["triggered"])
        self.assertFalse(simulate_trial(scenario, seed=1000, user_speaks=False, aec=True)["false_barge_in"])

    def test_headphones_barely_leak_so_no_false_barge_in_either_way(self):
        for aec in (True, False):
            self.assertFalse(simulate_trial(self._by_name("headphones"), seed=1000, user_speaks=False, aec=aec)["false_barge_in"])

    def test_a_loud_tv_in_the_background_is_a_KNOWN_limit_the_aec_cannot_fix(self):
        """La TV non e' nel riferimento: nessun filtro adattivo la cancella. Il test FISSA il limite
        (falso barge-in anche con AEC) invece di nasconderlo; se un giorno una separazione degli
        altoparlanti lo risolve, questo test va aggiornato di proposito."""
        self.assertTrue(simulate_trial(self._by_name("tv_background"), seed=1000, user_speaks=False, aec=True)["false_barge_in"])


if __name__ == "__main__":
    unittest.main()
