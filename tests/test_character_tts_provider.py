"""Test unitari per core/voice/character_tts_provider.py: nessuna suite esisteva finora, nessun
bug trovato. server_manager/base_tts_provider sono sempre MagicMock; 'sounddevice' e' importato
dentro _play()/stop(): mock.patch.dict su sys.modules. _synthesize_to_bytes usa un file VERO su
disco temporaneo (mai mockato: il contratto e' scrivere/leggere/cancellare un file reale)."""
import io
import unittest
import wave
from unittest import mock

from core.voice.character_tts_provider import CharacterTtsProvider
from core.voice.rvc_client import RvcError


def _wav_bytes(samples: bytes = b"\x00\x01\x02\x03") -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(samples)
    return buffer.getvalue()


def _write_wav(text, path):
    with open(path, "wb") as handle:
        handle.write(_wav_bytes())


def _provider():
    base = mock.MagicMock()
    base.synthesize_to_file.side_effect = _write_wav
    server_manager = mock.MagicMock()
    return CharacterTtsProvider(base, server_manager), base, server_manager


class SpeakTests(unittest.TestCase):
    def test_server_unavailable_falls_back_to_the_base_voice(self):
        provider, base, server_manager = _provider()
        server_manager.ensure_running.return_value = False
        provider.speak("ciao")
        base.speak.assert_called_once_with("ciao")
        server_manager.client.convert.assert_not_called()

    def test_a_conversion_failure_falls_back_to_the_base_voice(self):
        provider, base, server_manager = _provider()
        server_manager.ensure_running.return_value = True
        server_manager.client.convert.side_effect = RvcError("server offline")
        with mock.patch.object(provider, "_play") as play:
            provider.speak("ciao")
        base.speak.assert_called_once_with("ciao")
        play.assert_not_called()

    def test_a_successful_conversion_plays_the_converted_audio(self):
        provider, base, server_manager = _provider()
        server_manager.ensure_running.return_value = True
        server_manager.client.convert.return_value = b"AUDIO CONVERTITO"
        with mock.patch.object(provider, "_play") as play:
            provider.speak("ciao")
        play.assert_called_once_with(b"AUDIO CONVERTITO")
        base.speak.assert_not_called()

    def test_an_interruption_right_after_synthesis_skips_conversion_and_playback(self):
        provider, base, server_manager = _provider()
        server_manager.ensure_running.return_value = True

        original_synthesize = provider._synthesize_to_bytes

        def synthesize_then_interrupt(text):
            provider._interrupted = True
            return original_synthesize(text)

        provider._synthesize_to_bytes = synthesize_then_interrupt
        with mock.patch.object(provider, "_play") as play:
            provider.speak("ciao")
        server_manager.client.convert.assert_not_called()
        play.assert_not_called()
        base.speak.assert_not_called()

    def test_an_interruption_right_after_conversion_skips_playback(self):
        provider, base, server_manager = _provider()
        server_manager.ensure_running.return_value = True

        def convert_then_interrupt(audio_bytes):
            provider._interrupted = True
            return b"AUDIO"

        server_manager.client.convert.side_effect = convert_then_interrupt
        with mock.patch.object(provider, "_play") as play:
            provider.speak("ciao")
        play.assert_not_called()

    def test_a_long_response_is_converted_in_multiple_chunks(self):
        provider, base, server_manager = _provider()
        server_manager.ensure_running.return_value = True
        server_manager.client.convert.return_value = b"AUDIO"

        provider._speech_chunks = mock.Mock(
            return_value=[
                "Questa e' la prima frase.",
                "Questa e' la seconda frase.",
                "Questa e' la terza frase.",
            ]
        )

        with mock.patch.object(provider, "_play") as play:
            provider.speak("Una risposta lunga qualsiasi.")

        self.assertEqual(server_manager.client.convert.call_count, 3)
        self.assertEqual(play.call_count, 3)
        base.speak.assert_not_called()

    def test_prewarm_runs_one_discarded_conversion(self):
        provider, base, server_manager = _provider()
        server_manager.ensure_running.return_value = True
        server_manager.client.convert.return_value = b"AUDIO"

        provider.prewarm()

        provider._prewarm_future.result(timeout=2)

        server_manager.client.convert.assert_called_once()
        base.speak.assert_not_called()

    def test_an_interruption_between_chunks_stops_the_rest(self):
        provider, base, server_manager = _provider()
        server_manager.ensure_running.return_value = True
        server_manager.client.convert.return_value = b"AUDIO"
        provider.MAX_CHUNK_CHARS = 30

        def play_then_stop(audio):
            provider._interrupted = True

        with mock.patch.object(provider, "_play", side_effect=play_then_stop) as play:
            provider.speak(
                "Prima frase da pronunciare. "
                "Seconda frase che non deve essere pronunciata."
            )

        self.assertEqual(play.call_count, 1)

    def test_next_chunk_is_prepared_before_current_playback_finishes(self):
        provider, base, server_manager = _provider()
        server_manager.ensure_running.return_value = True
        server_manager.client.convert.return_value = b"AUDIO"

        provider._speech_chunks = mock.Mock(
            return_value=[
                "Prima frase abbastanza lunga.",
                "Seconda frase abbastanza lunga.",
            ]
        )

        states = []

        original_submit = provider._executor.submit

        def submit(fn, *args, **kwargs):
            states.append(("submit", args[0]))
            return original_submit(fn, *args, **kwargs)

        provider._executor.submit = submit

        with mock.patch.object(provider, "_play") as play:
            provider.speak(
                "Prima frase abbastanza lunga. "
                "Seconda frase abbastanza lunga."
            )

        self.assertEqual(play.call_count, 2)

        # Primo chunk + prefetch del secondo.
        self.assertEqual(
            server_manager.client.convert.call_count,
            2,
        )


