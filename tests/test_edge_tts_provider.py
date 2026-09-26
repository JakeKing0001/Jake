"""Test unitari per core/voice/edge_tts_provider.py: nessuna suite esisteva finora, nessun bug
trovato. 'edge_tts'/'av'/'sounddevice' sono pacchetti VERI installati in questo venv: si
patchano le loro classi/funzioni direttamente con mock.patch (mai mock.patch.dict su
sys.modules - vedi la nota in tests/test_character_tts_provider.py sul perche')."""
import asyncio
import time
import unittest
from unittest import mock

import numpy as np

from core.voice.edge_tts_provider import EdgeTtsProvider, _split_sentences


def _provider(**kwargs):
    return EdgeTtsProvider(**kwargs)


class SplitSentencesTests(unittest.TestCase):
    def test_empty_text_returns_nothing(self):
        self.assertEqual(_split_sentences(""), [])

    def test_a_single_sentence(self):
        self.assertEqual(_split_sentences("Ciao mondo."), ["Ciao mondo."])

    def test_short_fragments_are_merged_with_the_next_sentence(self):
        result = _split_sentences("Ok. Ho capito perfettamente cosa intendi dire.")
        self.assertEqual(result, ["Ok. Ho capito perfettamente cosa intendi dire."])

    def test_long_sentences_stay_separate(self):
        long_one = "Questa e' una frase abbastanza lunga da superare venticinque caratteri."
        long_two = "Anche questa seconda frase e' sufficientemente lunga da restare separata."
        result = _split_sentences(f"{long_one} {long_two}")
        self.assertEqual(result, [long_one, long_two])

    def test_newlines_also_split_sentences(self):
        result = _split_sentences("Prima riga abbastanza lunga da restare sola.\nSeconda riga altrettanto lunga qui.")
        self.assertEqual(len(result), 2)


class SynthesizeTests(unittest.TestCase):
    def test_a_successful_synthesis_concatenates_audio_chunks(self):
        class FakeCommunicate:
            def __init__(self, text, voice, rate=None, pitch=None):
                pass

            async def stream(self):
                yield {"type": "audio", "data": b"PARTE1"}
                yield {"type": "other", "data": b"ignorato"}
                yield {"type": "audio", "data": b"PARTE2"}

        provider = _provider()
        with mock.patch("edge_tts.Communicate", FakeCommunicate):
            result = provider._synthesize("ciao")
        self.assertEqual(result, b"PARTE1PARTE2")

    def test_a_timeout_returns_none_instead_of_raising(self):
        class SlowCommunicate:
            def __init__(self, text, voice, rate=None, pitch=None):
                pass

            async def stream(self):
                await asyncio.sleep(1)
                yield {"type": "audio", "data": b"troppo tardi"}

        provider = _provider(timeout=0.01)
        with mock.patch("edge_tts.Communicate", SlowCommunicate):
            result = provider._synthesize("ciao")
        self.assertIsNone(result)

    def test_a_network_error_returns_none_instead_of_raising(self):
        provider = _provider()
        with mock.patch("edge_tts.Communicate", side_effect=RuntimeError("rete assente")):
            result = provider._synthesize("ciao")
        self.assertIsNone(result)


class DecodeMp3Tests(unittest.TestCase):
    def test_concatenates_resampled_frames_into_one_array(self):
        resampled_a = mock.MagicMock()
        resampled_a.to_ndarray.return_value = np.array([[1, 2]], dtype=np.int16)
        resampled_b = mock.MagicMock()
        resampled_b.to_ndarray.return_value = np.array([[3, 4]], dtype=np.int16)

        fake_resampler = mock.MagicMock()
        fake_resampler.resample.side_effect = [[resampled_a], [resampled_b], []]

        fake_container = mock.MagicMock()
        fake_container.streams.audio = [mock.MagicMock()]
        fake_container.decode.return_value = [mock.MagicMock(), mock.MagicMock()]

        with mock.patch("av.open", return_value=fake_container), mock.patch("av.AudioResampler", return_value=fake_resampler):
            result = EdgeTtsProvider._decode_mp3(b"finto mp3")

        np.testing.assert_array_equal(result, [1, 2, 3, 4])
        self.assertEqual(result.dtype, np.int16)
        fake_container.close.assert_called_once()

    def test_no_frames_decoded_returns_an_empty_array(self):
        fake_resampler = mock.MagicMock()
        fake_resampler.resample.return_value = []
        fake_container = mock.MagicMock()
        fake_container.streams.audio = [mock.MagicMock()]
        fake_container.decode.return_value = []

        with mock.patch("av.open", return_value=fake_container), mock.patch("av.AudioResampler", return_value=fake_resampler):
            result = EdgeTtsProvider._decode_mp3(b"finto mp3")

        self.assertEqual(len(result), 0)
        self.assertEqual(result.dtype, np.int16)


