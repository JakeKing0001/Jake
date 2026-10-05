"""GPU ceduta a giochi/app a schermo intero (core/gpu_yield.py) e ripresa con isteresi."""
import tempfile
import unittest
from pathlib import Path

from core.gpu_yield import QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, GpuDemand, GpuYieldMonitor
from core.ollama_gpu_budget import GpuBudget, GpuLayerPolicy


def _demand(state=5, exe=None, processes=(), apps=(), gaming=False):
    return GpuDemand(yield_apps=apps, gaming_mode=lambda: gaming, notification_state=lambda: state,
                     foreground_exe=lambda: exe, processes=lambda: set(processes))


class _Flip:
    def __init__(self):
        self.value = None

    def reason(self):
        return self.value


class GpuYieldTest(unittest.TestCase):
    def test_demand_signals(self):
        self.assertIsNone(_demand().reason())
        self.assertEqual(_demand(state=QUNS_BUSY, exe="eldenring.exe").reason(), "fullscreen:eldenring.exe")
        # video a schermo intero o Jake stesso non giustificano scaricare il modello
        self.assertIsNone(_demand(state=QUNS_BUSY, exe="chrome.exe").reason())
        self.assertIsNone(_demand(state=QUNS_BUSY, exe="jakehud.exe").reason())
        self.assertEqual(_demand(state=QUNS_RUNNING_D3D_FULL_SCREEN, exe="chrome.exe").reason(), "fullscreen:chrome.exe")
        self.assertEqual(_demand(processes={"blender.exe"}, apps=["Blender.exe"]).reason(), "app:blender.exe")
        self.assertEqual(_demand(gaming=True).reason(), "gaming_mode")

    def test_vram_pressure_counts_the_model_own_memory(self):
        from core.gpu_yield import VramPressure

        mb = 2**20
        tags = lambda: [{"name": "m:1", "size": 3000 * mb}]
        # non caricato, 200 MB liberi (un training ha preso la GPU): non ci sta
        self.assertTrue(VramPressure("m:1", lambda: [], tags, free_vram=lambda: 200)().startswith("vram:"))
        # caricato tutto sulla GPU: la sua memoria conta come disponibile
        loaded = lambda: [{"name": "m:1", "size": 3500 * mb, "size_vram": 3500 * mb}]
        self.assertIsNone(VramPressure("m:1", loaded, tags, free_vram=lambda: 400)())
        # senza GPU NVIDIA nessun segnale
        self.assertIsNone(VramPressure("m:1", lambda: [], tags, free_vram=lambda: None)())

    def test_monitor_hysteresis_and_listeners(self):
        now = [0.0]
        demand = _Flip()
        monitor = GpuYieldMonitor(demand, enter_s=4, exit_s=20, clock=lambda: now[0])
        events = []
        monitor.add_listener(lambda yielding, reason: events.append((yielding, reason)))

        demand.value = "fullscreen:game.exe"
        self.assertFalse(monitor.evaluate())
        now[0] = 3
        self.assertFalse(monitor.evaluate())
        now[0] = 4
        self.assertTrue(monitor.evaluate())
        demand.value = None           # alt-tab breve: niente ricarica
        now[0] = 10
        monitor.evaluate()
        demand.value = "fullscreen:game.exe"
        now[0] = 12
        monitor.evaluate()
        demand.value = None
        now[0] = 13
        monitor.evaluate()
        now[0] = 32
        self.assertFalse(monitor.evaluate())
        now[0] = 33
        self.assertTrue(monitor.evaluate())
        self.assertEqual(events, [(True, "fullscreen:game.exe"), (False, None)])

    def test_policy_yield_overrides_budget_and_full_gpu(self):
        policy = GpuLayerPolicy(GpuBudget(), "qwen2.5:7b", 8192, "http://127.0.0.1:1", gpu=lambda: "none")
        self.assertIsNone(policy.layers())
        policy.set_yield(0)
        self.assertEqual(policy.layers(), 0)
        policy.set_yield(None)
        self.assertIsNone(policy.layers())

    def test_rvc_hold_keeps_server_off_until_release(self):
        from core.voice.rvc_server_manager import RvcServerManager

        manager = RvcServerManager("jake", project_root=Path(tempfile.mkdtemp()))
        started = []
        manager.ensure_running = lambda timeout=90: started.append(True) or True
        manager.hold()
        manager.prewarm()
        self.assertTrue(manager.held)
        self.assertEqual(started, [])
        manager.release()
        manager.prewarm()
        manager._prewarm_thread.join(2)
        self.assertEqual(started, [True])


if __name__ == "__main__":
    unittest.main()
