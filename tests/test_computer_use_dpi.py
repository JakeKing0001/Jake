"""F3.1.5 — Computer Use con DPI diversi, contro la fixture Qt vera.

La fixture viene lanciata con `QT_SCALE_FACTOR` 1.0 / 1.25 / 1.5 / 2.0 (sopra la scala di sistema di
questa macchina, 125%): Qt ridisegna davvero piu' grande (verificato dai bounds UIA). A ogni scala il
bottone "Aggiungi" viene premuto con un click PIXEL calcolato dal centro dei bounds UI Automation -
il percorso che si rompe se coordinate UIA e coordinate del mouse non sono nello stesso spazio (processo
non per-monitor DPI aware, vedi core/win_dpi.py) - e l'effetto e' verificato in modo indipendente (la
voce compare davvero nella lista). Multi-monitor: verificabile solo con piu' di un monitor collegato;
altrimenti il test e' saltato con il motivo, mai dichiarato verde."""
import ctypes
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from core.computer_agent import ComputerAgent
from core.computer_use.selector import ElementSelector, SelectorEngine
from core.computer_use.ui_automation_adapter import UIAutomationAdapter
from core.win_dpi import PER_MONITOR_AWARE, current_dpi_awareness

_ROOT = Path(__file__).resolve().parent.parent
_TITLE = "Jake Computer Use Fixture"
_INPUT_ID = "QApplication.jake_fixture_window.fixture_input"


class FixtureAtScale:
    def __init__(self, scale: float):
        self.scale = scale

    def __enter__(self):
        env = dict(os.environ, QT_SCALE_FACTOR=str(self.scale))
        self.process = subprocess.Popen(
            [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "60"], cwd=str(_ROOT), env=env,
        )
        self.adapter = UIAutomationAdapter()
        try:
            self.window = self.adapter.find_window_by_title(_TITLE, timeout_seconds=15.0)
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        return False


class DpiScalingTests(unittest.TestCase):
    SCALES = (1.0, 1.25, 1.5, 2.0)

    def test_jake_is_per_monitor_dpi_aware_before_acting(self):
        ComputerAgent()
        self.assertEqual(current_dpi_awareness(), PER_MONITOR_AWARE)

    def test_a_pixel_click_from_uia_bounds_hits_the_button_at_every_scale(self):
        agent = ComputerAgent()
        widths = {}
        for scale in self.SCALES:
            with self.subTest(scale=scale), FixtureAtScale(scale) as fixture:
                engine = SelectorEngine(fixture.adapter)
                button = engine.wait_for_unique_element(
                    fixture.window, ElementSelector(name="Aggiungi", control_type="Button"), timeout_seconds=5.0,
                )
                left, top, width, height = fixture.adapter.describe_element(button).bounds
                widths[scale] = width
                label = f"dpi {scale}"
                typed = agent.type_into_element(label, window_title=_TITLE, automation_id=_INPUT_ID)
                self.assertTrue(typed.success)

                clicked = agent.click_point(left + width // 2, top + height // 2)
                self.assertTrue(clicked.success)

                item = engine.wait_for_unique_element(
                    fixture.window, ElementSelector(name=label, control_type="ListItem"), timeout_seconds=3.0,
                )
                self.assertEqual(item.CurrentName, label)
        # Prova che la scala e' stata davvero applicata (non 4 volte la stessa finestra).
        self.assertGreater(widths[2.0], widths[1.0] * 1.8)
        self.assertGreater(widths[1.5], widths[1.0] * 1.3)

    def test_multi_monitor(self):
        monitors = ctypes.windll.user32.GetSystemMetrics(80)  # SM_CMONITORS
        if monitors < 2:
            self.skipTest(f"un solo monitor collegato ({monitors}): prova multi-monitor da ripetere con due schermi")
        agent = ComputerAgent()
        with FixtureAtScale(1.0) as fixture:
            # Porta la finestra sul secondo monitor (a destra del primario) e ripete il click pixel.
            width = ctypes.windll.user32.GetSystemMetrics(0)
            from core.computer_use.executor import ActionExecutor

            ActionExecutor().move_window(fixture.window, width + 50, 50)
            time.sleep(0.5)
            engine = SelectorEngine(fixture.adapter)
            button = engine.wait_for_unique_element(
                fixture.window, ElementSelector(name="Aggiungi", control_type="Button"), timeout_seconds=5.0,
            )
            left, top, w, h = fixture.adapter.describe_element(button).bounds
            self.assertGreaterEqual(left, width, "la finestra deve essere davvero sul secondo monitor")
            self.assertTrue(agent.type_into_element("secondo monitor", window_title=_TITLE, automation_id=_INPUT_ID).success)
            self.assertTrue(agent.click_point(left + w // 2, top + h // 2).success)
            engine.wait_for_unique_element(
                fixture.window, ElementSelector(name="secondo monitor", control_type="ListItem"), timeout_seconds=3.0,
            )


if __name__ == "__main__":
    unittest.main()
