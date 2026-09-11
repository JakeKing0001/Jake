"""Test unitari per core/voice/vad_listener.py: nessuna suite esisteva finora, nessun bug
trovato. 'webrtcvad' e 'sounddevice' sono pacchetti VERI installati in questo venv: si patchano i
loro attributi direttamente con mock.patch (mai mock.patch.dict su sys.modules, che ripristina
un'istantanea completa di sys.modules all'uscita e cancellerebbe silenziosamente 'numpy'
importato a livello di modulo qui - vedi la nota in tests/test_character_tts_provider.py per il
dettaglio di questo problema, riprodotto per davvero in quel file)."""
import unittest
from unittest import mock

import numpy as np

from core.voice.vad_listener import VadListener


def _continue_n_times(n: int):
    state = {"count": 0}

    def _continue() -> bool:
        state["count"] += 1
        return state["count"] <= n

    return _continue


def _fake_input_stream_factory(frames):
    def _fake_input_stream(**kwargs):
        callback = kwargs["callback"]
        for frame in frames:
            callback(frame, frame.shape[0], None, None)
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.__exit__.return_value = False
        return stream

    return _fake_input_stream


def _make_listener(is_speech_sequence, silence_ms=30):
    fake_vad_instance = mock.MagicMock()
    fake_vad_instance.is_speech.side_effect = is_speech_sequence
    with mock.patch("webrtcvad.Vad", return_value=fake_vad_instance):
        return VadListener(silence_ms=silence_ms)


class IsAvailableTests(unittest.TestCase):
    def test_a_device_with_input_channels_is_available(self):
        listener = _make_listener([])
        with mock.patch("sounddevice.query_devices", return_value=[{"max_input_channels": 2}]):
            self.assertTrue(listener.is_available())

    def test_no_input_devices_is_unavailable(self):
        listener = _make_listener([])
        with mock.patch("sounddevice.query_devices", return_value=[{"max_input_channels": 0}]):
            self.assertFalse(listener.is_available())

    def test_a_query_failure_is_unavailable(self):
        listener = _make_listener([])
        with mock.patch("sounddevice.query_devices", side_effect=RuntimeError("no driver")):
            self.assertFalse(listener.is_available())


class ListenForUtterancesTests(unittest.TestCase):
    def test_speech_followed_by_silence_yields_one_utterance(self):
        frame_samples = VadListener.FRAME_SAMPLES
        speech_frame = np.full((frame_samples, 1), 1000, dtype=np.int16)
        silence_frame = np.zeros((frame_samples, 1), dtype=np.int16)
        listener = _make_listener([True, False])  # una parlata, poi silenzio

        with mock.patch("sounddevice.InputStream", side_effect=_fake_input_stream_factory([speech_frame, silence_frame])):
            gen = listener.listen_for_utterances(_continue_n_times(2))
            utterance = next(gen)
            gen.close()

        self.assertEqual(utterance.dtype, np.float32)
        self.assertEqual(len(utterance), frame_samples * 2)
        self.assertTrue(np.all(np.abs(utterance) <= 1.0))

    def test_muted_frames_never_produce_an_utterance(self):
        frame_samples = VadListener.FRAME_SAMPLES
        speech_frame = np.full((frame_samples, 1), 1000, dtype=np.int16)
        silence_frame = np.zeros((frame_samples, 1), dtype=np.int16)
        listener = _make_listener([True, False])
        listener.muted = True

        with mock.patch("sounddevice.InputStream", side_effect=_fake_input_stream_factory([speech_frame, silence_frame])):
            gen = listener.listen_for_utterances(_continue_n_times(2))
            with self.assertRaises(StopIteration):
                next(gen)

    def test_incomplete_frames_are_skipped(self):
        frame_samples = VadListener.FRAME_SAMPLES
        incomplete_frame = np.zeros((frame_samples // 2, 1), dtype=np.int16)
        listener = _make_listener([])

        with mock.patch("sounddevice.InputStream", side_effect=_fake_input_stream_factory([incomplete_frame])):
            gen = listener.listen_for_utterances(_continue_n_times(1))
            with self.assertRaises(StopIteration):
                next(gen)
        listener.vad.is_speech.assert_not_called()

    def test_on_level_callback_receives_a_normalized_level_and_speech_flag(self):
        frame_samples = VadListener.FRAME_SAMPLES
        speech_frame = np.full((frame_samples, 1), 32767, dtype=np.int16)
        on_level_calls = []
        listener = _make_listener([True])
        listener.on_level = lambda level, is_speech: on_level_calls.append((level, is_speech))

        with mock.patch("sounddevice.InputStream", side_effect=_fake_input_stream_factory([speech_frame])):
            gen = listener.listen_for_utterances(_continue_n_times(1))
            with self.assertRaises(StopIteration):
                next(gen)

        self.assertEqual(len(on_level_calls), 1)
        level, is_speech = on_level_calls[0]
        self.assertTrue(is_speech)
        self.assertLessEqual(level, 1.0)

    def test_a_broken_on_level_callback_does_not_interrupt_listening(self):
        frame_samples = VadListener.FRAME_SAMPLES
        speech_frame = np.full((frame_samples, 1), 1000, dtype=np.int16)
        silence_frame = np.zeros((frame_samples, 1), dtype=np.int16)
        listener = _make_listener([True, False])
        listener.on_level = mock.MagicMock(side_effect=RuntimeError("boom"))

        with mock.patch("sounddevice.InputStream", side_effect=_fake_input_stream_factory([speech_frame, silence_frame])):
            gen = listener.listen_for_utterances(_continue_n_times(2))
            utterance = next(gen)
            gen.close()
        self.assertEqual(len(utterance), frame_samples * 2)


class ToFloat32Tests(unittest.TestCase):
    def test_converts_and_normalizes_pcm16_frames(self):
        frames = [np.full((4, 1), 16384, dtype=np.int16)]
        result = VadListener._to_float32(frames)
        self.assertEqual(result.dtype, np.float32)
        np.testing.assert_allclose(result, [0.5, 0.5, 0.5, 0.5], atol=1e-4)


if __name__ == "__main__":
    unittest.main()
