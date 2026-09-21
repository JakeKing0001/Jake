"""Test per core/voice/live_transcriber.py (F2.2.1, F2.2.4): partial su un thread proprio, "l'ultimo vince",
nessun blocco del thread di ascolto. Thread veri, provider finto controllato da Event (mai sleep a caso
per la logica: solo attese con timeout per non bloccare la suite se qualcosa si rompe)."""
import threading
import time
import unittest

import numpy as np

from core.voice.live_transcriber import LiveTranscriber
from core.voice.streaming_stt import KIND_FINAL, KIND_PARTIAL

SR = 16000
TIMEOUT = 5.0


def frame(ms=30, value=1000):
    return np.full(SR * ms // 1000, value, dtype=np.int16)


class Provider:
    def __init__(self, hypotheses=None, block=None):
        self.hypotheses = list(hypotheses or ["apri", "apri spotify", "apri spotify e metti"])
        self.calls = []  # lunghezze in campioni dell'audio decodificato
        self.block = block  # threading.Event: se dato, la decodifica resta ferma finche' non e' settato
        self.entered = threading.Event()

    def transcribe(self, audio, sample_rate):
        self.calls.append(len(audio))
        self.entered.set()
        if self.block is not None:
            self.block.wait(TIMEOUT)
        return self.hypotheses.pop(0) if len(self.hypotheses) > 1 else self.hypotheses[0]


class Collector:
    def __init__(self):
        self.events = []
        self.got = threading.Event()

    def __call__(self, event):
        self.events.append(event)
        self.got.set()


def feed_seconds(live, seconds):
    for _ in range(int(seconds * 1000 / 30)):
        live.feed(frame())


class PartialsTests(unittest.TestCase):
    def test_partials_arrive_from_a_worker_thread_with_growing_revisions(self):
        collector = Collector()
        live = LiveTranscriber(Provider(), collector, interval_s=0.3)
        self.addCleanup(live.close)
        live.start_utterance()
        feed_seconds(live, 0.4)
        self.assertTrue(collector.got.wait(TIMEOUT))
        collector.got.clear()
        feed_seconds(live, 0.4)
        self.assertTrue(collector.got.wait(TIMEOUT))
        self.assertEqual([e.revision for e in collector.events], [1, 2])
        self.assertTrue(all(e.kind == KIND_PARTIAL for e in collector.events))
        self.assertEqual({e.utterance_id for e in collector.events}, {live.utterance_id})

    def test_nothing_is_decoded_before_a_full_interval_of_audio(self):
        provider = Provider()
        live = LiveTranscriber(provider, Collector(), interval_s=0.6)
        self.addCleanup(live.close)
        live.start_utterance()
        feed_seconds(live, 0.3)
        time.sleep(0.3)
        self.assertEqual(provider.calls, [])

    def test_audio_outside_an_utterance_is_ignored(self):
        provider = Provider()
        live = LiveTranscriber(provider, Collector(), interval_s=0.1)
        self.addCleanup(live.close)
        feed_seconds(live, 1.0)  # nessun start_utterance
        time.sleep(0.3)
        self.assertEqual(provider.calls, [])

    def test_frames_can_be_int16_or_float32(self):
        provider = Provider()
        live = LiveTranscriber(provider, Collector(), interval_s=0.1)
        self.addCleanup(live.close)
        live.start_utterance()
        for _ in range(5):
            live.feed(np.full(480, 0.1, dtype=np.float32))
        deadline = time.time() + TIMEOUT
        while not provider.calls and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(provider.calls)


class NeverBlocksTheListeningThreadTests(unittest.TestCase):
    def test_feed_returns_immediately_while_a_decode_is_stuck(self):
        block = threading.Event()
        provider = Provider(block=block)
        live = LiveTranscriber(provider, Collector(), interval_s=0.1)
        self.addCleanup(live.close)
        self.addCleanup(block.set)
        live.start_utterance()
        feed_seconds(live, 0.2)
        self.assertTrue(provider.entered.wait(TIMEOUT))  # la decodifica e' bloccata dentro il modello
        started = time.perf_counter()
        feed_seconds(live, 2.0)  # 66 frame mentre il modello e' occupato
        self.assertLess(time.perf_counter() - started, 0.5)
        self.assertGreater(live.skipped_decodes, 0)

    def test_the_latest_wins_only_one_decode_covers_everything_that_piled_up(self):
        block = threading.Event()
        provider = Provider(block=block)
        collector = Collector()
        live = LiveTranscriber(provider, collector, interval_s=0.1)
        self.addCleanup(live.close)
        self.addCleanup(block.set)
        live.start_utterance()
        feed_seconds(live, 0.2)
        self.assertTrue(provider.entered.wait(TIMEOUT))
        first_len = provider.calls[0]
        feed_seconds(live, 3.0)  # 3 s accumulati mentre la prima decodifica e' ferma
        block.set()
        deadline = time.time() + TIMEOUT
        while len(provider.calls) < 2 and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)
        # UNA sola decodifica successiva, su tutto l'audio (non una ogni 0,1 s di audio accumulato)
        self.assertEqual(len(provider.calls), 2)
        self.assertGreater(provider.calls[1], first_len + int(2.9 * SR))


