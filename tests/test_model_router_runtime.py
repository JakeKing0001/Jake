"""F8.4 nel runtime: JakeCore sceglie il modello con il ModelRouter (catalogo da config, modelli installati,
batteria/VRAM reali) invece di un nome scritto a mano. Nessun Ollama reale: sorgenti iniettate."""
import unittest
from types import SimpleNamespace

from core.model_router import (Capability, HardwareInfo, build_local_router, choose_model, detect_local_hardware)

CONFIG = {"ollama_model": "grande:7b", "ollama_light_model": "piccolo:1b"}


def _hardware(on_battery, percent):
    info = HardwareInfo(on_battery=on_battery, battery_percent=percent)
    return lambda: info


class ChooseModelTests(unittest.TestCase):
    def test_plugged_in_uses_the_best_installed_model(self):
        router = build_local_router(CONFIG, lambda: ["grande:7b", "piccolo:1b"], hardware=_hardware(False, 100))
        self.assertEqual(choose_model(router, Capability.REASON, "grande:7b"), "grande:7b")

    def test_low_battery_prefers_the_light_model_if_declared_and_installed(self):
        router = build_local_router(CONFIG, lambda: ["grande:7b", "piccolo:1b"], hardware=_hardware(True, 18))
        self.assertEqual(choose_model(router, Capability.REASON, "grande:7b"), "piccolo:1b")
        router = build_local_router(CONFIG, lambda: ["grande:7b"], hardware=_hardware(True, 18))
        self.assertEqual(choose_model(router, Capability.REASON, "grande:7b"), "grande:7b", "leggero non installato")

    def test_ollama_unreachable_falls_back_to_the_configured_model(self):
        router = build_local_router(CONFIG, lambda: None, hardware=_hardware(False, 100))
        self.assertEqual(choose_model(router, Capability.REASON, "grande:7b"), "grande:7b")


class HardwareTelemetryTests(unittest.TestCase):
    def test_reads_free_vram_and_battery_and_leaves_unknowns_unknown(self):
        ok = lambda *a, **k: SimpleNamespace(returncode=0, stdout="6983\n")  # noqa: E731
        info = detect_local_hardware(run=ok, battery=SimpleNamespace(power_plugged=False, percent=42))
        self.assertEqual((info.available_vram_mb, info.on_battery, info.battery_percent), (6983, True, 42.0))

        def missing(*a, **k):
            raise OSError("nvidia-smi assente")

        info = detect_local_hardware(run=missing, battery=None)
        self.assertIsNone(info.available_vram_mb)


class JakeCoreModelTests(unittest.TestCase):
    def test_set_model_now_also_changes_the_model_used_by_agents(self):
        """Bug reale: SET_MODEL non aggiornava JakeCore.model, quindi agenti e ricevute restavano sul vecchio."""
        from core.jake_core import JakeCore
        from skills.model_control import SetModelSkill

        core = JakeCore.__new__(JakeCore)
        core.ollama = SimpleNamespace(list_models=lambda: ["vecchio:7b", "nuovo:8b"])
        core.config = SimpleNamespace(get=lambda key, default=None: None)
        core.model = "vecchio:7b"
        self.assertEqual(core.model, "vecchio:7b")
        SetModelSkill([core], SimpleNamespace(set=lambda *a: None)).execute({"model": "nuovo:8b"})
        self.assertEqual(core.model, "nuovo:8b")


class ModelUnloadTests(unittest.TestCase):
    def test_when_the_router_switches_model_the_previous_one_is_unloaded_once(self):
        """F8.4.4: a batteria bassa il router passa al modello leggero; quello grande non resta in VRAM per ore."""
        import threading

        from core.jake_core import JakeCore

        hardware = {"info": HardwareInfo(on_battery=False, battery_percent=100)}
        unloaded, done = [], threading.Event()
        core = JakeCore.__new__(JakeCore)
        core.ollama = SimpleNamespace(list_models=lambda: ["grande:7b", "piccolo:1b"],
                                      unload=lambda model: (unloaded.append(model), done.set()))
        core._configured_model = "grande:7b"
        core._model_router = build_local_router(CONFIG, core.ollama.list_models, hardware=lambda: hardware["info"])
        self.assertEqual(core.model, "grande:7b")
        self.assertEqual(core.model, "grande:7b")
        hardware["info"] = HardwareInfo(on_battery=True, battery_percent=15)
        core._model_router = build_local_router(CONFIG, core.ollama.list_models, hardware=lambda: hardware["info"])
        self.assertEqual(core.model, "piccolo:1b")
        self.assertTrue(done.wait(2))
        self.assertEqual(core.model, "piccolo:1b")
        self.assertEqual(unloaded, ["grande:7b"], "scaricato una volta, solo al cambio")


class RealObservationsTests(unittest.TestCase):
    def test_every_real_model_call_feeds_the_router_statistics(self):
        from unittest import mock

        from core.jake_core import JakeCore
        from core.ollama_client import OllamaClient, OllamaError

        client = OllamaClient(base_url="http://127.0.0.1:9")
        core = JakeCore.__new__(JakeCore)
        core.ollama = client
        core.config = SimpleNamespace(get=lambda key, default=None: None)
        core.model = "grande:7b"
        client.on_chat = core._record_model_call
        with mock.patch.object(client, "_post", return_value={"message": {"content": "ok"}}):
            client.chat("grande:7b", [])
        with mock.patch.object(client, "_post", side_effect=OllamaError("giu'")), self.assertRaises(OllamaError):
            client.chat("grande:7b", [])
        stats = core.model_router.evals.stats(Capability.REASON, "grande:7b")
        self.assertEqual(stats.samples, 2)
        self.assertTrue(0 < stats.success_rate < 0.5, "l'ultima osservazione (fallita) pesa di piu'")


if __name__ == "__main__":
    unittest.main()
