"""Test di F2.5.5-F2.5.7 sui provider TTS: parametri di volume/ritmo (set_speech_params), contenuto
identico tra voce online e voce offline di ripiego, e consenso alla clonazione vocale valutato a
ogni frase in CharacterTtsProvider. Nessun audio vero: sounddevice/winsdk/pyttsx3 sono mockati."""
import unittest
from unittest import mock

import numpy as np

from core.voice.character_tts_provider import CharacterTtsProvider
from core.voice.edge_tts_provider import EdgeTtsProvider
from core.voice.onecore_tts_provider import OneCoreTtsProvider
from core.voice.tts_provider import Pyttsx3TtsProvider, TtsProvider, scale_pcm


class ScalePcmTests(unittest.TestCase):
    def test_full_gain_returns_the_same_samples(self):
        samples = np.array([1000, -1000], dtype=np.int16)
        self.assertIs(scale_pcm(samples, 1.0), samples)

    def test_half_gain_halves_and_keeps_the_dtype(self):
        result = scale_pcm(np.array([1000, -1000, 32767], dtype=np.int16), 0.5)
        self.assertEqual(result.dtype, np.int16)
        self.assertEqual(result.tolist(), [500, -500, 16383])

    def test_zero_and_negative_gain_give_silence(self):
        self.assertEqual(scale_pcm(np.array([5, 6], dtype=np.int16), 0.0).tolist(), [0, 0])
        self.assertEqual(scale_pcm(np.array([5, 6], dtype=np.int16), -1.0).tolist(), [0, 0])

    def test_unsigned_samples_do_not_wrap(self):
        result = scale_pcm(np.array([200, 255], dtype=np.uint8), 0.5)
        self.assertEqual(result.tolist(), [100, 127])


class BaseProviderTests(unittest.TestCase):
    def test_a_provider_without_support_says_so(self):
        class Bare(TtsProvider):
            def speak(self, text):
                pass

            def stop(self):
                pass

        self.assertFalse(Bare().set_speech_params(0.5, -5))


class EdgeParamsTests(unittest.TestCase):
    def _provider(self, **kwargs):
        return EdgeTtsProvider(**kwargs)

    def test_rate_is_relative_to_the_configured_base(self):
        provider = self._provider(rate="+8%")
        provider.set_speech_params(1.0, -10)
        self.assertEqual(provider.rate, "-2%")
        provider.set_speech_params(1.0, 0)
        self.assertEqual(provider.rate, "+8%")

    def test_volume_scales_the_pcm_that_reaches_the_speakers(self):
        provider = self._provider()
        provider.set_speech_params(0.5, 0)
        with mock.patch("sounddevice.play") as play, mock.patch("sounddevice.wait"):
            provider._play(np.array([1000, -1000], dtype=np.int16))
        played = play.call_args.args[0]
        self.assertEqual(played.tolist(), [500, -500])

    def test_params_are_forwarded_to_the_offline_fallback(self):
        fallback = mock.MagicMock()
        provider = self._provider(fallback=fallback)
        self.assertTrue(provider.set_speech_params(0.4, -8))
        fallback.set_speech_params.assert_called_once_with(0.4, -8)

    def test_volume_is_clamped(self):
        provider = self._provider()
        provider.set_speech_params(7.0, 0)
        self.assertEqual(provider.volume, 1.0)
        provider.set_speech_params(-1.0, 0)
        self.assertEqual(provider.volume, 0.0)


class OfflineFallbackContentTests(unittest.TestCase):
    """F2.5.5: senza rete cambia la voce, non il contenuto."""

    def test_offline_speaks_exactly_the_same_text_as_a_full_answer(self):
        fallback = mock.MagicMock()
        provider = EdgeTtsProvider(fallback=fallback)
        text = "Prima frase abbastanza lunga qui. Seconda frase altrettanto lunga qui."
        with mock.patch("core.voice.edge_tts_provider.is_online", return_value=False):
            provider.speak(text)
        fallback.speak.assert_called_once_with(text)

    def test_a_mid_answer_network_failure_continues_with_the_remaining_sentences_verbatim(self):
        fallback = mock.MagicMock()
        provider = EdgeTtsProvider(fallback=fallback)
        first = "Questa e' la prima frase, abbastanza lunga da restare sola."
        second = "Questa e' la seconda frase, anche lei sufficientemente lunga."
        third = "Questa e' la terza frase, ugualmente lunga per non fondersi."
        spoken = []
        results = iter([b"ok", None, None])
        with mock.patch("core.voice.edge_tts_provider.is_online", return_value=True), \
                mock.patch.object(provider, "_synthesize", side_effect=lambda t: next(results)), \
                mock.patch.object(provider, "_decode_mp3", side_effect=lambda mp3: mp3), \
                mock.patch.object(provider, "_play", side_effect=spoken.append):
            provider.speak(f"{first} {second} {third}")
        self.assertEqual(len(spoken), 1)  # la prima e' uscita dalla voce online
        fallback.speak.assert_called_once_with(f"{second} {third}")  # il resto, senza perdere nulla


