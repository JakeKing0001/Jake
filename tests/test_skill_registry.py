"""Test unitari per core/skill_registry.py::SkillRegistry.register_skill (F1, Trustworthy Agent
Core 3.0 - vedi la fase F1 in ROADMAP.md).

Come tests/test_risk.py, evita di istanziare SkillRegistry() per davvero (il costruttore vero
costruisce anche MemoryManager/NestClient/EmbeddingProvider/VisionProvider/PlannerProvider, vedi
la nota in tests/__init__.py): usa SkillRegistry.__new__ per un oggetto "spoglio" con solo gli
attributi che register_skill() legge (skills, logger), stesso approccio gia' usato per JakeCore
in tests/test_jake_core_permissions.py."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from core.policy_engine import PolicyEngine
from core.sandboxed_skill_worker import SandboxedSkillWorker
from core.skill_registry import SkillRegistry
from core.skill_result import SkillResult


class FakeLoggerCapturingWarnings:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else msg)


class FakeSkill:
    metadata = {"intent": "FAKE", "description": "Una skill finta.", "parameters": {}}

    def execute(self, parameters=None):
        return None


def _bare_registry(skills: dict = None) -> SkillRegistry:
    registry = SkillRegistry.__new__(SkillRegistry)
    registry.skills = dict(skills or {})
    registry.logger = FakeLoggerCapturingWarnings()
    registry._forged_intents = {}  # F1.6: letto da execute()/register_skill()
    registry._sandbox_worker = None
    registry._plugin_violation_counts = {}  # F1.6.8: letto da _execute_forged()/_record_plugin_violation()
    registry._quarantined_plugins = set()
    return registry


class RegisterSkillCollisionTests(unittest.TestCase):
    """F1: prima di questo controllo, un plugin (o un file copiato per errore dentro plugins/)
    poteva dichiarare un intent gia' esistente e sostituire silenziosamente la skill reale
    dietro quel nome - senza che nulla lo segnalasse, nemmeno nei log. register_skill() e' l'
    unico punto d'ingresso usato da plugin/Skill Forge (le skill built-in arrivano con
    self.skills.update(...) direttamente in __init__, mai da qui)."""

    def test_registering_a_brand_new_intent_does_not_warn(self):
        registry = _bare_registry()

        registry.register_skill("BRAND_NEW_INTENT", FakeSkill())

        self.assertEqual(registry.logger.warnings, [])
        self.assertIn("BRAND_NEW_INTENT", registry.skills)

    def test_registering_an_already_taken_intent_warns_but_still_overwrites(self):
        """Non blocca (un plugin che sostituisce di proposito una skill built-in e' un uso
        legittimo del punto di estensione), ma non deve piu' restare silenzioso."""
        original = FakeSkill()
        replacement = FakeSkill()
        registry = _bare_registry({"SYSTEM_POWER": original})

        registry.register_skill("SYSTEM_POWER", replacement)

        self.assertEqual(len(registry.logger.warnings), 1)
        self.assertIn("SYSTEM_POWER", registry.logger.warnings[0])
        self.assertIs(registry.skills["SYSTEM_POWER"], replacement)

    def test_re_registering_the_exact_same_skill_instance_does_not_warn(self):
        """Ri-registrare la STESSA istanza (es. un modulo plugin ricaricato due volte con lo
        stesso oggetto) non e' una collisione da segnalare."""
        skill = FakeSkill()
        registry = _bare_registry({"SOME_INTENT": skill})

        registry.register_skill("SOME_INTENT", skill)

        self.assertEqual(registry.logger.warnings, [])


class ConcurrentListCapabilitiesTests(unittest.TestCase):
    """F1.8.2 (stesso principio gia' applicato altrove in questa sessione): buco reale,
    riprodotto per davvero prima del fix - list_capabilities() iterava self.skills DIRETTAMENTE
    (`for intent, skill in self.skills.items():`), un dict LIVE che register_skill() (il punto
    d'ingresso di plugin/Skill Forge, raggiungibile da un comando voce/companion mentre
    un'altra richiesta concorrente sta facendo routing/retrieval semantico) puo' mutare in
    qualsiasi momento da un altro thread. Un ciclo `for` su un dict live e' un punto di cambio
    thread naturale a ogni iterazione: se la dimensione del dict cambia a meta' ciclo, Python
    solleva RuntimeError, facendo fallire l'intera richiesta in corso."""

    def setUp(self):
        self._original_switch_interval = sys.getswitchinterval()
        sys.setswitchinterval(0.00001)
        self.addCleanup(sys.setswitchinterval, self._original_switch_interval)

    def test_registering_a_skill_while_listing_capabilities_never_raises(self):
        registry = _bare_registry({f"INTENT_{i}": FakeSkill() for i in range(2000)})
        errors: list[Exception] = []
        errors_lock = threading.Lock()

        def _list_repeatedly():
            for _ in range(50):
                try:
                    registry.list_capabilities()
                except RuntimeError as exc:
                    with errors_lock:
                        errors.append(exc)

        def _register_many():
            for i in range(5000):
                registry.register_skill(f"NEW_INTENT_{i}", FakeSkill())

        threads = [threading.Thread(target=_list_repeatedly), threading.Thread(target=_register_many)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [], "list_capabilities() non deve mai sollevare per una mutazione concorrente")


