"""Budget di VRAM del modello Ollama principale (core/ollama_gpu_budget.py, runtime_options in core/ollama_client.py).
Ollama finto: le "misure" sono quelle che restituisce il finto /api/ps, mai una regola layer -> MB data per buona."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import ollama_client
from core.ollama_client import OllamaClient, runtime_options
from core.ollama_gpu_budget import (
    Calibrator,
    GpuBudget,
    GpuLayerPolicy,
    budget_from_settings,
    model_vram_mb,
)
from tools import preflight

MODEL = "qwen2.5:7b"


class FakeOllama:
    """VRAM per numero di layer come un runtime vero: una base appena un layer va sulla GPU, poi per layer."""

    def __init__(self, base=520, per_layer=157, digest="sha-1", layers=28):
        self.base, self.per_layer, self.digest, self.layers = base, per_layer, digest, layers
        self.loaded = None
        self.loads = []

    def vram(self, n):
        return 0 if n == 0 else self.base + n * self.per_layer

    def ps(self):
        if self.loaded is None:
            return []
        return [{"name": MODEL, "size": 5_400_000_000, "size_vram": self.vram(self.loaded) * 1_000_000}]

    def tags(self):
        return [{"name": MODEL, "digest": self.digest}]

    def show(self, model):
        return {"model_info": {"qwen2.block_count": self.layers}}

    def load(self, model, options):
        self.loads.append(options)
        self.loaded = options["num_gpu"]

    def unload(self, model):
        self.loaded = None


class _TmpCache(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name) / "calibration.json"

    def calibrator(self, api, budget_mb=1024, mode="hard", ctx=8192, gpu="RTX 4060"):
        return Calibrator(api, MODEL, GpuBudget(budget_mb=budget_mb, mode=mode), ctx, gpu, cache_path=self.cache)


class CalibrationTests(_TmpCache):
    def test_picks_the_most_layers_whose_measured_vram_fits_the_hard_budget(self):
        api = FakeOllama()
        result = self.calibrator(api).calibrate()
        self.assertEqual(result["num_gpu"], 3)                 # 991 MB; 4 layer = 1148 MB, oltre 1024
        self.assertLessEqual(result["vram_mb"], 1024)
        self.assertLessEqual(len(api.loads), 5)                # poche cariche, non decine
        self.assertIsNone(api.loaded)                          # scaricato: la prossima richiesta usa il valore scelto
        self.assertTrue(all(load["num_ctx"] == 8192 for load in api.loads))

    def test_soft_budget_tolerates_a_little_more_for_speed(self):
        self.assertEqual(self.calibrator(FakeOllama(), mode="soft").calibrate()["num_gpu"], 4)   # 1148 <= 1228

    def test_falls_back_to_cpu_only_when_even_one_layer_breaks_the_hard_budget(self):
        result = self.calibrator(FakeOllama(base=1100)).calibrate()
        self.assertEqual((result["num_gpu"], result["vram_mb"]), (0, 0))

    def test_cache_is_reused_and_invalidated_by_digest_gpu_budget_or_context(self):
        api = FakeOllama()
        self.calibrator(api).calibrate()
        self.assertEqual(self.calibrator(api).cached("sha-1")["num_gpu"], 3)
        self.assertIsNone(self.calibrator(api).cached("sha-2"), "modello aggiornato")
        self.assertIsNone(self.calibrator(api, gpu="RTX 4090").cached("sha-1"), "GPU diversa")
        self.assertIsNone(self.calibrator(api, budget_mb=2048).cached("sha-1"), "budget diverso")
        self.assertIsNone(self.calibrator(api, ctx=4096).cached("sha-1"), "context diverso")

    def test_policy_uses_the_cache_without_reloading_the_model(self):
        api = FakeOllama()
        self.calibrator(api).calibrate()
        api.loads.clear()
        policy = GpuLayerPolicy(GpuBudget(budget_mb=1024), MODEL, 8192, "http://x", api=api,
                                gpu=lambda: "RTX 4060", cache_path=self.cache)
        self.assertEqual(policy.layers(), 3)
        self.assertEqual(api.loads, [])


class ParsingAndSettingsTests(unittest.TestCase):
    def test_vram_comes_from_the_model_entry_of_api_ps_only(self):
        models = [{"name": "nomic-embed-text:latest", "size_vram": 300_000_000},
                  {"name": MODEL, "size": 5_400_000_000, "size_vram": 987_000_000}]
        self.assertEqual(model_vram_mb(models, MODEL), 987)
        self.assertIsNone(model_vram_mb(models, "qwen2.5vl:7b"))

    def test_budget_settings_and_low_memory(self):
        self.assertFalse(budget_from_settings({}).enabled, "nessun cambio per chi non configura nulla")
        self.assertEqual(budget_from_settings({"low_memory": True}).budget_mb, 1024)
        self.assertEqual(budget_from_settings({"low_memory": True, "ollama_gpu_budget_mb": 2048}).budget_mb, 2048)
        budget = budget_from_settings({"ollama_gpu_budget_mb": "abc", "ollama_num_gpu_layers": "6",
                                       "ollama_context": 4096, "ollama_gpu_budget_mode": "SOFT"})
        self.assertEqual((budget.budget_mb, budget.fixed_layers, budget.context, budget.mode), (None, 6, 4096, "soft"))


class RuntimeOptionsTests(unittest.TestCase):
    def _settings(self, policy):
        return (mock.patch.object(ollama_client, "_settings_cache", {"primary": MODEL, "low_memory": False}),
                mock.patch.object(ollama_client, "_gpu_policy", policy))

    def test_primary_gets_num_gpu_and_caller_options_survive(self):
        policy = GpuLayerPolicy(GpuBudget(budget_mb=1024, fixed_layers=3), MODEL, 8192, "http://x", api=FakeOllama())
        settings, gpu = self._settings(policy)
        with settings, gpu:
            merged = runtime_options(MODEL, {"temperature": 0, "num_predict": 40, "num_ctx": 8192, "num_gpu": 99})
            self.assertEqual(merged, {"temperature": 0, "num_predict": 40, "num_ctx": 8192, "num_gpu": 3})
            self.assertEqual(runtime_options("nomic-embed-text", {"num_ctx": 2048}), {"num_ctx": 2048})

    def test_configured_context_applies_to_every_primary_call(self):
        policy = GpuLayerPolicy(GpuBudget(budget_mb=1024, fixed_layers=3, context=4096), MODEL, 4096, "http://x")
        settings, gpu = self._settings(policy)
        with settings, gpu:
            self.assertEqual(runtime_options(MODEL, {"num_ctx": 8192})["num_ctx"], 4096)

    def test_without_a_budget_nothing_changes(self):
        settings, gpu = self._settings(GpuLayerPolicy(GpuBudget(), MODEL, 8192, "http://x"))
        with settings, gpu:
            self.assertEqual(runtime_options(MODEL, {"num_ctx": 8192}), {"num_ctx": 8192})

    def test_failed_calibration_stays_on_cpu(self):
        api = mock.MagicMock()
        api.tags.side_effect = OSError("ollama spento")
        policy = GpuLayerPolicy(GpuBudget(budget_mb=1024), MODEL, 8192, "http://x", api=api, gpu=lambda: "RTX 4060")
        self.assertEqual(policy.layers(), 0)

    def test_the_shared_client_sends_the_policy_to_ollama(self):
        policy = GpuLayerPolicy(GpuBudget(budget_mb=1024, fixed_layers=3), MODEL, 8192, "http://x")
        settings, gpu = self._settings(policy)
        with settings, gpu:
            client = OllamaClient()
            with mock.patch.object(client, "_post", return_value={}) as post:
                client.chat(MODEL, [], options={"temperature": 0})
        self.assertEqual(post.call_args[0][1]["options"], {"num_ctx": 8192, "temperature": 0, "num_gpu": 3})


class PreflightOffloadTests(unittest.TestCase):
    def test_reports_model_size_and_gpu_resident_part_separately(self):
        calibration = {"num_gpu": 3, "layers_total": 29, "vram_mb": 987, "size_mb": 5379}
        model, budget = preflight.check_ollama_offload(MODEL, GpuBudget(budget_mb=1024), calibration)
        self.assertIn("~5.4 GB modello", model.detail)
        self.assertIn("~0.99 GB VRAM", model.detail)
        self.assertIn("offload parziale 3/29 layer", model.detail)
        self.assertEqual(budget.status, preflight.OK)
        self.assertEqual(preflight.check_ollama_offload(MODEL, GpuBudget(budget_mb=1024), None)[0].status, preflight.WARN)
        self.assertEqual(preflight.check_ollama_offload(MODEL, GpuBudget(), calibration), [])

    def test_gpu_memory_estimate_uses_the_resident_part(self):
        tags = {"models": [{"name": MODEL, "size": 4_683_087_332}]}
        check = preflight.check_gpu_budget(MODEL, tags, 8188, 1118, resident_model_mb=987)
        self.assertIn("~987 MB", check.detail)
        self.assertEqual(check.status, preflight.OK)


if __name__ == "__main__":
    unittest.main()
