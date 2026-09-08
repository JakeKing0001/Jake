"""Test unitari per il riconoscimento della wake word (core/voice/wake_word_session.py), in
particolare il risveglio dalla pausa in posizione naturale (v4.1, Voice Natural 2.0). Nessun
vero microfono/audio: VAD, STT e TTS sono tutti finti, e non si aspetta mai un vero thread di
sintesi vocale (usa un TTS finto che ritorna all'istante)."""
import time
import unittest
from types import SimpleNamespace

from core.voice.wake_word_session import WakeWordSession


class FakeSttProvider:
    def __init__(self, text: str):
        self.text = text

    def transcribe(self, utterance, sample_rate):
        return self.text


class FakeTtsProvider:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)

    def stop(self):
        pass


class FakeJakeCore:
    EXIT_SENTINEL = "__exit__"

    def __init__(self):
        self.conversation_state = SimpleNamespace(has_pending_action=lambda: False)

    def answer(self, text):
        return "risposta"


def _session(text: str) -> WakeWordSession:
    session = WakeWordSession(
        FakeJakeCore(), FakeSttProvider(text), FakeTtsProvider(),
        vad_listener=SimpleNamespace(on_level=None, muted=False, SAMPLE_RATE=16000),
    )
    return session


class MatchWakeWordTests(unittest.TestCase):
    def test_wake_word_at_start_returns_remainder(self):
        session = _session("")
        self.assertEqual(session._match_wake_word("Jake apri spotify"), "apri spotify")

    def test_greeting_prefix_is_skipped(self):
        session = _session("")
        self.assertEqual(session._match_wake_word("ehi Jake che ore sono"), "che ore sono")

    def test_no_wake_word_returns_none(self):
        session = _session("")
        self.assertIsNone(session._match_wake_word("apri spotify"))

    def test_fuzzy_transcription_variant_is_accepted(self):
        session = _session("")
        self.assertEqual(session._match_wake_word("geek che tempo fa"), "che tempo fa")


class ContainsWakeWordAnywhereTests(unittest.TestCase):
    def test_true_when_wake_word_is_in_the_middle(self):
        session = _session("")
        self.assertTrue(session._contains_wake_word_anywhere("scusa jake torna operativo", session._is_close_to_wake_word))

    def test_true_when_wake_word_is_at_the_end(self):
        session = _session("")
        self.assertTrue(session._contains_wake_word_anywhere("riprendi pure jake", session._is_close_to_wake_word))

    def test_false_without_wake_word(self):
        session = _session("")
        self.assertFalse(session._contains_wake_word_anywhere("continua pure", session._is_close_to_wake_word))


class PauseWakeUpTests(unittest.TestCase):
    def test_wakes_up_when_wake_word_is_not_the_first_word(self):
        """Prima del v4.1 il risveglio richiedeva 'Jake' come prima parola: 'scusa se ti
        disturbo, Jake, svegliati' non funzionava."""
        session = _session("scusa se ti disturbo jake svegliati")
        session.paused_until = time.time() + 60

        session._handle_utterance(object())

        self.assertEqual(session.paused_until, 0.0, "resume_listening() doveva azzerare la pausa")

    def test_stays_paused_without_a_wake_up_phrase(self):
        session = _session("jake continua a dormire per favore")  # nome presente, ma nessuna frase di risveglio
        session.paused_until = time.time() + 60
        original_pause = session.paused_until

        session._handle_utterance(object())

        self.assertEqual(session.paused_until, original_pause)

    def test_stays_paused_without_the_wake_word_at_all(self):
        session = _session("svegliati per favore")  # frase di risveglio, ma senza il nome
        session.paused_until = time.time() + 60
        original_pause = session.paused_until

        session._handle_utterance(object())

        self.assertEqual(session.paused_until, original_pause)


if __name__ == "__main__":
    unittest.main()
