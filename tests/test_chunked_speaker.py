"""Test per core/voice/chunked_speaker.py (F2.5.2-F2.5.4). Il motore TTS e' finto: registra le unita'
e, se richiesto, resta "in riproduzione" finche' non riceve stop() o un permesso esplicito, cosi' i
casi di interruzione si provano con thread veri ma senza sleep a caso."""
import threading
import time
import unittest

from core.voice.chunked_speaker import ChunkedSpeaker
from core.voice.speech_text import CODE_OMITTED, STYLES

TIMEOUT = 5.0


class FakeTts:
    def __init__(self, block_first: bool = False):
        self.spoken: list[str] = []
        self.stop_calls = 0
        self.block_first = block_first
        self.started = threading.Event()
        self._release = threading.Event()
        self._stopped = threading.Event()

    def speak(self, text):
        self.spoken.append(text)
        self.started.set()
        if self.block_first and len(self.spoken) == 1:
            # "in riproduzione" finche' non arriva stop() o release()
            while not (self._stopped.is_set() or self._release.is_set()):
                time.sleep(0.005)

    def stop(self):
        self.stop_calls += 1
        self._stopped.set()

    def release(self):
        self._release.set()


class StreamingBehaviourTests(unittest.TestCase):
    def test_speaks_the_first_sentence_before_the_answer_is_finished(self):
        tts = FakeTts()
        speaker = ChunkedSpeaker(tts)
        speaker.feed("Ho trovato tre file nella cartella Documenti. Ora ti dico")
        self.assertTrue(tts.started.wait(TIMEOUT))
        self.assertEqual(tts.spoken, ["Ho trovato tre file nella cartella Documenti."])
        speaker.feed(" quali sono, uno per uno.")
        speaker.finish()
        self.assertTrue(speaker.wait(TIMEOUT))
        self.assertEqual(tts.spoken[-1], "Ora ti dico quali sono, uno per uno.")

    def test_the_last_sentence_without_punctuation_is_spoken_on_finish(self):
        tts = FakeTts()
        speaker = ChunkedSpeaker(tts)
        speaker.feed("Fatto, ho aperto la cartella richiesta")
        speaker.finish()
        self.assertTrue(speaker.wait(TIMEOUT))
        self.assertEqual(tts.spoken, ["Fatto, ho aperto la cartella richiesta"])

    def test_a_decimal_split_across_two_deltas_is_not_cut(self):
        tts = FakeTts()
        speaker = ChunkedSpeaker(tts)
        for piece in ("La temperatura e' di 21", ".5 gradi oggi a Milano", ", con cielo sereno."):
            speaker.feed(piece)
        speaker.finish()
        self.assertTrue(speaker.wait(TIMEOUT))
        self.assertEqual(" ".join(tts.spoken), "La temperatura e' di 21.5 gradi oggi a Milano, con cielo sereno.")

    def test_speak_convenience_says_a_complete_text(self):
        tts = FakeTts()
        speaker = ChunkedSpeaker(tts)
        speaker.speak("Prima frase abbastanza lunga qui. Seconda frase altrettanto lunga qui.")
        self.assertTrue(speaker.wait(TIMEOUT))
        self.assertEqual(len(tts.spoken), 2)

    def test_empty_input_finishes_immediately_and_speaks_nothing(self):
        tts = FakeTts()
        speaker = ChunkedSpeaker(tts)
        speaker.finish()
        self.assertTrue(speaker.wait(1.0))
        self.assertEqual(tts.spoken, [])

    def test_first_speech_latency_is_measured_from_the_first_delta(self):
        now = {"t": 100.0}
        tts = FakeTts()
        speaker = ChunkedSpeaker(tts, clock=lambda: now["t"])
        speaker.feed("Prima frase completa e abbastanza lunga.")
        self.assertTrue(tts.started.wait(TIMEOUT))
        speaker.finish()
        speaker.wait(TIMEOUT)
        self.assertEqual(speaker.first_speech_latency_s, 0.0)


class CodeAndMarkdownTests(unittest.TestCase):
    def test_code_arriving_in_pieces_is_never_spoken_and_announced_once(self):
        tts = FakeTts()
        speaker = ChunkedSpeaker(tts)
        for piece in (
            "Ecco lo script che ho scritto per te. ", "```python\nprint('ciao')\n", "x = 1\n```\n",
            "Poi ecco un altro pezzo. ", "```sh\nls -la\n```\n", "Fine del messaggio, grazie.",
        ):
            speaker.feed(piece)
        speaker.finish()
        self.assertTrue(speaker.wait(TIMEOUT))
        spoken = " ".join(tts.spoken)
        self.assertNotIn("print", spoken)
        self.assertNotIn("ls -la", spoken)
        self.assertNotIn("```", spoken)
        self.assertEqual(spoken.count(CODE_OMITTED), 1)
        self.assertIn("Fine del messaggio, grazie.", spoken)

    def test_markdown_markers_are_not_pronounced(self):
        tts = FakeTts()
        speaker = ChunkedSpeaker(tts)
        speaker.speak("Questo e' **molto importante**, leggi [la guida](https://x.io/a) subito.")
        self.assertTrue(speaker.wait(TIMEOUT))
        self.assertEqual(tts.spoken, ["Questo e' molto importante, leggi la guida subito."])