class RecordingSkill:
    def __init__(self):
        self.calls = 0

    def execute(self, parameters=None):
        self.calls += 1
        return SkillResult(success=True, data={})


class PolicyGateTests(unittest.TestCase):
    """F1.2.1 (percorso 7, l'ultimo dei tre "percorso N" dichiarati aperti - i percorsi 3
    (PlanExecutor.execute) e 6 (rollback_effect) erano gia' fail-closed): questo dispatcher grezzo
    non controllava MAI la policy da solo. Nei due chiamanti di produzione reali
    (JakeCore._resolve_and_execute/_run_confirmed_action) non era gia' sfruttabile - entrambi
    chiamano _authorize_command() PRIMA di arrivare qui - ma restava un default pericoloso per un
    futuro chiamante che se lo dimenticasse, stesso principio "nega per default" gia' applicato ai
    percorsi 3/6."""

    def test_no_policy_engine_blocks_without_calling_the_skill(self):
        skill = RecordingSkill()
        registry = _bare_registry({"CREATE_PATH": skill})

        result = registry.execute("CREATE_PATH", {"path": "x"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "POLICY_BLOCKED")
        self.assertEqual(skill.calls, 0)

    def test_intent_in_blocked_intents_blocks_without_calling_the_skill(self):
        skill = RecordingSkill()
        registry = _bare_registry({"DELETE_PATH": skill})
        policy_engine = PolicyEngine(blocked_intents={"DELETE_PATH"})

        result = registry.execute("DELETE_PATH", {"path": "x"}, policy_engine=policy_engine)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "POLICY_BLOCKED")
        self.assertEqual(skill.calls, 0)

    def test_intent_not_blocked_actually_executes_the_skill(self):
        skill = RecordingSkill()
        registry = _bare_registry({"ADD_NOTE": skill})
        policy_engine = PolicyEngine()

        result = registry.execute("ADD_NOTE", {"text": "x"}, policy_engine=policy_engine)

        self.assertTrue(result.success)
        self.assertEqual(skill.calls, 1)

    def test_unknown_intent_still_returns_none_regardless_of_policy_engine(self):
        """Il controllo di policy si applica solo dopo aver trovato la skill: un intent
        sconosciuto resta None (UNKNOWN_INTENT per chi legge il risultato), non POLICY_BLOCKED -
        comportamento invariato rispetto a prima di questa correzione."""
        registry = _bare_registry({})

        self.assertIsNone(registry.execute("NON_ESISTE", {}))
        self.assertIsNone(registry.execute("NON_ESISTE", {}, policy_engine=PolicyEngine()))


