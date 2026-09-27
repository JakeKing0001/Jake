"""F0.6.2: il preflight dice cosa manca su questa macchina prima di avviare Jake. Le singole verifiche con fonti
finte per i casi che qui non si possono provocare; un giro reale su questa macchina nel test finale."""
import importlib.metadata
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib import error

from tools import preflight
from tools.preflight import FAIL, OK, WARN


class PreflightTests(unittest.TestCase):
    def test_missing_base_dependencies_block_the_start_and_say_how_to_fix(self):
        def version_of(name):
            if name == "cryptography":
                raise importlib.metadata.PackageNotFoundError(name)
            return "1.0"

        check = preflight.check_requirements("base", preflight.ROOT / "requirements" / "base.txt", FAIL, version_of)
        self.assertEqual(check.status, FAIL)
        self.assertIn("mancano cryptography: installa con pip install -r requirements/base.txt", check.detail)

    def test_ollama_down_or_model_missing_is_a_limit_not_a_blocker(self):
        def down(url):
            raise error.URLError("connection refused")

        self.assertEqual(preflight.check_ollama("http://127.0.0.1:9", "qwen2.5:7b", down).status, WARN)
        missing = preflight.check_ollama("http://x", "qwen2.5:7b", lambda url: {"models": [{"name": "llama3:8b"}]})
        self.assertIn("ollama pull qwen2.5:7b", missing.detail)
        self.assertEqual(preflight.check_ollama("http://x", "llama3", lambda url: {"models": [{"name": "llama3:latest"}]}).status, OK)

    def test_old_python_low_disk_no_microphone_and_unbuilt_hud(self):
        self.assertEqual(preflight.check_python((3, 10, 0)).status, FAIL)
        low = preflight.check_disk(Path("."), usage=lambda _: SimpleNamespace(free=512 * 1024 ** 2))
        self.assertEqual(low.status, WARN)
        self.assertEqual(preflight.check_microphone(lambda: [{"max_input_channels": 0}]).status, WARN)
        hud = preflight.check_native_hud({"hud_native_enabled": True, "hud_native_path": "C:/non/esiste.exe"}, Path("x"))
        self.assertEqual(hud.status, WARN)
        self.assertIsNone(preflight.check_native_hud({}, Path("x")), "HUD nativo spento: niente da controllare")

    def test_a_real_run_on_this_machine_reports_every_check_and_errors_only_for_blockers(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(preflight.check_data_dir(Path(tmp) / "dati").status, OK)
        checks = preflight.run_all({"ollama_model": "modello-che-non-esiste"})
        names = [c.name for c in checks]
        self.assertEqual(names[:3], ["Python", "Dipendenze base", "Dipendenze voce"])
        self.assertNotIn(FAIL, [c.status for c in checks if c.name in ("Ollama", "Microfono", "GPU")])
        self.assertIn(preflight.render(checks).splitlines()[-1].split(":")[0].split(",")[0],
                      ("Tutto pronto.", "Jake puo' partire", "Jake non puo' partire"))


if __name__ == "__main__":
    unittest.main()
