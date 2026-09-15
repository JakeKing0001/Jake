"""Verifica per davvero (non solo per costruzione) che core/sandboxed_skill_worker.py contenga
per davvero l'esecuzione PERMANENTE di una skill forgiata, non solo il passo di validazione una
tantum gia' coperto da core/process_sandbox.py (vedi tests/test_process_sandbox.py per lo stesso
principio su quel modulo gemello). Ogni test che avvia un worker vero spawna un processo Python
reale: piu' lento dei test unitari puri, ma e' l'unico modo di dimostrare che il confine di
sicurezza sia imposto dal sistema operativo, non solo dichiarato nel codice."""
import tempfile
import time
import unittest
from pathlib import Path

from core.sandboxed_skill_worker import _WIN32_AVAILABLE, SandboxedSkillWorker

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)

ECHO_PLUGIN = """
from core.skill_result import SkillResult

class EchoSkill:
    metadata = {"intent": "ECHO_TEST", "description": "test", "parameters": {}}

    def execute(self, parameters=None):
        parameters = parameters or {}
        return SkillResult(success=True, data={"echo": parameters.get("text")})


def register(registry):
    registry.register_skill("ECHO_TEST", EchoSkill())
"""


def _write_plugin(tmp_dir: Path, source: str, name: str = "plugin.py") -> str:
    path = tmp_dir / name
    path.write_text(source, encoding="utf-8")
    return str(path)