class ForgedSkillSandboxWiringTests(unittest.TestCase):
    """F1.6 (collegamento del worker sandboxato alle skill forgiate): un intent registrato con
    plugin_path= esegue DAVVERO nel worker sandboxato (core/sandboxed_skill_worker.py), non in
    processo - verificato confrontando os.getpid() dentro la skill (il worker e' un processo
    SEPARATO, deve riportare un pid diverso da quello di questo stesso processo di test) invece
    di fidarsi solo della lettura del codice."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.registry = _bare_registry()
        self.addCleanup(self.registry.stop_sandbox_worker)

    def _write_pid_reporting_plugin(self, intent: str) -> str:
        path = Path(self._tmpdir.name) / f"{intent.lower()}.py"
        path.write_text(
            "import os\n"
            "from core.skill_result import SkillResult\n"
            "class Skill:\n"
            f"    metadata = {{'intent': '{intent}', 'description': '', 'parameters': {{}}}}\n"
            "    def execute(self, parameters=None):\n"
            "        return SkillResult(success=True, data={'pid': os.getpid()})\n"
            "def register(registry):\n"
            f"    registry.register_skill('{intent}', Skill())\n",
            encoding="utf-8",
        )
        return str(path)

    def test_a_forged_intent_executes_in_a_separate_process_not_this_one(self):
        plugin_path = self._write_pid_reporting_plugin("PID_TEST")
        self.registry.register_skill("PID_TEST", FakeSkill(), plugin_path=plugin_path)

        result = self.registry.execute("PID_TEST", {}, policy_engine=PolicyEngine())

        self.assertTrue(result.success, result.error)
        self.assertNotEqual(result.data["pid"], os.getpid())

    def test_the_live_in_process_skill_object_is_never_invoked_for_a_forged_intent(self):
        """F1.6.7 ("serializzare input/output; nessun oggetto core condiviso col plugin"): vero
        per costruzione con questo protocollo (un processo separato non puo' condividere oggetti
        Python live con Jake), ma qui lo si prova in modo diretto e avversariale, non solo per
        deduzione dal pid diverso (vedi il test sopra) - una spia il cui execute() SOLLEVA se mai
        venisse chiamato, cosi' un'eventuale futura regressione che facesse cadere un intent
        forgiato sul ramo in-processo per errore fallirebbe qui in modo inequivocabile, invece di
        restituire semplicemente un pid sbagliato."""
        plugin_path = self._write_pid_reporting_plugin("SPY_TEST")
        spy = mock.Mock()
        spy.metadata = {"intent": "SPY_TEST", "description": "", "parameters": {}}
        spy.execute.side_effect = AssertionError(
            "l'oggetto skill live in processo non deve MAI essere eseguito per un intent forgiato"
        )
        self.registry.register_skill("SPY_TEST", spy, plugin_path=plugin_path)

        result = self.registry.execute("SPY_TEST", {}, policy_engine=PolicyEngine())

        self.assertTrue(result.success, result.error)
        self.assertNotEqual(result.data["pid"], os.getpid())
        spy.execute.assert_not_called()

    def test_a_policy_block_on_a_forged_intent_never_reaches_the_worker(self):
        """L'ordine conta: blocked_intents si controlla PRIMA del routing verso il worker, stesso
        principio fail-closed gia' verificato per le skill in processo."""
        plugin_path = self._write_pid_reporting_plugin("BLOCKED_FORGED")
        self.registry.register_skill("BLOCKED_FORGED", FakeSkill(), plugin_path=plugin_path)

        result = self.registry.execute(
            "BLOCKED_FORGED", {}, policy_engine=PolicyEngine(blocked_intents={"BLOCKED_FORGED"}),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "POLICY_BLOCKED")
        self.assertIsNone(self.registry._sandbox_worker, "un intent bloccato non deve mai avviare il worker")

    def test_stop_sandbox_worker_is_safe_when_no_forged_skill_was_ever_invoked(self):
        self.registry.stop_sandbox_worker()  # non deve sollevare nulla

    def test_registering_a_new_forged_intent_invalidates_an_already_running_worker(self):
        """Il worker carica i plugin UNA VOLTA all'avvio: una nuova installazione a caldo (Skill
        Forge) dopo che il worker gia' esiste deve farlo ripartire, non restare con l'elenco
        vecchio - altrimenti la skill appena installata non sarebbe mai servibile."""
        first_plugin = self._write_pid_reporting_plugin("FIRST_FORGED")
        self.registry.register_skill("FIRST_FORGED", FakeSkill(), plugin_path=first_plugin)
        self.registry.execute("FIRST_FORGED", {}, policy_engine=PolicyEngine())  # avvia il worker
        self.assertIsNotNone(self.registry._sandbox_worker)

        second_plugin = self._write_pid_reporting_plugin("SECOND_FORGED")
        self.registry.register_skill("SECOND_FORGED", FakeSkill(), plugin_path=second_plugin)

        self.assertIsNone(self.registry._sandbox_worker, "un nuovo intent forgiato deve invalidare il worker gia' avviato")

        result = self.registry.execute("SECOND_FORGED", {}, policy_engine=PolicyEngine())
        self.assertTrue(result.success, result.error)

    def test_a_real_timeout_from_the_worker_is_recorded_as_a_violation(self):
        """F1.6.8 (prova di collegamento vera, non solo la logica isolata sotto): un timeout
        VERO restituito da un worker VERO (una skill che dorme piu' a lungo del timeout
        configurato) deve incrementare il conteggio delle violazioni del plugin - non solo
        simulato passando 'SANDBOX_WORKER_TIMEOUT' a mano."""
        plugin_path = self._write_sleepy_plugin("SLOW_FORGED", sleep_seconds=2.0)
        self.registry.register_skill("SLOW_FORGED", FakeSkill(), plugin_path=plugin_path)
        project_root = str(Path(__file__).resolve().parent.parent)
        worker = SandboxedSkillWorker(project_root=project_root, plugin_paths=[plugin_path], invoke_timeout_seconds=0.2)
        worker.start()
        self.addCleanup(worker.stop)
        self.registry._sandbox_worker = worker

        result = self.registry.execute("SLOW_FORGED", {}, policy_engine=PolicyEngine())

        self.assertFalse(result.success)
        self.assertEqual(result.error, "SANDBOX_WORKER_TIMEOUT")
        self.assertEqual(self.registry._plugin_violation_counts.get(plugin_path), 1)

    def _write_sleepy_plugin(self, intent: str, sleep_seconds: float) -> str:
        path = Path(self._tmpdir.name) / f"{intent.lower()}.py"
        path.write_text(
            "import time\n"
            "from core.skill_result import SkillResult\n"
            "class Skill:\n"
            f"    metadata = {{'intent': '{intent}', 'description': '', 'parameters': {{}}}}\n"
            "    def execute(self, parameters=None):\n"
            f"        time.sleep({sleep_seconds})\n"
            "        return SkillResult(success=True, data={})\n"
            "def register(registry):\n"
            f"    registry.register_skill('{intent}', Skill())\n",
            encoding="utf-8",
        )
        return str(path)