class SynthesizeToBytesTests(unittest.TestCase):
    def test_the_temporary_file_is_cleaned_up_afterwards(self):
        provider, base, _ = _provider()
        captured_paths = []
        original = base.synthesize_to_file.side_effect

        def capture_and_write(text, path):
            captured_paths.append(path)
            return original(text, path)

        base.synthesize_to_file.side_effect = capture_and_write
        result = provider._synthesize_to_bytes("ciao")
        self.assertEqual(result, _wav_bytes())
        import os
        self.assertFalse(os.path.exists(captured_paths[0]))

    def test_the_temporary_file_is_cleaned_up_even_if_synthesis_fails(self):
        provider, base, _ = _provider()
        captured_paths = []

        def fail(text, path):
            captured_paths.append(path)
            raise RuntimeError("boom")

        base.synthesize_to_file.side_effect = fail
        with self.assertRaises(RuntimeError):
            provider._synthesize_to_bytes("ciao")
        import os
        self.assertFalse(os.path.exists(captured_paths[0]))


class PlayTests(unittest.TestCase):
    # 'sounddevice' e' un pacchetto vero installato in questo venv (a differenza di 'keyboard'/
    # 'winreg' usati altrove): qui si patchano i suoi attributi direttamente con mock.patch
    # invece di mock.patch.dict su sys.modules. sys.modules va evitato quando il codice sotto
    # test fa anche un import REALE di un'altra libreria nello stesso blocco (qui numpy, dentro
    # _play()): mock.patch.dict ripristina un'istantanea COMPLETA di sys.modules all'uscita,
    # quindi cancella silenziosamente qualunque modulo importato per la prima volta durante il
    # blocco (numpy in questo caso) - alla successiva import fresca, l'estensione C di numpy
    # rifiuta di caricarsi una seconda volta nello stesso processo ("cannot load module more
    # than once per process"). Riprodotto per davvero prima di questa correzione.
    def test_plays_valid_wav_bytes_through_sounddevice(self):
        provider, _, _ = _provider()
        with mock.patch("sounddevice.play") as play, mock.patch("sounddevice.wait") as wait:
            provider._play(_wav_bytes())
        play.assert_called_once()
        wait.assert_called_once()
        self.assertFalse(provider._playing)

    def test_playing_flag_is_cleared_even_if_playback_raises(self):
        provider, _, _ = _provider()
        with mock.patch("sounddevice.play", side_effect=RuntimeError("no audio device")):
            with self.assertRaises(RuntimeError):
                provider._play(_wav_bytes())
        self.assertFalse(provider._playing)


class StopTests(unittest.TestCase):
    def test_stop_marks_interrupted_and_stops_the_base_provider(self):
        provider, base, _ = _provider()
        provider.stop()
        self.assertTrue(provider._interrupted)
        base.stop.assert_called_once()

    def test_stop_while_playing_also_stops_sounddevice(self):
        provider, base, _ = _provider()
        provider._playing = True
        with mock.patch("sounddevice.stop") as stop:
            provider.stop()
        stop.assert_called_once()

    def test_stop_while_not_playing_does_not_touch_sounddevice(self):
        provider, base, _ = _provider()
        with mock.patch("sounddevice.stop") as stop:
            provider.stop()
        stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
