"""Test unitari per core/voice/onecore_tts_provider.py: nessuna suite esisteva finora, nessun
bug trovato. 'winsdk' e' un pacchetto VERO installato in questo venv (binding WinRT): si
patchano le sue classi direttamente con mock.patch/AsyncMock, mai un vero motore vocale
OneCore. 'sounddevice' e' patchato allo stesso modo (vedi la nota in
tests/test_character_tts_provider.py sul perche' mai mock.patch.dict su sys.modules qui)."""
import io
import unittest
import wave
from unittest import mock

from core.voice.onecore_tts_provider import OneCoreTtsProvider


def _fake_voice(display_name, language, gender=None):
    voice = mock.MagicMock()
    voice.display_name = display_name
    voice.language = language
    voice.gender = gender
    return voice


def _make_provider(voices, voice_name_contains=None, preferred_gender="male"):
    fake_gender = mock.MagicMock(MALE="MALE", FEMALE="FEMALE")
    fake_synth_class = mock.MagicMock()
    fake_synth_class.all_voices = voices
    with mock.patch("winsdk.windows.media.speechsynthesis.SpeechSynthesizer", fake_synth_class):
        with mock.patch("winsdk.windows.media.speechsynthesis.VoiceGender", fake_gender):
            provider = OneCoreTtsProvider(voice_name_contains=voice_name_contains, preferred_gender=preferred_gender)
    return provider, fake_synth_class


class SelectVoiceTests(unittest.TestCase):
    def test_no_voices_available_returns_none(self):
        provider, _ = _make_provider([])
        self.assertIsNone(provider.voice)

    def test_an_explicit_name_match_wins(self):
        target = _fake_voice("Microsoft Cosimo", "it-IT", gender="FEMALE")
        other = _fake_voice("Microsoft David", "en-US", gender="MALE")
        provider, _ = _make_provider([other, target], voice_name_contains="cosimo")
        self.assertIs(provider.voice, target)
        self.assertTrue(provider.matched_preferred_gender)

    def test_prefers_an_italian_voice_matching_the_gender(self):
        male_it = _fake_voice("Voce IT Maschile", "it-IT", gender="MALE")
        female_it = _fake_voice("Voce IT Femminile", "it-IT", gender="FEMALE")
        provider, _ = _make_provider([female_it, male_it], preferred_gender="male")
        self.assertIs(provider.voice, male_it)
        self.assertTrue(provider.matched_preferred_gender)

    def test_falls_back_to_the_first_italian_voice_without_a_gender_match(self):
        female_it = _fake_voice("Voce IT Femminile", "it-IT", gender="FEMALE")
        provider, _ = _make_provider([female_it], preferred_gender="male")
        self.assertIs(provider.voice, female_it)
        self.assertFalse(provider.matched_preferred_gender)

    def test_falls_back_to_the_first_voice_when_no_italian_voice_exists(self):
        english = _fake_voice("English Voice", "en-US", gender="FEMALE")
        provider, _ = _make_provider([english], preferred_gender="male")
        self.assertIs(provider.voice, english)


class SpeakTests(unittest.TestCase):
    def test_no_voice_selected_does_nothing(self):
        provider, _ = _make_provider([])
        with mock.patch.object(provider, "_synthesize") as synthesize:
            provider.speak("ciao")
        synthesize.assert_not_called()

    def test_a_selected_voice_synthesizes_and_plays(self):
        # mock.patch.object rileva che _synthesize e' una async def e crea automaticamente un
        # AsyncMock: basta un return_value semplice, l'attesa (await) e' gia' gestita da
        # AsyncMock stesso - avvolgerlo a mano in un'altra coroutine (tentativo iniziale) fa si'
        # che asyncio.run restituisca la coroutine interna mai attesa, non i byte veri.
        voice = _fake_voice("Voce", "it-IT", gender="MALE")
        provider, _ = _make_provider([voice])
        with mock.patch.object(provider, "_synthesize", return_value=b"AUDIO"):
            with mock.patch.object(provider, "_play") as play:
                provider.speak("ciao")
        play.assert_called_once_with(b"AUDIO")


class SynthesizeTests(unittest.TestCase):
    def test_reads_the_synthesized_stream_into_bytes(self):
        voice = _fake_voice("Voce", "it-IT", gender="MALE")
        provider, fake_synth_class = _make_provider([voice])
        payload = b"BYTE SINTETIZZATI"

        fake_stream = mock.MagicMock(size=len(payload))
        synth_instance = mock.MagicMock()
        synth_instance.synthesize_text_to_stream_async = mock.AsyncMock(return_value=fake_stream)
        fake_synth_class.return_value = synth_instance

        fake_reader_instance = mock.MagicMock()
        fake_reader_instance.load_async = mock.AsyncMock(return_value=None)
        fake_reader_instance.read_bytes.side_effect = lambda buf: buf.__setitem__(slice(None), payload)
        fake_reader_class = mock.MagicMock(return_value=fake_reader_instance)

        with mock.patch("winsdk.windows.storage.streams.DataReader", fake_reader_class):
            import asyncio
            result = asyncio.run(provider._synthesize("ciao"))

        self.assertEqual(result, payload)
        self.assertIs(synth_instance.voice, provider.voice)


class PlayTests(unittest.TestCase):
    def _wav_bytes(self) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(b"\x00\x01\x02\x03")
        return buffer.getvalue()

    def test_plays_valid_wav_bytes(self):
        provider, _ = _make_provider([])
        with mock.patch("sounddevice.play") as play, mock.patch("sounddevice.wait"):
            provider._play(self._wav_bytes())
        play.assert_called_once()
        self.assertFalse(provider._playing)

    def test_playing_flag_is_cleared_even_on_failure(self):
        provider, _ = _make_provider([])
        with mock.patch("sounddevice.play", side_effect=RuntimeError("no device")):
            with self.assertRaises(RuntimeError):
                provider._play(self._wav_bytes())
        self.assertFalse(provider._playing)


class StopTests(unittest.TestCase):
    def test_stop_while_playing_stops_sounddevice(self):
        provider, _ = _make_provider([])
        provider._playing = True
        with mock.patch("sounddevice.stop") as stop:
            provider.stop()
        stop.assert_called_once()

    def test_stop_while_not_playing_does_nothing(self):
        provider, _ = _make_provider([])
        with mock.patch("sounddevice.stop") as stop:
            provider.stop()
        stop.assert_not_called()


class SynthesizeToFileTests(unittest.TestCase):
    def test_writes_the_synthesized_bytes_to_disk(self):
        import tempfile
        from pathlib import Path

        provider, _ = _make_provider([])
        with mock.patch.object(provider, "_synthesize", return_value=b"CONTENUTO AUDIO"):
            with tempfile.TemporaryDirectory() as tmp:
                output_path = str(Path(tmp) / "out.bin")
                provider.synthesize_to_file("ciao", output_path)
                self.assertEqual(Path(output_path).read_bytes(), b"CONTENUTO AUDIO")


if __name__ == "__main__":
    unittest.main()