class ForgedSkillQuarantineTests(unittest.TestCase):
    """F1.6.8 ("terminare e mettere in quarantena plugin che viola limiti o protocollo") - logica
    di quarantena isolata dal worker vero (gia' provata collegata per davvero da
    ForgedSkillSandboxWiringTests.test_a_real_timeout_from_the_worker_is_recorded_as_a_violation):
    qui si verifica il COMPORTAMENTO della quarantena (soglia, esclusione da un riavvio, ripristino
    esplicito) senza pagare il costo di un worker/processo vero per ogni caso."""

    def setUp(self):
        self.registry = _bare_registry({"FLAKY_FORGED": FakeSkill()})
        self.registry._forged_intents = {"FLAKY_FORGED": "C:\\plugins\\flaky.py"}

    def test_fewer_than_the_threshold_violations_do_not_quarantine(self):
        for _ in range(SkillRegistry._QUARANTINE_THRESHOLD - 1):
            self.registry._record_plugin_violation("C:\\plugins\\flaky.py")

        self.assertNotIn("C:\\plugins\\flaky.py", self.registry._quarantined_plugins)

    def test_reaching_the_threshold_quarantines_the_plugin(self):
        for _ in range(SkillRegistry._QUARANTINE_THRESHOLD):
            self.registry._record_plugin_violation("C:\\plugins\\flaky.py")

        self.assertIn("C:\\plugins\\flaky.py", self.registry._quarantined_plugins)

    def test_a_quarantined_plugins_intent_is_refused_without_touching_the_worker(self):
        for _ in range(SkillRegistry._QUARANTINE_THRESHOLD):
            self.registry._record_plugin_violation("C:\\plugins\\flaky.py")

        result = self.registry.execute("FLAKY_FORGED", {}, policy_engine=PolicyEngine())

        self.assertFalse(result.success)
        self.assertEqual(result.error, "SKILL_QUARANTINED")
        self.assertIsNone(self.registry._sandbox_worker, "un intent in quarantena non deve mai avviare il worker")

    def test_a_quarantined_plugin_is_excluded_from_the_next_worker_start(self):
        self.registry._forged_intents = {
            "FLAKY_FORGED": "C:\\plugins\\flaky.py", "GOOD_FORGED": "C:\\plugins\\good.py",
        }
        for _ in range(SkillRegistry._QUARANTINE_THRESHOLD):
            self.registry._record_plugin_violation("C:\\plugins\\flaky.py")

        with mock.patch("core.skill_registry.SandboxedSkillWorker") as worker_cls:
            worker_cls.return_value.start.return_value = None
            self.registry._get_or_start_sandbox_worker()

        called_plugin_paths = worker_cls.call_args.kwargs["plugin_paths"]
        self.assertEqual(called_plugin_paths, ["C:\\plugins\\good.py"])

    def test_clear_quarantine_restores_normal_execution(self):
        for _ in range(SkillRegistry._QUARANTINE_THRESHOLD):
            self.registry._record_plugin_violation("C:\\plugins\\flaky.py")
        self.assertIn("C:\\plugins\\flaky.py", self.registry._quarantined_plugins)

        self.registry.clear_quarantine("C:\\plugins\\flaky.py")

        self.assertNotIn("C:\\plugins\\flaky.py", self.registry._quarantined_plugins)
        self.assertEqual(self.registry._plugin_violation_counts.get("C:\\plugins\\flaky.py"), None)


if __name__ == "__main__":
    unittest.main()
