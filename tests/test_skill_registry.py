"""Test unitari per core/skill_registry.py::SkillRegistry.register_skill (F1, Trustworthy Agent
Core 3.0 - vedi la fase F1 in ROADMAP.md).

Come tests/test_risk.py, evita di istanziare SkillRegistry() per davvero (il costruttore vero
costruisce anche MemoryManager/NestClient/EmbeddingProvider/VisionProvider/PlannerProvider, vedi
la nota in tests/__init__.py): usa SkillRegistry.__new__ per un oggetto "spoglio" con solo gli
attributi che register_skill() legge (skills, logger), stesso approccio gia' usato per JakeCore
in tests/test_jake_core_permissions.py."""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from core.action_snapshot import SnapshotStore
from core.policy_engine import PolicyEngine
from core.resource_lock import ResourceLockManager
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
    registry._resource_locks = ResourceLockManager()  # F1.8.1: letto da execute() per le 4 mutazioni filesystem
    registry.snapshot_store = SnapshotStore()  # F1.3.4: letto/scritto da execute() per DELETE_PATH
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


class FilesystemMutationResourceLockTests(unittest.TestCase):
    """F1.8.1 (ultimo pezzo, "una coda per azioni concorrenti"): buco reale riprodotto
    empiricamente PRIMA di questo fix (due thread veri, non ipotizzato) - le quattro skill di
    mutazione filesystem (stesso gruppo di F1.2.2) fanno tutte un controllo-poi-agisci non
    atomico. Due MOVE_PATH concorrenti, sorgenti diverse ma stesso NOME file verso la STESSA
    cartella di destinazione, superavano ENTRAMBI il controllo "il file di destinazione non
    esiste ancora" prima che uno dei due lo creasse davvero: entrambi riportavano `success=True`,
    ma uno dei due file spariva silenziosamente sovrascritto dall'altro, senza alcun
    `ALREADY_EXISTS` ne' altro errore. Ora serializzato per resource key
    (`core/resource_lock.py::ResourceLockManager`, il meccanismo gia' costruito e testato in
    isolamento nella fase 7 del piano multi-device di F1.4 ma mai collegato prima d'ora a un
    chokepoint di produzione reale)."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_resource_lock_test_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_two_concurrent_move_path_to_the_same_destination_never_lose_a_file(self):
        from skills.move_path import MovePathSkill

        source_a = self.tmp_dir / "a.txt"
        source_a.write_text("contenuto A")
        source_b_dir = self.tmp_dir / "sub"
        source_b_dir.mkdir()
        source_b = source_b_dir / "a.txt"  # stesso NOME file di source_a, cartella diversa
        source_b.write_text("contenuto B")
        dest_dir = self.tmp_dir / "dest"
        dest_dir.mkdir()

        registry = _bare_registry({"MOVE_PATH": MovePathSkill()})
        policy_engine = PolicyEngine()
        start_barrier = threading.Barrier(2)
        real_move = shutil.move

        def slow_move(src, dst):
            # Tiene il lock impegnato abbastanza a lungo da forzare DAVVERO l'altro thread ad
            # attendere sul lock (non solo a "vincere per fortuna" dello scheduler): la prova che
            # serve non e' che i due thread partano insieme, ma che il secondo resti bloccato
            # finche' il primo non ha finito l'INTERA sequenza controllo+azione.
            time.sleep(0.1)
            return real_move(src, dst)

        results: list[SkillResult] = []
        results_lock = threading.Lock()

        def _move(source: Path):
            start_barrier.wait(timeout=5)
            result = registry.execute(
                "MOVE_PATH", {"path": str(source), "destination": str(dest_dir), "confirmed": True},
                policy_engine=policy_engine,
            )
            with results_lock:
                results.append(result)

        threads = [threading.Thread(target=_move, args=(source,)) for source in (source_a, source_b)]
        with mock.patch("shutil.move", slow_move):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        self.assertEqual(len(successes), 1, "solo UNA delle due mosse concorrenti deve riuscire, mai entrambe")
        self.assertEqual(len(failures), 1)
        self.assertEqual(
            failures[0].error, "ALREADY_EXISTS",
            "il secondo tentativo deve fallire onestamente (il lock lo fa ripartire dopo il primo), non sovrascrivere in silenzio",
        )
        surviving_sources = [path for path in (source_a, source_b) if path.exists()]
        self.assertEqual(len(surviving_sources), 1, "il sorgente della mossa fallita non deve mai sparire")
        self.assertEqual((dest_dir / "a.txt").read_text(), "contenuto A" if surviving_sources[0] == source_b else "contenuto B")

    def test_resource_lock_keys_are_empty_for_non_filesystem_intents(self):
        registry = _bare_registry()
        self.assertEqual(registry._resource_lock_keys("ADD_NOTE", {"text": "x"}), ())
        self.assertEqual(registry._resource_lock_keys("CREATE_PATH", None), ())
        self.assertEqual(registry._resource_lock_keys("CREATE_PATH", {}), ())

    def test_resource_lock_keys_normalize_case_and_resolve_relative_paths(self):
        """Due grafie diverse dello STESSO percorso (case diverso su Windows, o un percorso
        relativo/assoluto equivalente) devono produrre la STESSA resource key - altrimenti il
        lock non serializzerebbe davvero le due chiamate."""
        registry = _bare_registry()
        target = self.tmp_dir / "File.txt"

        keys_lower = registry._resource_lock_keys("DELETE_PATH", {"path": str(target).lower()})
        keys_upper = registry._resource_lock_keys("DELETE_PATH", {"path": str(target).upper()})

        self.assertEqual(keys_lower, keys_upper)
        self.assertEqual(len(keys_lower), 1)

    def test_resource_lock_keys_for_move_path_cover_both_source_and_destination(self):
        registry = _bare_registry()
        keys = registry._resource_lock_keys(
            "MOVE_PATH", {"path": str(self.tmp_dir / "a.txt"), "destination": str(self.tmp_dir / "dest")},
        )
        self.assertEqual(len(keys), 2)

    def test_resource_lock_keys_accept_pathlike_values(self):
        registry = _bare_registry()
        target = self.tmp_dir / "nested" / "file.txt"
        target.parent.mkdir(exist_ok=True)

        keys_from_path = registry._resource_lock_keys("DELETE_PATH", {"path": target})
        keys_from_str = registry._resource_lock_keys("DELETE_PATH", {"path": str(target)})

        self.assertEqual(keys_from_path, keys_from_str)
        self.assertEqual(len(keys_from_path), 1)

    def test_an_unrelated_resource_key_is_never_blocked_by_a_slow_filesystem_mutation(self):
        """Il lock e' per RESOURCE KEY, non un lock unico globale sul filesystem: un MOVE_PATH
        lento su una cartella non deve mai bloccare un CREATE_PATH concorrente su una cartella
        completamente indipendente."""
        from skills.create_path import CreatePathSkill
        from skills.move_path import MovePathSkill

        source = self.tmp_dir / "lento.txt"
        source.write_text("x")
        slow_dest = self.tmp_dir / "slow_dest"
        slow_dest.mkdir()
        unrelated_new_file = self.tmp_dir / "unrelated" / "nuovo.txt"
        unrelated_new_file.parent.mkdir()

        registry = _bare_registry({"MOVE_PATH": MovePathSkill(), "CREATE_PATH": CreatePathSkill()})
        policy_engine = PolicyEngine()
        started_slow_move = threading.Event()
        release_slow_move = threading.Event()
        real_move = shutil.move

        def slow_move(src, dst):
            started_slow_move.set()
            release_slow_move.wait(timeout=5)
            return real_move(src, dst)

        create_result: list[SkillResult] = []

        def _slow_move_thread():
            with mock.patch("shutil.move", slow_move):
                registry.execute(
                    "MOVE_PATH", {"path": str(source), "destination": str(slow_dest), "confirmed": True},
                    policy_engine=policy_engine,
                )

        move_thread = threading.Thread(target=_slow_move_thread)
        move_thread.start()
        self.assertTrue(started_slow_move.wait(timeout=5), "il MOVE_PATH lento non e' mai partito")

        create_result.append(registry.execute(
            "CREATE_PATH", {"path": str(unrelated_new_file)}, policy_engine=policy_engine,
        ))
        release_slow_move.set()
        move_thread.join(timeout=5)

        self.assertTrue(create_result[0].success, "una risorsa indipendente non deve mai attendere il lock di un'altra")


class DeletePathSnapshotAdoptionTests(unittest.TestCase):
    """F1.3.4 (adozione - prima fetta, vedi core/action_snapshot.py): DELETE_PATH e' l'unica
    delle quattro mutazioni filesystem senza un rollback naturale (core/execution_safety.py::
    INTENT_SAFETY_REGISTRY) - solo uno snapshot del contenuto PRIMA della cancellazione rende un
    futuro ripristino possibile. action_id/private sono opzionali su execute(): questi test
    coprono sia il caso "chi li passa ottiene uno snapshot vero" sia "chi non li passa ancora
    (l'agente/PlanExecutor, prossima fetta dichiarata) non vede alcun cambio di comportamento"."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_snapshot_test_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_a_confirmed_delete_path_with_an_action_id_snapshots_the_content_first(self):
        from skills.delete_path import DeletePathSkill

        target = self.tmp_dir / "nota.txt"
        target.write_text("contenuto vero da salvare")
        registry = _bare_registry({"DELETE_PATH": DeletePathSkill()})

        result = registry.execute(
            "DELETE_PATH", {"path": str(target), "confirmed": True},
            policy_engine=PolicyEngine(), action_id="action-1",
        )

        self.assertTrue(result.success)
        self.assertFalse(target.exists(), "la cancellazione vera deve comunque avvenire")
        snapshot = registry.snapshot_store.get("action-1")
        self.assertIsNotNone(snapshot, "il contenuto deve essere stato catturato PRIMA della cancellazione")
        self.assertEqual(snapshot.content, b"contenuto vero da salvare")

    def test_without_an_action_id_no_snapshot_is_captured(self):
        """Il comportamento di chi non passa ancora action_id (l'agente/PlanExecutor, prossima
        fetta dichiarata) resta invariato - nessuno snapshot, come prima di questo incremento."""
        from skills.delete_path import DeletePathSkill

        target = self.tmp_dir / "nota.txt"
        target.write_text("x")
        registry = _bare_registry({"DELETE_PATH": DeletePathSkill()})

        result = registry.execute(
            "DELETE_PATH", {"path": str(target), "confirmed": True}, policy_engine=PolicyEngine(),
        )

        self.assertTrue(result.success)
        self.assertEqual(registry.snapshot_store._snapshots, {})

    def test_private_mode_never_captures_a_snapshot_even_with_an_action_id(self):
        """Stessa garanzia di privacy gia' data altrove (core/action_snapshot.py::
        capture_snapshot, action_ledger.record, JakeCore._answer_inner): uno scambio in
        modalita' privata non deve lasciare traccia."""
        from skills.delete_path import DeletePathSkill

        target = self.tmp_dir / "segreto.txt"
        target.write_text("dato sensibile")
        registry = _bare_registry({"DELETE_PATH": DeletePathSkill()})

        result = registry.execute(
            "DELETE_PATH", {"path": str(target), "confirmed": True},
            policy_engine=PolicyEngine(), action_id="action-1", private=True,
        )

        self.assertTrue(result.success)
        self.assertIsNone(registry.snapshot_store.get("action-1"))

    def test_a_different_filesystem_intent_never_gets_a_snapshot(self):
        """CREATE_PATH/RENAME_PATH/MOVE_PATH hanno gia' un rollback vero (ri-eseguire
        l'inverso) - non serve loro anche uno snapshot, solo DELETE_PATH e' il candidato oggi."""
        from skills.create_path import CreatePathSkill

        target = self.tmp_dir / "nuovo.txt"
        registry = _bare_registry({"CREATE_PATH": CreatePathSkill()})

        result = registry.execute(
            "CREATE_PATH", {"path": str(target)}, policy_engine=PolicyEngine(), action_id="action-1",
        )

        self.assertTrue(result.success)
        self.assertIsNone(registry.snapshot_store.get("action-1"))

    def test_a_delete_path_that_does_not_actually_delete_anything_captures_no_snapshot(self):
        """Confermare senza 'confirmed' (la skill chiede conferma, non cancella) non deve
        catturare comunque il contenuto - capture_snapshot() e' innocuo qui, ma vale la pena
        dichiarare esplicitamente che non succede nulla di indesiderato."""
        from skills.delete_path import DeletePathSkill

        target = self.tmp_dir / "nota.txt"
        target.write_text("x")
        registry = _bare_registry({"DELETE_PATH": DeletePathSkill()})

        result = registry.execute(
            "DELETE_PATH", {"path": str(target)}, policy_engine=PolicyEngine(), action_id="action-1",
        )

        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertTrue(target.exists())
        # capture_snapshot() e' comunque avvenuto (il lock/percorso risolto sono gli stessi): e'
        # onesto, non un buco - lo snapshot esiste ma non serve mai perche' nulla e' stato
        # cancellato davvero. Dichiarato qui invece di lasciarlo implicito.
        self.assertIsNotNone(registry.snapshot_store.get("action-1"))


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

    def test_the_call_right_after_a_real_timeout_gets_a_correct_fresh_answer_not_a_stale_one(self):
        """F1.6.3 (fix del worker che rimaneva vivo dopo un timeout, vedi core/
        sandboxed_skill_worker.py::invoke()): prova end-to-end che il percorso reale - non solo
        il singolo `SandboxedSkillWorker`, vedi tests/test_sandboxed_skill_worker.py per quella
        parte - si riprende davvero da un timeout. Registra un intent lento (che scade) E uno
        rapido nello STESSO plugin/worker, fa scadere il primo per davvero, poi invoca il secondo:
        `_get_or_start_sandbox_worker()` deve accorgersi che il vecchio worker e' morto (terminato
        dal fix) e avviarne uno nuovo, cosi' il secondo intent riceve la propria risposta VERA
        (non quella - mai arrivata a questo worker nuovo - della skill lenta di prima)."""
        plugin_path = self._write_sleepy_and_echo_plugin("SLOW_FORGED", "FAST_FORGED", sleep_seconds=2.0)
        self.registry.register_skill("SLOW_FORGED", FakeSkill(), plugin_path=plugin_path)
        self.registry.register_skill("FAST_FORGED", FakeSkill(), plugin_path=plugin_path)
        project_root = str(Path(__file__).resolve().parent.parent)
        worker = SandboxedSkillWorker(project_root=project_root, plugin_paths=[plugin_path], invoke_timeout_seconds=0.2)
        worker.start()
        self.registry._sandbox_worker = worker

        slow_result = self.registry.execute("SLOW_FORGED", {}, policy_engine=PolicyEngine())
        self.assertEqual(slow_result.error, "SANDBOX_WORKER_TIMEOUT")

        fresh_result = self.registry.execute("FAST_FORGED", {}, policy_engine=PolicyEngine())

        # setUp() gia' registra self.registry.stop_sandbox_worker in addCleanup - letto a
        # teardown, quando self.registry._sandbox_worker punta gia' al worker nuovo qui sotto.
        self.assertTrue(fresh_result.success, fresh_result.error)
        self.assertEqual(fresh_result.data, {"fast": True})
        self.assertIsNot(self.registry._sandbox_worker, worker, "doveva essere avviato un worker NUOVO dopo il timeout")

    def _write_sleepy_and_echo_plugin(self, slow_intent: str, fast_intent: str, sleep_seconds: float) -> str:
        path = Path(self._tmpdir.name) / f"{slow_intent.lower()}.py"
        path.write_text(
            "import time\n"
            "from core.skill_result import SkillResult\n"
            "class SlowSkill:\n"
            f"    metadata = {{'intent': '{slow_intent}', 'description': '', 'parameters': {{}}}}\n"
            "    def execute(self, parameters=None):\n"
            f"        time.sleep({sleep_seconds})\n"
            "        return SkillResult(success=True, data={'marker': 'STALE_SLOW_RESPONSE'})\n"
            "class FastSkill:\n"
            f"    metadata = {{'intent': '{fast_intent}', 'description': '', 'parameters': {{}}}}\n"
            "    def execute(self, parameters=None):\n"
            "        return SkillResult(success=True, data={'fast': True})\n"
            "def register(registry):\n"
            f"    registry.register_skill('{slow_intent}', SlowSkill())\n"
            f"    registry.register_skill('{fast_intent}', FastSkill())\n",
            encoding="utf-8",
        )
        return str(path)

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
