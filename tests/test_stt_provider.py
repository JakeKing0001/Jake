"""Test unitari per core/voice/stt_provider.py: nessuna suite esisteva finora, nessun bug
trovato. 'faster_whisper.WhisperModel' e' sempre mockato (mai un vero modello Whisper caricato/
scaricato); 'ctranslate2' e os.add_dll_directory sono mockati per cuda_available()."""
import unittest
from unittest import mock

from core.voice.stt_provider import WhisperSttProvider, cuda_available


def _fake_segment(text):
    segment = mock.MagicMock()
    segment.text = text
    return segment


def _provider(device=None, model_raises_on_cuda=False):
    fake_model_class = mock.MagicMock()
    if model_raises_on_cuda:
        fake_model_class.side_effect = [RuntimeError("no cuda"), mock.MagicMock()]
    fake_module = mock.MagicMock(WhisperModel=fake_model_class)
    with mock.patch.dict("sys.modules", {"faster_whisper": fake_module}):
        with mock.patch("core.voice.stt_provider.cuda_available", return_value=False):
            provider = WhisperSttProvider(device=device)
    return provider, fake_model_class


class InitTests(unittest.TestCase):
    def test_defaults_to_cpu_without_cuda(self):
        provider, _ = _provider()
        self.assertEqual(provider.device, "cpu")
        self.assertEqual(provider.model_size, WhisperSttProvider.CPU_MODEL)
        self.assertEqual(provider.compute_type, "int8")

    def test_explicit_cuda_device_uses_the_gpu_model(self):
        fake_model_class = mock.MagicMock()
        fake_module = mock.MagicMock(WhisperModel=fake_model_class)
        with mock.patch.dict("sys.modules", {"faster_whisper": fake_module}):
            provider = WhisperSttProvider(device="cuda")
        self.assertEqual(provider.device, "cuda")
        self.assertEqual(provider.model_size, WhisperSttProvider.GPU_MODEL)
        self.assertEqual(provider.compute_type, "float16")

    def test_a_gpu_load_failure_falls_back_to_cpu(self):
        fake_model_class = mock.MagicMock(side_effect=[RuntimeError("no vram"), mock.MagicMock()])
        fake_module = mock.MagicMock(WhisperModel=fake_model_class)
        with mock.patch.dict("sys.modules", {"faster_whisper": fake_module}):
            provider = WhisperSttProvider(device="cuda")
        self.assertEqual(provider.device, "cpu")
        self.assertEqual(provider.model_size, WhisperSttProvider.CPU_MODEL)
        self.assertEqual(fake_model_class.call_count, 2)

    def test_a_cpu_load_failure_is_not_swallowed(self):
        fake_model_class = mock.MagicMock(side_effect=RuntimeError("modello mancante"))
        fake_module = mock.MagicMock(WhisperModel=fake_model_class)
        with mock.patch.dict("sys.modules", {"faster_whisper": fake_module}):
            with mock.patch("core.voice.stt_provider.cuda_available", return_value=False):
                with self.assertRaises(RuntimeError):
                    WhisperSttProvider(device=None)


class BuildPromptTests(unittest.TestCase):
    def test_deduplicates_base_vocabulary_and_hotwords(self):
        provider, _ = _provider()
        provider.set_hotwords(["jake", "Blender"])  # "jake" e "Blender" gia' nel vocabolario base
        occurrences = provider.initial_prompt.lower().count("blender")
        self.assertEqual(occurrences, 1)

    def test_ends_with_a_period(self):
        provider, _ = _provider()
        self.assertTrue(provider.initial_prompt.endswith("."))

    def test_long_prompts_are_truncated_at_a_comma_boundary(self):
        provider, _ = _provider()
        provider.set_hotwords([f"parola numero {i}" for i in range(200)])
        self.assertLessEqual(len(provider.initial_prompt), WhisperSttProvider.MAX_PROMPT_CHARS + 1)
        self.assertTrue(provider.initial_prompt.endswith("."))
        self.assertNotIn(",.", provider.initial_prompt)

    def test_overly_long_single_words_are_dropped(self):
        provider, _ = _provider()
        provider.set_hotwords(["x" * 41])
        self.assertNotIn("x" * 41, provider.initial_prompt)