class PlayTests(unittest.TestCase):
    def test_an_empty_pcm_array_is_not_played(self):
        provider = _provider()
        with mock.patch("sounddevice.play") as play:
            provider._play(np.zeros((0,), dtype=np.int16))
        play.assert_not_called()

    def test_an_interrupted_provider_does_not_play(self):
        provider = _provider()
        provider._interrupted = True
        with mock.patch("sounddevice.play") as play:
            provider._play(np.array([1, 2, 3], dtype=np.int16))
        play.assert_not_called()

    def test_plays_and_clears_the_playing_flag_afterwards(self):
        provider = _provider()
        with mock.patch("sounddevice.play") as play, mock.patch("sounddevice.wait"):
            provider._play(np.array([1, 2, 3], dtype=np.int16))
        play.assert_called_once()
        self.assertFalse(provider._playing)


class SpeakTests(unittest.TestCase):
    def test_empty_text_does_nothing(self):
        provider = _provider()
        with mock.patch.object(provider, "_executor") as executor:
            provider.speak("")
        executor.submit.assert_not_called()

    def test_offline_goes_straight_to_the_fallback(self):
        provider = _provider(fallback=mock.MagicMock())
        with mock.patch("core.voice.edge_tts_provider.is_online", return_value=False):
            with mock.patch.object(provider, "_executor") as executor:
                provider.speak("ciao")
        executor.submit.assert_not_called()
        provider.fallback.speak.assert_called_once_with("ciao")

    def test_each_sentence_is_synthesized_decoded_and_played_in_order(self):
        provider = _provider()
        played = []
        with mock.patch("core.voice.edge_tts_provider.is_online", return_value=True):
            with mock.patch.object(provider, "_synthesize", side_effect=lambda text: text.encode()):
                with mock.patch.object(provider, "_decode_mp3", side_effect=lambda mp3: mp3):
                    with mock.patch.object(provider, "_play", side_effect=played.append):
                        long_a = "Questa e' la prima frase, abbastanza lunga da restare sola."
                        long_b = "Questa e' la seconda frase, anche lei sufficientemente lunga."
                        provider.speak(f"{long_a} {long_b}")
        self.assertEqual(played, [long_a.encode(), long_b.encode()])

    def test_a_synthesis_failure_falls_back_with_the_remaining_text(self):
        provider = _provider(fallback=mock.MagicMock())
        long_a = "Questa e' la prima frase, abbastanza lunga da restare sola."
        long_b = "Questa e' la seconda frase, anche lei sufficientemente lunga."
        with mock.patch("core.voice.edge_tts_provider.is_online", return_value=True):
            with mock.patch.object(provider, "_synthesize", return_value=None):
                with mock.patch.object(provider, "_play") as play:
                    provider.speak(f"{long_a} {long_b}")
        play.assert_not_called()
        provider.fallback.speak.assert_called_once_with(f"{long_a} {long_b}")
        self.assertEqual(provider._consecutive_failures, 1)

    def test_a_decode_failure_falls_back_with_the_remaining_text(self):
        provider = _provider(fallback=mock.MagicMock())
        long_a = "Questa e' la prima frase, abbastanza lunga da restare sola."
        long_b = "Questa e' la seconda frase, anche lei sufficientemente lunga."
        with mock.patch("core.voice.edge_tts_provider.is_online", return_value=True):
            with mock.patch.object(provider, "_synthesize", side_effect=lambda text: text.encode()):
                with mock.patch.object(provider, "_decode_mp3", side_effect=RuntimeError("boom")):
                    with mock.patch.object(provider, "_play") as play:
                        provider.speak(f"{long_a} {long_b}")
        play.assert_not_called()
        provider.fallback.speak.assert_called_once_with(f"{long_a} {long_b}")

    def test_interruption_stops_processing_further_sentences(self):
        """La frase successiva viene pre-sintetizzata MENTRE suona la corrente (per design): dopo
        l'interruzione non deve essere suonata, e il prefetch non va oltre di lei. (La versione
        precedente asseriva che la seconda non fosse neanche sintetizzata: passava solo se il worker
        perdeva la corsa con il thread del test.)"""
        provider = _provider()
        long_a = "Questa e' la prima frase, abbastanza lunga da restare sola."
        long_b = "Questa e' la seconda frase, anche lei sufficientemente lunga."
        long_c = "Questa e' la terza frase, che non deve mai essere preparata."
        played = []

        def interrupt_after_playing(pcm):
            played.append(pcm)
            provider._interrupted = True

        with mock.patch("core.voice.edge_tts_provider.is_online", return_value=True):
            with mock.patch.object(provider, "_synthesize", side_effect=lambda text: text.encode()) as synth:
                with mock.patch.object(provider, "_decode_mp3", side_effect=lambda mp3: mp3):
                    with mock.patch.object(provider, "_play", side_effect=interrupt_after_playing):
                        provider.speak(f"{long_a} {long_b} {long_c}")
                        time.sleep(0.1)  # un eventuale prefetch in volo ha il tempo di arrivare
        self.assertEqual(played, [long_a.encode()])
        self.assertNotIn(mock.call(long_c), synth.call_args_list)


class StopTests(unittest.TestCase):
    def test_stop_marks_interrupted_and_stops_the_fallback(self):
        provider = _provider(fallback=mock.MagicMock())
        provider.stop()
        self.assertTrue(provider._interrupted)
        provider.fallback.stop.assert_called_once()

    def test_stop_while_playing_also_stops_sounddevice(self):
        provider = _provider()
        provider._playing = True
        with mock.patch("sounddevice.stop") as stop:
            provider.stop()
        stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
