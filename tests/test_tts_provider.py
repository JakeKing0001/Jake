"""Test unitari per core/voice/tts_provider.py::Pyttsx3TtsProvider: nessuna suite esisteva
finora, nessun bug trovato. 'pyttsx3' e' importato dentro __init__/i metodi: mock.patch.dict su
sys.modules basta perche' l'import avviene a ogni chiamata. Mai un vero motore SAPI5."""
import unittest
from unittest import mock

from core.voice.tts_provider import Pyttsx3TtsProvider


def _fake_voice(id_, name, gender=None, languages=None):
    voice = mock.MagicMock()
    voice.id = id_
    voice.name = name
    voice.gender = gender
    voice.languages = languages or []
    return voice


def _make_provider(voices, preferred_gender="male", voice_id=None):
    fake_pyttsx3 = mock.MagicMock()
    probe = mock.MagicMock()
    probe.getProperty.return_value = voices
    fake_pyttsx3.init.return_value = probe
    with mock.patch.dict("sys.modules", {"pyttsx3": fake_pyttsx3}):
        provider = Pyttsx3TtsProvider(preferred_gender=preferred_gender, voice_id=voice_id)
    return provider, fake_pyttsx3, probe


class SelectVoiceIdTests(unittest.TestCase):
    def test_prefers_an_italian_voice_matching_the_requested_gender(self):
        voices = [
            _fake_voice("v1", "David", gender="male", languages=["en-US"]),
            _fake_voice("v2", "Cosimo", gender="male", languages=["it-IT"]),
            _fake_voice("v3", "Elsa", gender="female", languages=["it-IT"]),
        ]
        provider, _, probe = _make_provider(voices, preferred_gender="male")
        self.assertEqual(provider.voice_id, "v2")
        self.assertTrue(provider.matched_preferred_gender)
        probe.stop.assert_called_once()

    def test_falls_back_to_the_first_italian_voice_when_gender_does_not_match(self):
        voices = [_fake_voice("v1", "Elsa", gender="female", languages=["it-IT"])]
        provider, _, _ = _make_provider(voices, preferred_gender="male")
        self.assertEqual(provider.voice_id, "v1")
        self.assertFalse(provider.matched_preferred_gender)

    def test_no_italian_voice_and_no_gender_match_returns_none(self):
        voices = [_fake_voice("v1", "Bob", gender="male", languages=["en-US"])]
        provider, _, _ = _make_provider(voices, preferred_gender="female")
        self.assertIsNone(provider.voice_id)

    def test_no_voices_at_all_returns_none(self):
        provider, _, _ = _make_provider([])
        self.assertIsNone(provider.voice_id)

    def test_gender_is_matched_as_a_whole_word_not_a_substring(self):
        # "male" e' una sottostringa di "female": un confronto ingenuo classificherebbe
        # erroneamente una voce femminile come maschile.
        voices = [_fake_voice("v1", "Female Voice IT-IT", gender=None, languages=["it-IT"])]
        provider, _, _ = _make_provider(voices, preferred_gender="male")
        self.assertFalse(provider.matched_preferred_gender)

    def test_an_explicit_voice_id_skips_selection_entirely(self):
        fake_pyttsx3 = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"pyttsx3": fake_pyttsx3}):
            provider = Pyttsx3TtsProvider(voice_id="v-esplicita")
        self.assertEqual(provider.voice_id, "v-esplicita")
        fake_pyttsx3.init.assert_not_called()


class SpeakAndStopTests(unittest.TestCase):
    def test_speak_builds_an_engine_and_says_the_text(self):
        provider, fake_pyttsx3, _ = _make_provider([])
        engine = mock.MagicMock()
        fake_pyttsx3.init.return_value = engine
        with mock.patch.dict("sys.modules", {"pyttsx3": fake_pyttsx3}):
            provider.speak("ciao")
        engine.say.assert_called_once_with("ciao")
        engine.runAndWait.assert_called_once()
        self.assertIsNone(provider._engine)

    def test_stop_with_no_active_engine_does_not_raise(self):
        provider, _, _ = _make_provider([])
        provider.stop()

    def test_stop_stops_the_currently_speaking_engine(self):
        provider, fake_pyttsx3, _ = _make_provider([])
        engine = mock.MagicMock()

        def blocking_run_and_wait():
            provider.stop()

        engine.runAndWait.side_effect = blocking_run_and_wait
        fake_pyttsx3.init.return_value = engine
        with mock.patch.dict("sys.modules", {"pyttsx3": fake_pyttsx3}):
            provider.speak("ciao")
        engine.stop.assert_called_once()


class SynthesizeToFileTests(unittest.TestCase):
    def test_saves_to_file_and_always_stops_the_engine(self):
        provider, fake_pyttsx3, _ = _make_provider([])
        engine = mock.MagicMock()
        fake_pyttsx3.init.return_value = engine
        with mock.patch.dict("sys.modules", {"pyttsx3": fake_pyttsx3}):
            provider.synthesize_to_file("ciao", "C:\\tmp\\out.wav")
        engine.save_to_file.assert_called_once_with("ciao", "C:\\tmp\\out.wav")
        engine.stop.assert_called_once()

    def test_stops_the_engine_even_if_saving_fails(self):
        provider, fake_pyttsx3, _ = _make_provider([])
        engine = mock.MagicMock()
        engine.save_to_file.side_effect = RuntimeError("boom")
        fake_pyttsx3.init.return_value = engine
        with mock.patch.dict("sys.modules", {"pyttsx3": fake_pyttsx3}):
            with self.assertRaises(RuntimeError):
                provider.synthesize_to_file("ciao", "C:\\tmp\\out.wav")
        engine.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