class Pyttsx3ParamsTests(unittest.TestCase):
    def _provider(self):
        fake = mock.MagicMock()
        probe = mock.MagicMock()
        probe.getProperty.return_value = []
        fake.init.return_value = probe
        with mock.patch.dict("sys.modules", {"pyttsx3": fake}):
            provider = Pyttsx3TtsProvider(rate=200)
        return provider, fake

    def test_rate_and_volume_reach_the_engine(self):
        provider, fake = self._provider()
        self.assertTrue(provider.set_speech_params(0.35, -10))
        engine = fake.init.return_value
        provider._build_engine()
        engine.setProperty.assert_any_call("rate", 180)
        engine.setProperty.assert_any_call("volume", 0.35)

    def test_default_volume_is_full(self):
        provider, fake = self._provider()
        provider._build_engine()
        fake.init.return_value.setProperty.assert_any_call("volume", 1.0)

    def test_rate_never_drops_below_a_floor(self):
        provider, _ = self._provider()
        provider.set_speech_params(1.0, -50)
        self.assertGreaterEqual(provider.rate, 60)


class OneCoreParamsTests(unittest.TestCase):
    def test_volume_scales_the_played_samples(self):
        fake_synth = mock.MagicMock()
        fake_synth.all_voices = []
        with mock.patch("winsdk.windows.media.speechsynthesis.SpeechSynthesizer", fake_synth), \
                mock.patch("winsdk.windows.media.speechsynthesis.VoiceGender", mock.MagicMock()):
            provider = OneCoreTtsProvider()
        self.assertTrue(provider.set_speech_params(0.25, 0))
        import io
        import wave

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(np.array([4000, -4000], dtype=np.int16).tobytes())
        with mock.patch("sounddevice.play") as play, mock.patch("sounddevice.wait"):
            provider._play(buffer.getvalue())
        self.assertEqual(play.call_args.args[0].tolist(), [1000, -1000])


class CharacterConsentTests(unittest.TestCase):
    def _provider(self, consent_check):
        base = mock.MagicMock()
        server = mock.MagicMock()
        server.ensure_running.return_value = True
        return CharacterTtsProvider(base, server, consent_check=consent_check), base, server

    def test_without_consent_the_timbre_is_never_converted(self):
        provider, base, server = self._provider(lambda: False)
        provider.speak("ciao")
        base.speak.assert_called_once_with("ciao")
        server.ensure_running.assert_not_called()  # nemmeno avviato il server di conversione
        server.client.convert.assert_not_called()

    def test_with_consent_conversion_proceeds(self):
        provider, base, server = self._provider(lambda: True)
        server.client.convert.return_value = b""
        base.synthesize_to_file.side_effect = lambda text, path: open(path, "wb").close()
        with mock.patch.object(provider, "_play") as play:
            provider.speak("ciao")
        server.client.convert.assert_called_once()
        play.assert_called_once()
        base.speak.assert_not_called()

    def test_revocation_mid_session_applies_to_the_very_next_sentence(self):
        state = {"allowed": True}
        provider, base, server = self._provider(lambda: state["allowed"])
        server.client.convert.return_value = b""
        base.synthesize_to_file.side_effect = lambda text, path: open(path, "wb").close()
        with mock.patch.object(provider, "_play"):
            provider.speak("prima")
            state["allowed"] = False
            provider.speak("dopo")
        self.assertEqual(server.client.convert.call_count, 1)
        base.speak.assert_called_once_with("dopo")

    def test_no_check_configured_keeps_the_legacy_behaviour(self):
        provider, base, server = self._provider(None)
        server.ensure_running.return_value = False
        provider.speak("ciao")
        base.speak.assert_called_once_with("ciao")

    def test_speech_params_reach_the_base_voice(self):
        provider, base, _ = self._provider(lambda: True)
        self.assertTrue(provider.set_speech_params(0.5, -4))
        base.set_speech_params.assert_called_once_with(0.5, -4)


class MainWiringTests(unittest.TestCase):
    def test_main_refuses_to_build_the_character_voice_without_consent(self):
        import tempfile
        from pathlib import Path

        import main
        from core.voice.voice_consent import VoiceConsentRegistry

        base = mock.MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            registry = VoiceConsentRegistry(Path(tmp) / "c.json")
            with mock.patch("core.voice.voice_consent.VoiceConsentRegistry", lambda: registry), \
                    mock.patch("core.voice.rvc_server_manager.RvcServerManager") as manager_class:
                manager_class.return_value.is_installed.return_value = True
                provider, manager = main._build_character_tts_provider(base, "jake_the_dog")
                self.assertIs(provider, base)
                self.assertIsNone(manager)
                registry.grant("jake_the_dog", "personaggio", "fictional-character", "Personaggio di fantasia.")
                provider, manager = main._build_character_tts_provider(base, "jake_the_dog")
                self.assertIsInstance(provider, CharacterTtsProvider)
                self.assertTrue(provider.consent_check())
                registry.revoke("jake_the_dog")
                self.assertFalse(provider.consent_check())


if __name__ == "__main__":
    unittest.main()
