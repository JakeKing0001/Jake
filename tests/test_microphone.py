"""Test unitari per core/voice/microphone.py: nessuna suite esisteva finora, nessun bug trovato.
'sounddevice' e' importato dentro le funzioni: mock.patch.dict su sys.modules basta perche'
l'import avviene a ogni chiamata. Mai un vero dispositivo audio."""
import unittest
from unittest import mock

import numpy as np

from core.voice.microphone import Microphone, MicrophoneError


class IsAvailableTests(unittest.TestCase):
    def test_a_device_with_input_channels_is_available(self):
        fake_sd = mock.MagicMock()
        fake_sd.query_devices.return_value = [{"max_input_channels": 0}, {"max_input_channels": 2}]
        with mock.patch.dict("sys.modules", {"sounddevice": fake_sd}):
            self.assertTrue(Microphone().is_available())

    def test_no_input_devices_is_unavailable(self):
        fake_sd = mock.MagicMock()
        fake_sd.query_devices.return_value = [{"max_input_channels": 0}]
        with mock.patch.dict("sys.modules", {"sounddevice": fake_sd}):
            self.assertFalse(Microphone().is_available())

    def test_a_query_failure_is_unavailable(self):
        fake_sd = mock.MagicMock()
        fake_sd.query_devices.side_effect = RuntimeError("no driver")
        with mock.patch.dict("sys.modules", {"sounddevice": fake_sd}):
            self.assertFalse(Microphone().is_available())


class RecordWhileTests(unittest.TestCase):
    def test_captured_frames_are_concatenated(self):
        fake_sd = mock.MagicMock()
        should_continue_calls = iter([True, False])

        def fake_input_stream(**kwargs):
            callback = kwargs["callback"]
            callback(np.array([[0.1], [0.2]], dtype="float32"), 2, None, None)
            stream = mock.MagicMock()
            stream.__enter__.return_value = stream
            stream.__exit__.return_value = False
            return stream

        fake_sd.InputStream.side_effect = fake_input_stream
        fake_sd.sleep = mock.MagicMock()

        with mock.patch.dict("sys.modules", {"sounddevice": fake_sd}):
            result = Microphone().record_while(lambda: next(should_continue_calls))

        self.assertEqual(result.shape, (2,))
        np.testing.assert_allclose(result, [0.1, 0.2])

    def test_no_frames_captured_returns_an_empty_array(self):
        fake_sd = mock.MagicMock()
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.__exit__.return_value = False
        fake_sd.InputStream.return_value = stream
        fake_sd.sleep = mock.MagicMock()

        with mock.patch.dict("sys.modules", {"sounddevice": fake_sd}):
            result = Microphone().record_while(lambda: False)

        self.assertEqual(result.shape, (0,))

    def test_a_stream_failure_raises_microphone_error(self):
        fake_sd = mock.MagicMock()
        fake_sd.InputStream.side_effect = RuntimeError("nessun dispositivo")

        with mock.patch.dict("sys.modules", {"sounddevice": fake_sd}):
            with self.assertRaises(MicrophoneError):
                Microphone().record_while(lambda: True)


if __name__ == "__main__":
    unittest.main()