class UtteranceLifecycleTests(unittest.TestCase):
    def test_a_partial_finishing_after_the_utterance_ended_is_dropped(self):
        block = threading.Event()
        provider = Provider(block=block)
        collector = Collector()
        live = LiveTranscriber(provider, collector, interval_s=0.1)
        self.addCleanup(live.close)
        self.addCleanup(block.set)
        live.start_utterance()
        feed_seconds(live, 0.2)
        self.assertTrue(provider.entered.wait(TIMEOUT))
        live.end_utterance()
        block.set()
        time.sleep(0.3)
        self.assertEqual(collector.events, [])  # un partial dopo il final sarebbe rumore per HUD e companion

    def test_starting_a_new_utterance_during_a_decode_does_not_leak_the_old_events(self):
        block = threading.Event()
        provider = Provider(hypotheses=["vecchia frase"], block=block)
        collector = Collector()
        live = LiveTranscriber(provider, collector, interval_s=0.1)
        self.addCleanup(live.close)
        self.addCleanup(block.set)
        live.start_utterance()
        old_id = live.utterance_id
        feed_seconds(live, 0.2)
        self.assertTrue(provider.entered.wait(TIMEOUT))
        live.start_utterance()  # non aspetta la decodifica in corso
        self.assertNotEqual(live.utterance_id, old_id)
        block.set()
        time.sleep(0.3)
        self.assertNotIn(old_id, {e.utterance_id for e in collector.events})

    def test_end_utterance_gives_the_id_and_the_next_revision_for_the_final_event(self):
        collector = Collector()
        live = LiveTranscriber(Provider(), collector, interval_s=0.2)
        self.addCleanup(live.close)
        live.start_utterance()
        for _ in range(2):
            collector.got.clear()
            feed_seconds(live, 0.3)
            self.assertTrue(collector.got.wait(TIMEOUT))
        utterance_id, revision = live.end_utterance()
        self.assertEqual(utterance_id, collector.events[0].utterance_id)
        self.assertEqual(revision, collector.events[-1].revision + 1)
        final = live.final_event("apri spotify", 0.9, utterance_id, revision)
        self.assertEqual((final.kind, final.text, final.stable_text, final.confidence), (KIND_FINAL, "apri spotify", "apri spotify", 0.9))

    def test_end_utterance_is_idempotent_and_stops_further_feeding(self):
        provider = Provider()
        live = LiveTranscriber(provider, Collector(), interval_s=0.1)
        self.addCleanup(live.close)
        live.start_utterance()
        first = live.end_utterance()
        self.assertEqual(live.end_utterance(), first)
        feed_seconds(live, 1.0)
        time.sleep(0.2)
        self.assertEqual(provider.calls, [])


class ResilienceTests(unittest.TestCase):
    def test_a_slow_model_degrades_the_utterance_to_final_only(self):
        now = {"t": 0.0}

        class Slow(Provider):
            def transcribe(self, audio, sample_rate):
                now["t"] += 5.0  # ogni decodifica "costa" 5 s di orologio finto
                return super().transcribe(audio, sample_rate)

        collector = Collector()
        live = LiveTranscriber(Slow(), collector, interval_s=0.1, partial_budget_s=1.0, clock=lambda: now["t"])
        self.addCleanup(live.close)
        live.start_utterance()
        feed_seconds(live, 0.3)
        deadline = time.time() + TIMEOUT
        while not live.degraded and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(live.degraded)
        self.assertEqual(collector.events, [])

    def test_a_broken_event_callback_does_not_kill_the_worker(self):
        calls = []

        def broken(event):
            calls.append(event)
            raise RuntimeError("HUD rotto")

        provider = Provider(hypotheses=["a", "a b", "a b c"])
        live = LiveTranscriber(provider, broken, interval_s=0.1)
        self.addCleanup(live.close)
        live.start_utterance()
        for _ in range(3):
            feed_seconds(live, 0.2)
            time.sleep(0.15)
        deadline = time.time() + TIMEOUT
        while len(calls) < 2 and time.time() < deadline:
            feed_seconds(live, 0.2)
            time.sleep(0.1)
        self.assertGreaterEqual(len(calls), 2)

    def test_the_model_lock_serialises_partials_with_the_final_transcription(self):
        lock = threading.Lock()
        provider = Provider()
        live = LiveTranscriber(provider, Collector(), model_lock=lock, interval_s=0.1)
        self.addCleanup(live.close)
        live.start_utterance()
        with lock:  # la trascrizione finale sta usando il modello
            feed_seconds(live, 0.3)
            time.sleep(0.3)
            self.assertEqual(provider.calls, [])  # il partial aspetta: mai due decodifiche insieme
        deadline = time.time() + TIMEOUT
        while not provider.calls and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(provider.calls)

    def test_close_stops_the_worker_thread(self):
        live = LiveTranscriber(Provider(), Collector())
        live.close()
        self.assertFalse(live._worker.is_alive())

    def test_detailed_confidence_is_forwarded(self):
        class Detailed(Provider):
            def transcribe_detailed(self, audio, sample_rate):
                return "apri", 0.77

        collector = Collector()
        live = LiveTranscriber(Detailed(), collector, interval_s=0.1)
        self.addCleanup(live.close)
        live.start_utterance()
        feed_seconds(live, 0.3)
        self.assertTrue(collector.got.wait(TIMEOUT))
        self.assertEqual(collector.events[0].confidence, 0.77)


if __name__ == "__main__":
    unittest.main()
