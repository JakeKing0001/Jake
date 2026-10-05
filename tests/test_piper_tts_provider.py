"""Voce Piper del personaggio (core/voice/piper_tts_provider.py) con un motore finto: niente modello ne' audio."""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np


class _Chunk:
    def __init__(self, n):
        self.audio_int16_array = np.ones(n, dtype=np.int16)


class _Voice:
    config = types.SimpleNamespace(sample_rate=22050)
    calls = []

    @classmethod
    def load(cls, path):
        return cls()

    def synthesize(self, text, syn_config=None):
        _Voice.calls.append((text, syn_config.length_scale))
        yield _Chunk(10)
        yield _Chunk(20)


_fake_piper = types.SimpleNamespace(PiperVoice=_Voice,
                                    SynthesisConfig=lambda length_scale: types.SimpleNamespace(length_scale=length_scale))


@mock.patch.dict(sys.modules, {"piper": _fake_piper})
class PiperTtsProviderTest(unittest.TestCase):
    def _provider(self, **kwargs):
        from core.voice.piper_tts_provider import PiperTtsProvider

        provider = PiperTtsProvider("x.onnx", **kwargs)
        provider.played = []
        provider._play = lambda pcm: provider.played.append(len(pcm))
        return provider

    def test_speaks_every_sentence_chunk_in_order(self):
        provider = self._provider(rate_percent=8)
        provider.speak("Ciao. Come stai?")
        self.assertEqual(provider.played, [10, 20])
        self.assertAlmostEqual(_Voice.calls[-1][1], 1 / 1.08)

    def test_revoked_consent_uses_base_voice(self):
        base = mock.Mock()
        provider = self._provider(base_tts_provider=base, consent_check=lambda: False)
        provider.speak("Ciao.")
        base.speak.assert_called_once_with("Ciao.")
        self.assertEqual(provider.played, [])

    def test_stop_interrupts_remaining_chunks(self):
        provider = self._provider()
        provider._play = lambda pcm: (provider.played.append(len(pcm)), provider.stop())
        provider.speak("Uno. Due.")
        self.assertEqual(provider.played, [10])

    def test_model_path_needs_onnx_and_config(self):
        from core.voice.piper_tts_provider import piper_model_path

        folder = Path(tempfile.mkdtemp())
        (folder / "jake.onnx").write_bytes(b"")
        self.assertIsNone(piper_model_path("jake", folder))
        (folder / "jake.onnx.json").write_text("{}")
        self.assertEqual(piper_model_path("jake", folder), folder / "jake.onnx")


if __name__ == "__main__":
    unittest.main()