class TranscribeTests(unittest.TestCase):
    def test_joins_segment_texts(self):
        provider, _ = _provider()
        provider._model.transcribe.return_value = ([_fake_segment(" ciao "), _fake_segment("mondo ")], None)
        result = provider.transcribe([0.0, 0.1], sample_rate=16000)
        self.assertEqual(result, "ciao mondo")

    def test_passes_the_initial_prompt_and_language(self):
        provider, _ = _provider()
        provider._model.transcribe.return_value = ([], None)
        provider.transcribe([0.0])
        _, kwargs = provider._model.transcribe.call_args
        self.assertEqual(kwargs["initial_prompt"], provider.initial_prompt)
        self.assertEqual(kwargs["language"], "it")
        self.assertFalse(kwargs["condition_on_previous_text"])

    def test_known_hallucinations_are_dropped(self):
        provider, _ = _provider()
        provider._model.transcribe.return_value = (
            [_fake_segment("Sottotitoli e revisione a cura di QTSS")], None,
        )
        result = provider.transcribe([0.0])
        self.assertEqual(result, "")

    def test_genuine_text_is_not_treated_as_a_hallucination(self):
        provider, _ = _provider()
        provider._model.transcribe.return_value = ([_fake_segment("che tempo fa oggi")], None)
        result = provider.transcribe([0.0])
        self.assertEqual(result, "che tempo fa oggi")


class CudaAvailableTests(unittest.TestCase):
    def test_a_working_cuda_device_is_detected(self):
        fake_ctranslate2 = mock.MagicMock()
        fake_ctranslate2.get_cuda_device_count.return_value = 1
        with mock.patch("core.voice.stt_provider._add_cuda_dll_dirs"):
            with mock.patch.dict("sys.modules", {"ctranslate2": fake_ctranslate2}):
                self.assertTrue(cuda_available())

    def test_no_cuda_devices_is_false(self):
        fake_ctranslate2 = mock.MagicMock()
        fake_ctranslate2.get_cuda_device_count.return_value = 0
        with mock.patch("core.voice.stt_provider._add_cuda_dll_dirs"):
            with mock.patch.dict("sys.modules", {"ctranslate2": fake_ctranslate2}):
                self.assertFalse(cuda_available())

    def test_an_import_failure_is_false_not_a_crash(self):
        with mock.patch("core.voice.stt_provider._add_cuda_dll_dirs"):
            with mock.patch.dict("sys.modules", {"ctranslate2": None}):
                self.assertFalse(cuda_available())


class TranscribeDetailedTests(unittest.TestCase):
    """F2.2.2: confidenza per frase = exp(media pesata sulla durata di avg_logprob)."""

    @staticmethod
    def _segment(text, avg_logprob, start=0.0, end=1.0):
        segment = _fake_segment(text)
        segment.avg_logprob, segment.start, segment.end = avg_logprob, start, end
        return segment

    def test_confidence_is_exp_of_the_mean_logprob(self):
        import math

        provider, _ = _provider()
        provider._model.transcribe.return_value = ([self._segment("ciao", -0.5)], None)
        text, confidence = provider.transcribe_detailed([0.0])
        self.assertEqual(text, "ciao")
        self.assertAlmostEqual(confidence, math.exp(-0.5), places=3)

    def test_longer_segments_weigh_more(self):
        import math

        provider, _ = _provider()
        provider._model.transcribe.return_value = (
            [self._segment("a", -1.0, 0.0, 3.0), self._segment("b", -0.1, 3.0, 4.0)], None,
        )
        _, confidence = provider.transcribe_detailed([0.0])
        self.assertAlmostEqual(confidence, math.exp((-1.0 * 3 + -0.1 * 1) / 4), places=3)

    def test_confidence_is_none_when_the_model_reports_nothing(self):
        provider, _ = _provider()
        provider._model.transcribe.return_value = ([_fake_segment("ciao")], None)  # MagicMock: niente logprob numerico
        self.assertEqual(provider.transcribe_detailed([0.0]), ("ciao", None))

    def test_confidence_is_none_when_a_hallucination_wiped_the_text(self):
        provider, _ = _provider()
        provider._model.transcribe.return_value = ([self._segment("Sottotitoli e revisione a cura di QTSS", -0.2)], None)
        self.assertEqual(provider.transcribe_detailed([0.0]), ("", None))

    def test_transcribe_is_the_text_of_transcribe_detailed(self):
        provider, _ = _provider()
        provider._model.transcribe.return_value = ([self._segment("ciao", -0.3)], None)
        self.assertEqual(provider.transcribe([0.0]), "ciao")


if __name__ == "__main__":
    unittest.main()