class RealInvocationRoundTripTests(unittest.TestCase):
    def test_a_real_invocation_round_trips_through_the_worker(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin_path = _write_plugin(Path(tmp_dir), ECHO_PLUGIN)
            worker = SandboxedSkillWorker(project_root=_PROJECT_ROOT, plugin_paths=[plugin_path])
            self.addCleanup(worker.stop)
            worker.start()

            result = worker.invoke("ECHO_TEST", {"text": "ciao"})

            self.assertTrue(result.success)
            self.assertEqual(result.data, {"echo": "ciao"})

    def test_an_unknown_intent_reports_an_error_and_the_worker_stays_usable(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin_path = _write_plugin(Path(tmp_dir), ECHO_PLUGIN)
            worker = SandboxedSkillWorker(project_root=_PROJECT_ROOT, plugin_paths=[plugin_path])
            self.addCleanup(worker.stop)
            worker.start()

            missing = worker.invoke("NOT_REGISTERED", {})
            self.assertFalse(missing.success)

            still_alive = worker.invoke("ECHO_TEST", {"text": "ancora vivo"})
            self.assertTrue(still_alive.success)

    def test_stop_leaves_the_worker_not_alive(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin_path = _write_plugin(Path(tmp_dir), ECHO_PLUGIN)
            worker = SandboxedSkillWorker(project_root=_PROJECT_ROOT, plugin_paths=[plugin_path])
            worker.start()
            self.assertTrue(worker.is_alive())

            worker.stop()

            self.assertFalse(worker.is_alive())

    def test_stop_before_start_does_not_raise(self):
        worker = SandboxedSkillWorker(project_root=_PROJECT_ROOT, plugin_paths=[])
        worker.stop()  # non deve sollevare nulla: mai avviato, niente da fermare


class ResourceContainmentTests(unittest.TestCase):
    """Le due proprieta' di sicurezza chiave (vedi tests/test_process_sandbox.py per lo stesso
    principio sul modulo gemello): un worker a integrita' ridotta non puo' scrivere fuori dal
    proprio processo, e un'allocazione oltre il limite del Job Object fallisce senza portarsi
    via l'intero worker condiviso da tutte le skill forgiate."""

    def test_the_worker_cannot_write_outside_its_own_temp_directory(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            canary_path = str(tmp_path / "canary.txt").replace("\\", "\\\\")
            plugin_source = (
                "from core.skill_result import SkillResult\n"
                "class WriteSkill:\n"
                "    metadata = {'intent': 'WRITE_TEST', 'description': 'test', 'parameters': {}}\n"
                "    def execute(self, parameters=None):\n"
                "        try:\n"
                f"            with open('{canary_path}', 'w') as f:\n"
                "                f.write('leak')\n"
                "            return SkillResult(success=True, data={'wrote': True})\n"
                "        except PermissionError:\n"
                "            return SkillResult(success=False, data={}, error='PERMISSION_DENIED')\n"
                "def register(registry):\n"
                "    registry.register_skill('WRITE_TEST', WriteSkill())\n"
            )
            plugin_path = _write_plugin(tmp_path, plugin_source)
            worker = SandboxedSkillWorker(project_root=_PROJECT_ROOT, plugin_paths=[plugin_path])
            self.addCleanup(worker.stop)
            worker.start()

            result = worker.invoke("WRITE_TEST", {})

            if worker.integrity_restricted:
                self.assertFalse(result.success, "il worker a integrita' ridotta e' riuscito a scrivere: la restrizione MIC non funziona")
                self.assertFalse((tmp_path / "canary.txt").exists())
            else:
                if not _WIN32_AVAILABLE:
                    self.skipTest("pywin32 non disponibile: nessuna restrizione da verificare")
                self.skipTest("la sandbox a integrita' ridotta non si e' attivata in questo ambiente (ripiego usato)")

    def test_exceeding_the_memory_limit_fails_the_call_without_killing_the_worker(self):
        if not _WIN32_AVAILABLE:
            self.skipTest("pywin32 non disponibile: nessun Job Object da verificare")
        plugin_source = (
            "from core.skill_result import SkillResult\n"
            "class HogSkill:\n"
            "    metadata = {'intent': 'HOG_TEST', 'description': 'test', 'parameters': {}}\n"
            "    def execute(self, parameters=None):\n"
            "        data = bytearray(500 * 1024 * 1024)\n"
            "        return SkillResult(success=True, data={})\n"
            "class EchoSkill:\n"
            "    metadata = {'intent': 'ECHO_TEST', 'description': 'test', 'parameters': {}}\n"
            "    def execute(self, parameters=None):\n"
            "        return SkillResult(success=True, data={})\n"
            "def register(registry):\n"
            "    registry.register_skill('HOG_TEST', HogSkill())\n"
            "    registry.register_skill('ECHO_TEST', EchoSkill())\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin_path = _write_plugin(Path(tmp_dir), plugin_source)
            worker = SandboxedSkillWorker(
                project_root=_PROJECT_ROOT, plugin_paths=[plugin_path],
                memory_limit_bytes=50 * 1024 * 1024, invoke_timeout_seconds=10,
            )
            self.addCleanup(worker.stop)
            worker.start()
            if not worker.integrity_restricted:
                self.skipTest("Job Object non attivo in questo ambiente (ripiego usato)")

            hog_result = worker.invoke("HOG_TEST", {})
            self.assertFalse(hog_result.success)

            still_alive = worker.invoke("ECHO_TEST", {})
            self.assertTrue(still_alive.success, "il worker deve sopravvivere a una singola chiamata che esagera con la memoria")

    def test_a_forged_skill_cannot_spawn_an_unbounded_number_of_child_processes(self):
        """F1.6.3 ("aggiungere Job Object per... process tree"): ActiveProcessLimit ferma un
        fork bomb / uno spawn senza limite, imposto dal KERNEL, non da un controllo applicativo
        che una skill ostile potrebbe scoprire e aggirare. Il tetto e' volutamente GENEROSO
        (default 32, qui abbassato per velocita' del test), non il minimo stretto "solo il
        worker" - vedi il docstring del modulo: quel minimo si e' rivelato troppo stretto per
        davvero, rompendo l'avvio del worker in questo stesso ambiente di sviluppo (un venv `uv`
        il cui python.exe rilancia l'interprete vero come figlio, due processi solo per partire).
        Qui si spawnano piu' figli veri (non una simulazione) finche' uno non viene negato,
        provando che il tetto esiste per davvero senza assumere quanti processi l'avvio
        dell'interprete stesso consumi in QUESTO ambiente."""
        if not _WIN32_AVAILABLE:
            self.skipTest("pywin32 non disponibile: nessun Job Object da verificare")
        plugin_source = (
            "from core.skill_result import SkillResult\n"
            "import subprocess, sys\n"
            "class SpawnManyTestSkill:\n"
            "    metadata = {'intent': 'SPAWN_MANY_TEST', 'description': 'test', 'parameters': {}}\n"
            "    def execute(self, parameters=None):\n"
            "        spawned = 0\n"
            "        hit_limit = False\n"
            "        for _ in range(10):\n"
            "            try:\n"
            "                subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'])\n"
            "                spawned += 1\n"
            "            except OSError:\n"
            "                hit_limit = True\n"
            "                break\n"
            "        return SkillResult(success=True, data={'spawned': spawned, 'hit_limit': hit_limit})\n"
            "class EchoSkill:\n"
            "    metadata = {'intent': 'ECHO_TEST', 'description': 'test', 'parameters': {}}\n"
            "    def execute(self, parameters=None):\n"
            "        return SkillResult(success=True, data={})\n"
            "def register(registry):\n"
            "    registry.register_skill('SPAWN_MANY_TEST', SpawnManyTestSkill())\n"
            "    registry.register_skill('ECHO_TEST', EchoSkill())\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin_path = _write_plugin(Path(tmp_dir), plugin_source)
            worker = SandboxedSkillWorker(
                project_root=_PROJECT_ROOT, plugin_paths=[plugin_path],
                invoke_timeout_seconds=15, max_processes=5,
            )
            self.addCleanup(worker.stop)
            worker.start()
            if not worker.integrity_restricted:
                self.skipTest("Job Object non attivo in questo ambiente (ripiego usato)")

            spawn_result = worker.invoke("SPAWN_MANY_TEST", {})

            self.assertTrue(spawn_result.success)
            self.assertTrue(
                spawn_result.data["hit_limit"],
                f"ActiveProcessLimit=5 doveva fermare almeno uno dei 10 tentativi di spawn "
                f"(ne sono partiti {spawn_result.data['spawned']})",
            )
            self.assertLess(spawn_result.data["spawned"], 10)

            still_alive = worker.invoke("ECHO_TEST", {})
            self.assertTrue(still_alive.success, "il worker deve sopravvivere a uno spawn negato dal limite")


class TimeoutTests(unittest.TestCase):
    def test_a_call_that_never_responds_times_out_instead_of_hanging_forever(self):
        plugin_source = (
            "from core.skill_result import SkillResult\n"
            "import time\n"
            "class SleepSkill:\n"
            "    metadata = {'intent': 'SLEEP_TEST', 'description': 'test', 'parameters': {}}\n"
            "    def execute(self, parameters=None):\n"
            "        time.sleep(30)\n"
            "        return SkillResult(success=True, data={})\n"
            "def register(registry):\n"
            "    registry.register_skill('SLEEP_TEST', SleepSkill())\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin_path = _write_plugin(Path(tmp_dir), plugin_source)
            worker = SandboxedSkillWorker(
                project_root=_PROJECT_ROOT, plugin_paths=[plugin_path], invoke_timeout_seconds=2,
            )
            self.addCleanup(worker.stop)
            worker.start()
            started = time.monotonic()

            result = worker.invoke("SLEEP_TEST", {})

            self.assertFalse(result.success)
            self.assertEqual(result.error, "SANDBOX_WORKER_TIMEOUT")
            # F1.6.3: invoke() ora forza anche l'arresto del worker rimasto indietro (vedi sotto),
            # che aggiunge fino al timeout di stop() (3s di default) oltre a invoke_timeout_seconds
            # - il margine resta ampio apposta, non e' un limite stretto sul tempo esatto.
            self.assertLess(time.monotonic() - started, 10, "invoke() non deve aspettare indefinitamente")

    def test_a_timed_out_worker_is_actually_terminated_not_left_running(self):
        """F1.6.3 ("timeout wall-clock imposto dal Job Object stesso" - Job Object non ha affatto
        un tipo di limite wall-clock, solo CPU: l'unico modo reale e' un watchdog esterno che
        termini il processo). Prima di questa correzione un timeout faceva solo rinunciare il
        CHIAMANTE, lasciando il worker vero ancora vivo in background."""
        plugin_source = (
            "from core.skill_result import SkillResult\n"
            "import time\n"
            "class SleepSkill:\n"
            "    metadata = {'intent': 'SLEEP_TEST', 'description': 'test', 'parameters': {}}\n"
            "    def execute(self, parameters=None):\n"
            "        time.sleep(30)\n"
            "        return SkillResult(success=True, data={})\n"
            "def register(registry):\n"
            "    registry.register_skill('SLEEP_TEST', SleepSkill())\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin_path = _write_plugin(Path(tmp_dir), plugin_source)
            worker = SandboxedSkillWorker(
                project_root=_PROJECT_ROOT, plugin_paths=[plugin_path], invoke_timeout_seconds=2,
            )
            self.addCleanup(worker.stop)
            worker.start()

            worker.invoke("SLEEP_TEST", {})

            self.assertFalse(worker.is_alive(), "il worker rimasto indietro doveva essere terminato, non lasciato vivo")

    def test_a_second_call_on_the_same_timed_out_worker_is_refused_not_stale(self):
        """Buco reale riprodotto prima di correggerlo: una risposta arrivata IN RITARDO da una
        chiamata gia' scaduta per timeout restava nella coda condivisa e veniva consumata dalla
        chiamata SUCCESSIVA sullo STESSO oggetto worker, per un intent completamente diverso - un
        caso di corsa scoperto facendo davvero completare la skill lenta (non solo simulato) e
        verificando cosa la chiamata dopo riceveva per davvero. Dopo la correzione il worker che
        ha appena scaduto un timeout e' morto per davvero (vedi il test sopra): una SECONDA
        chiamata sullo STESSO oggetto deve quindi essere rifiutata onestamente
        (SANDBOX_WORKER_UNAVAILABLE), mai restituire la risposta vecchia della skill lenta - chi
        chiama (core/skill_registry.py::_get_or_start_sandbox_worker) e' responsabile di costruire
        un worker NUOVO dopo questo, vedi tests/test_skill_registry.py per quella parte."""
        plugin_source = (
            "from core.skill_result import SkillResult\n"
            "import time\n"
            "class SlowSkill:\n"
            "    metadata = {'intent': 'SLOW_TEST', 'description': 'test', 'parameters': {}}\n"
            "    def execute(self, parameters=None):\n"
            "        time.sleep(3)\n"
            "        return SkillResult(success=True, data={'marker': 'STALE_SLOW_RESPONSE'})\n"
            "def register(registry):\n"
            "    registry.register_skill('SLOW_TEST', SlowSkill())\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            plugin_path = _write_plugin(Path(tmp_dir), plugin_source)
            worker = SandboxedSkillWorker(
                project_root=_PROJECT_ROOT, plugin_paths=[plugin_path], invoke_timeout_seconds=1,
            )
            self.addCleanup(worker.stop)
            worker.start()

            slow_result = worker.invoke("SLOW_TEST", {})
            self.assertEqual(slow_result.error, "SANDBOX_WORKER_TIMEOUT")

            second_result = worker.invoke("SLOW_TEST", {})

            self.assertFalse(second_result.success)
            self.assertEqual(second_result.error, "SANDBOX_WORKER_UNAVAILABLE")
            self.assertNotEqual(second_result.data.get("marker"), "STALE_SLOW_RESPONSE")


if __name__ == "__main__":
    unittest.main()