class InterruptionTests(unittest.TestCase):
    def _blocked_speaker(self):
        tts = FakeTts(block_first=True)
        speaker = ChunkedSpeaker(tts)
        speaker.feed("Questa e' la prima frase abbastanza lunga da parlare. Questa e' la seconda frase in coda qui. E questa la terza in coda.")
        self.assertTrue(tts.started.wait(TIMEOUT))
        return tts, speaker

    def test_cancel_stops_the_current_unit_and_drops_every_queued_one(self):
        tts, speaker = self._blocked_speaker()
        dropped = speaker.cancel()
        self.assertEqual(tts.stop_calls, 1)
        self.assertGreaterEqual(dropped, 1)
        self.assertTrue(speaker.wait(TIMEOUT))
        self.assertEqual(len(tts.spoken), 1)  # nessuna unita' accodata e' stata pronunciata dopo il barge-in
        self.assertEqual(speaker.units_spoken, [])  # quella interrotta non conta come "detta"
        self.assertEqual(speaker.units_dropped, dropped)

    def test_cancel_returns_quickly(self):
        _, speaker = self._blocked_speaker()
        started = time.perf_counter()
        speaker.cancel()
        self.assertLess(time.perf_counter() - started, 0.3)  # criterio F2.5: stop entro 300 ms

    def test_text_fed_after_cancel_is_ignored(self):
        tts, speaker = self._blocked_speaker()
        speaker.cancel()
        speaker.wait(TIMEOUT)
        speaker.feed("Altro testo che non deve essere detto mai.")
        speaker.finish()
        time.sleep(0.05)
        self.assertEqual(len(tts.spoken), 1)

    def test_cancel_twice_is_harmless(self):
        _, speaker = self._blocked_speaker()
        speaker.cancel()
        self.assertEqual(speaker.cancel(), 0)

    def test_cancel_before_anything_was_fed(self):
        tts = FakeTts()
        speaker = ChunkedSpeaker(tts)
        self.assertEqual(speaker.cancel(), 0)
        self.assertTrue(speaker.wait(1.0))

    def test_reset_allows_a_new_answer_after_a_cancel(self):
        tts, speaker = self._blocked_speaker()
        speaker.cancel()
        speaker.reset()
        tts2_len = len(tts.spoken)
        speaker.speak("Nuova risposta abbastanza lunga da dire.")
        self.assertTrue(speaker.wait(TIMEOUT))
        self.assertEqual(tts.spoken[tts2_len:], ["Nuova risposta abbastanza lunga da dire."])

    def test_a_failing_provider_does_not_kill_the_remaining_units(self):
        class Flaky(FakeTts):
            def speak(self, text):
                super().speak(text)
                if len(self.spoken) == 1:
                    raise RuntimeError("audio")

        tts = Flaky()
        speaker = ChunkedSpeaker(tts)
        speaker.speak("Prima frase abbastanza lunga qui. Seconda frase altrettanto lunga qui.")
        self.assertTrue(speaker.wait(TIMEOUT))
        self.assertEqual(len(tts.spoken), 2)


class StyleTests(unittest.TestCase):
    TEXT = "Prima frase abbastanza lunga qui. Seconda frase altrettanto lunga qui. Terza frase ancora piu' lunga qui. Quarta frase finale lunga."

    def test_brief_speaks_two_units_then_says_the_rest_is_on_screen(self):
        tts = FakeTts()
        speaker = ChunkedSpeaker(tts, style=STYLES["brief"])
        speaker.speak(self.TEXT)
        self.assertTrue(speaker.wait(TIMEOUT))
        self.assertEqual(len(tts.spoken), 3)
        self.assertEqual(tts.spoken[-1], "Il resto e' a schermo.")

    def test_normal_speaks_everything_without_a_note(self):
        tts = FakeTts()
        speaker = ChunkedSpeaker(tts, style=STYLES["normal"])
        speaker.speak(self.TEXT)
        self.assertTrue(speaker.wait(TIMEOUT))
        self.assertEqual(len(tts.spoken), 4)
        self.assertNotIn("Il resto e' a schermo.", tts.spoken)

    def test_a_short_answer_in_brief_mode_gets_no_note(self):
        tts = FakeTts()
        speaker = ChunkedSpeaker(tts, style=STYLES["brief"])
        speaker.speak("Sono le dieci e mezza.")
        self.assertTrue(speaker.wait(TIMEOUT))
        self.assertEqual(tts.spoken, ["Sono le dieci e mezza."])


class CallbackTests(unittest.TestCase):
    def test_unit_callbacks_bracket_every_unit(self):
        events = []
        tts = FakeTts()
        speaker = ChunkedSpeaker(tts, on_unit_start=lambda u: events.append(("start", u)), on_unit_end=lambda u: events.append(("end", u)))
        speaker.speak("Prima frase abbastanza lunga qui. Seconda frase altrettanto lunga qui.")
        self.assertTrue(speaker.wait(TIMEOUT))
        self.assertEqual([e[0] for e in events], ["start", "end", "start", "end"])

    def test_a_broken_callback_does_not_stop_the_speech(self):
        def boom(_unit):
            raise RuntimeError("hud")

        tts = FakeTts()
        speaker = ChunkedSpeaker(tts, on_unit_end=boom)
        speaker.speak("Prima frase abbastanza lunga qui. Seconda frase altrettanto lunga qui.")
        self.assertTrue(speaker.wait(TIMEOUT))
        self.assertEqual(len(tts.spoken), 2)


if __name__ == "__main__":
    unittest.main()
