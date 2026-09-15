"""Test unitari per core/execution_safety.py: rollback_effect/verify_effect (F1, Trustworthy
Agent Core 3.0 - il criterio di uscita "restore verificato" in ROADMAP.md, fase F1).

Usa le skill VERE (skills/create_path.py, skills/rename_path.py, skills/move_path.py,
skills/delete_path.py) su un filesystem reale in una cartella temporanea, non una loro
reimplementazione: solo cosi' un rollback che sembra corretto leggendo il codice ma si comporta
diversamente con la skill vera (una chiave del dizionario data scritta in modo leggermente
diverso, un parametro mancante...) verrebbe scoperto. Prima di questi test, MOVE_PATH e
RENAME_PATH non avevano MAI un test end-to-end per il proprio rollback
(core/execution_safety.py, INTENT_SAFETY_REGISTRY): solo CREATE_PATH era verificato, e solo
passando dall'agente intero (tests/test_agent.py::RollbackAfterFatalErrorTests)."""
import ctypes
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from core.action_ledger import ActionLedger
from core.execution_safety import rollback_effect, verify_effect
from core.policy_engine import PolicyEngine
from core.skill_result import SkillResult
from skills.create_path import CreatePathSkill
from skills.delete_path import DeletePathSkill
from skills.file_utils import ExtractArchiveSkill
from skills.move_path import MovePathSkill
from skills.rename_path import RenamePathSkill


class RealSkillRegistry:
    """Registro minimale che esegue le skill filesystem VERE, non una loro simulazione: vedi
    la nota in cima al modulo sul perche' non basta reimplementare "sposta"/"rinomina" a mano."""

    def __init__(self):
        self._skills = {
            "CREATE_PATH": CreatePathSkill(),
            "DELETE_PATH": DeletePathSkill(),
            "MOVE_PATH": MovePathSkill(),
            "RENAME_PATH": RenamePathSkill(),
            "EXTRACT_ARCHIVE": ExtractArchiveSkill(),
        }

    def execute(self, intent, parameters=None, policy_engine=None):
        return self._skills[intent].execute(parameters or {})


class RollbackCreatePathTests(unittest.TestCase):
    """CREATE_PATH e' gia' verificato end-to-end passando dall'agente intero
    (tests/test_agent.py): qui lo si copre anche in isolamento, come per MOVE_PATH/RENAME_PATH
    sotto, cosi' i tre handler in INTENT_SAFETY_REGISTRY hanno la stessa profondita' di test
    invece di lasciarne due scoperti."""

    def test_rollback_deletes_the_created_file(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_execution_safety_"))
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        registry = RealSkillRegistry()
        target = tmp_dir / "nuovo.txt"

        create_result = registry.execute("CREATE_PATH", {"path": str(target)})
        self.assertTrue(create_result.success)
        self.assertTrue(target.exists())
        self.assertTrue(verify_effect("CREATE_PATH", create_result.data))

        rolled_back = rollback_effect(registry, "CREATE_PATH", create_result.data, policy_engine=PolicyEngine())

        self.assertTrue(rolled_back)
        self.assertFalse(target.exists())


class RollbackMovePathTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_execution_safety_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.registry = RealSkillRegistry()

    def test_rollback_moves_the_file_back_to_its_original_directory(self):
        original_dir = self.tmp_dir / "origine"
        destination_dir = self.tmp_dir / "destinazione"
        original_dir.mkdir()
        destination_dir.mkdir()
        source = original_dir / "documento.txt"
        source.write_text("contenuto")

        move_result = self.registry.execute("MOVE_PATH", {
            "path": str(source), "destination": str(destination_dir), "confirmed": True,
        })
        self.assertTrue(move_result.success)
        moved = Path(move_result.data["new_path"])
        self.assertTrue(moved.exists())
        self.assertFalse(source.exists())
        self.assertTrue(verify_effect("MOVE_PATH", move_result.data))

        rolled_back = rollback_effect(self.registry, "MOVE_PATH", move_result.data, policy_engine=PolicyEngine())

        self.assertTrue(rolled_back)
        self.assertTrue(source.exists(), "il rollback doveva riportare il file nella cartella originale")
        self.assertEqual(source.read_text(), "contenuto")
        self.assertFalse(moved.exists())


class RollbackRenamePathTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_execution_safety_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.registry = RealSkillRegistry()

    def test_rollback_restores_the_original_name(self):
        original = self.tmp_dir / "originale.txt"
        original.write_text("contenuto")

        rename_result = self.registry.execute("RENAME_PATH", {"path": str(original), "new_name": "nuovo_nome.txt"})
        self.assertTrue(rename_result.success)
        renamed = Path(rename_result.data["new_path"])
        self.assertTrue(renamed.exists())
        self.assertFalse(original.exists())
        self.assertTrue(verify_effect("RENAME_PATH", rename_result.data))

        rolled_back = rollback_effect(self.registry, "RENAME_PATH", rename_result.data, policy_engine=PolicyEngine())

        self.assertTrue(rolled_back)
        self.assertTrue(original.exists(), "il rollback doveva ripristinare il nome originale")
        self.assertEqual(original.read_text(), "contenuto")
        self.assertFalse(renamed.exists())


class RollbackExtractArchiveTests(unittest.TestCase):
    """F1 (Gate G1, secondo criterio): EXTRACT_ARCHIVE aggiunto a INTENT_SAFETY_REGISTRY -
    verificatore che controlla la cartella di destinazione popolata per davvero (non solo
    esistente: potrebbe gia' esserci vuota da prima per un altro motivo), rollback che riusa
    DELETE_PATH (gia' verificato sopra) per cancellare cio' che l'estrazione ha creato."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_execution_safety_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.registry = RealSkillRegistry()

    def _make_zip(self) -> Path:
        archive_source_dir = self.tmp_dir / "da_comprimere"
        archive_source_dir.mkdir()
        (archive_source_dir / "dentro.txt").write_text("contenuto")
        archive_path = shutil.make_archive(str(self.tmp_dir / "archivio"), "zip", str(archive_source_dir))
        return Path(archive_path)

    def test_rollback_deletes_the_extracted_destination_folder(self):
        archive_path = self._make_zip()

        extract_result = self.registry.execute("EXTRACT_ARCHIVE", {"path": str(archive_path)})
        self.assertTrue(extract_result.success)
        destination = Path(extract_result.data["destination"])
        self.assertTrue((destination / "dentro.txt").exists())
        self.assertTrue(verify_effect("EXTRACT_ARCHIVE", extract_result.data))

        rolled_back = rollback_effect(self.registry, "EXTRACT_ARCHIVE", extract_result.data, policy_engine=PolicyEngine())

        self.assertTrue(rolled_back)
        self.assertFalse(destination.exists())

    def test_verify_effect_is_false_if_the_destination_was_never_populated(self):
        """Riproduce il caso che un semplice is_dir() lascerebbe passare per errore: una
        cartella di destinazione gia' presente ma vuota (creata per un altro motivo, non
        dall'estrazione) non deve contare come prova che l'estrazione sia avvenuta."""
        empty_destination = self.tmp_dir / "gia_vuota"
        empty_destination.mkdir()

        self.assertFalse(verify_effect("EXTRACT_ARCHIVE", {"destination": str(empty_destination)}))


class VerifyCreatedSkillFileTests(unittest.TestCase):
    """F1 (Gate G1, secondo criterio): CREATE_SKILL/DELETE_CREATED_SKILL aggiunti a
    INTENT_SAFETY_REGISTRY - stesso verificatore filesystem di CREATE_PATH/DELETE_PATH, senza
    passare dalla vera fucina (core/skill_forge.py, gia' ampiamente testata per conto suo):
    verify_effect() e' una funzione pura sui dati, non serve altro per provarla."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_execution_safety_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_create_skill_is_verified_when_the_plugin_file_exists(self):
        plugin_path = self.tmp_dir / "learned_x.py"
        plugin_path.write_text("# skill generata")
        self.assertTrue(verify_effect("CREATE_SKILL", {"path": str(plugin_path)}))

    def test_create_skill_is_not_verified_if_the_file_is_missing(self):
        self.assertFalse(verify_effect("CREATE_SKILL", {"path": str(self.tmp_dir / "mai_scritto.py")}))

    def test_delete_created_skill_is_verified_when_the_plugin_file_is_gone(self):
        plugin_path = self.tmp_dir / "learned_x.py"
        self.assertFalse(plugin_path.exists())
        self.assertTrue(verify_effect("DELETE_CREATED_SKILL", {"path": str(plugin_path)}))

    def test_delete_created_skill_is_not_verified_if_the_file_is_still_there(self):
        plugin_path = self.tmp_dir / "learned_x.py"
        plugin_path.write_text("# non cancellata per davvero")
        self.assertFalse(verify_effect("DELETE_CREATED_SKILL", {"path": str(plugin_path)}))

    def test_neither_intent_has_a_rollback(self):
        from core.execution_safety import INTENT_SAFETY_REGISTRY

        self.assertIsNone(INTENT_SAFETY_REGISTRY["CREATE_SKILL"].rollback)
        self.assertIsNone(INTENT_SAFETY_REGISTRY["DELETE_CREATED_SKILL"].rollback)


class RollbackRespectsBlockedIntentsTests(unittest.TestCase):
    """F1.2.5: un rollback e' la compensazione di un effetto gia' approvato, ma l'intent che
    esegue davvero (RollbackAction.compensating_intent in core/execution_safety.py) resta
    soggetto a blocked_intents - un DELETE_PATH disabilitato in config.json non deve eseguire
    nemmeno come "annullamento" di un CREATE_PATH."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_execution_safety_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.registry = RealSkillRegistry()

    def test_rollback_is_refused_when_the_compensating_intent_is_blocked(self):
        target = self.tmp_dir / "nuovo.txt"
        create_result = self.registry.execute("CREATE_PATH", {"path": str(target)})
        self.assertTrue(create_result.success)
        policy_engine = PolicyEngine(blocked_intents={"DELETE_PATH"})

        rolled_back = rollback_effect(self.registry, "CREATE_PATH", create_result.data, policy_engine=policy_engine)

        self.assertFalse(rolled_back)
        self.assertTrue(target.exists(), "il file non doveva essere cancellato: DELETE_PATH e' bloccato")

    def test_rollback_still_happens_when_a_different_intent_is_blocked(self):
        target = self.tmp_dir / "nuovo.txt"
        create_result = self.registry.execute("CREATE_PATH", {"path": str(target)})
        self.assertTrue(create_result.success)
        policy_engine = PolicyEngine(blocked_intents={"OPEN_PATH"})

        rolled_back = rollback_effect(self.registry, "CREATE_PATH", create_result.data, policy_engine=policy_engine)

        self.assertTrue(rolled_back)
        self.assertFalse(target.exists())


class RollbackEdgeCaseTests(unittest.TestCase):
    def test_intent_without_a_rollback_handler_returns_false(self):
        self.assertFalse(rollback_effect(RealSkillRegistry(), "OPEN_PATH", {"path": "qualsiasi"}))

    def test_rollback_swallows_errors_and_returns_false(self):
        """Un rollback fallito non deve mai far crashare il chiamante (vedi il docstring di
        rollback_effect): qui il "registry" esplode per qualunque execute(). policy_engine
        passato esplicitamente: altrimenti il fail-closed su policy_engine=None (vedi sotto)
        farebbe tornare False PRIMA di arrivare a chiamare il registry, senza davvero verificare
        che l'eccezione venga inghiottita."""
        class ExplodingRegistry:
            def execute(self, intent, parameters=None, policy_engine=None):
                raise RuntimeError("boom")

        self.assertFalse(rollback_effect(ExplodingRegistry(), "CREATE_PATH", {"path": "x"}, policy_engine=PolicyEngine()))

    def test_policy_engine_none_is_fail_closed_not_no_restriction(self):
        """F1.2.1 (percorso 6): stesso principio "nega per default" gia' applicato a
        PlanExecutor.execute() (JakeCore gia' collega un vero policy_engine ai tre TaskAgent -
        F1.2.5 - quindi in produzione questo non era gia' sfruttabile, ma un TaskAgent costruito
        senza quel collegamento esplicito - un test, uno strumento, un futuro chiamante - non deve
        poter eseguire un rollback senza NESSUN controllo su blocked_intents)."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_execution_safety_"))
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        registry = RealSkillRegistry()
        target = tmp_dir / "nuovo.txt"
        create_result = registry.execute("CREATE_PATH", {"path": str(target)})
        self.assertTrue(create_result.success)

        rolled_back = rollback_effect(registry, "CREATE_PATH", create_result.data)

        self.assertFalse(rolled_back)
        self.assertTrue(target.exists(), "senza un policy_engine il rollback non deve eseguire nulla")


class RollbackReceiptTests(unittest.TestCase):
    """F1.7.2 ("collegare command, sub-step, verifica, undo e notifica con lo stesso trace id"):
    buco reale - un rollback riuscito non produceva MAI una propria ActionReceipt, quindi il
    ledger non mostrava da nessuna parte che un'azione era stata annullata. Usa un ActionLedger
    VERO (file temporaneo reale), non un Mock: la garanzia che conta e' cosa finisce davvero
    scritto su disco, non solo che record() sia stato chiamato."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_execution_safety_rollback_receipt_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.ledger = ActionLedger(path=self.tmp_dir / "ledger.jsonl")
        self.registry = RealSkillRegistry()
        # F1.7.6: rollback_effect() ora chiama anche log_action() (data/jake_actions.jsonl, un
        # logger di libreria standard condiviso per l'intero processo) - senza questo mock questi
        # test scriverebbero davvero sul file di produzione del progetto, stessa precauzione gia'
        # presa ovunque altro in questa suite per gli altri chokepoint.
        patcher = unittest.mock.patch("core.execution_safety.log_action")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_successful_rollback_writes_a_receipt_with_the_same_trace_id(self):
        target = self.tmp_dir / "nuovo.txt"
        create_result = self.registry.execute("CREATE_PATH", {"path": str(target)})
        self.assertTrue(create_result.success)

        rolled_back = rollback_effect(
            self.registry, "CREATE_PATH", create_result.data, policy_engine=PolicyEngine(),
            action_ledger=self.ledger, trace_id="t1", requested_by="agent:general",
        )

        self.assertTrue(rolled_back)
        [receipt] = self.ledger.read_all()
        self.assertEqual(receipt["trace_id"], "t1")
        self.assertEqual(receipt["intent"], "DELETE_PATH", "l'intent compensatorio che ha eseguito davvero")
        self.assertEqual(receipt["requested_by"], "rollback:agent:general")
        self.assertEqual(receipt["result"], "rollback_success")

    def test_no_receipt_when_action_ledger_is_not_passed(self):
        """Comportamento invariato per chi non passa action_ledger/trace_id (default None)."""
        target = self.tmp_dir / "nuovo.txt"
        create_result = self.registry.execute("CREATE_PATH", {"path": str(target)})

        rolled_back = rollback_effect(self.registry, "CREATE_PATH", create_result.data, policy_engine=PolicyEngine())

        self.assertTrue(rolled_back)
        self.assertEqual(self.ledger.read_all(), [])

    def test_no_receipt_when_no_rollback_was_attempted(self):
        """Un rollback bloccato dalla policy non e' un'esecuzione: niente da correlare."""
        target = self.tmp_dir / "nuovo.txt"
        create_result = self.registry.execute("CREATE_PATH", {"path": str(target)})

        rolled_back = rollback_effect(
            self.registry, "CREATE_PATH", create_result.data,
            policy_engine=PolicyEngine(blocked_intents={"DELETE_PATH"}),
            action_ledger=self.ledger, trace_id="t1",
        )

        self.assertFalse(rolled_back)
        self.assertEqual(self.ledger.read_all(), [])

    def test_a_failed_rollback_still_writes_a_receipt(self):
        """Un rollback tentato ma fallito e' comunque un evento degno di una ricevuta - l'errore
        non deve sparire in silenzio."""
        class ExplodingRegistry:
            def execute(self, intent, parameters=None, policy_engine=None):
                raise RuntimeError("boom")

        rolled_back = rollback_effect(
            ExplodingRegistry(), "CREATE_PATH", {"path": "x"}, policy_engine=PolicyEngine(),
            action_ledger=self.ledger, trace_id="t1",
        )

        self.assertFalse(rolled_back)
        [receipt] = self.ledger.read_all()
        self.assertEqual(receipt["result"], "rollback_failed")

    def test_private_mode_writes_no_receipt(self):
        """Stessa garanzia gia' verificata end-to-end per gli altri tre chokepoint (F1.7.8): la
        modalita' privata non lascia traccia nemmeno per un rollback."""
        target = self.tmp_dir / "nuovo.txt"
        create_result = self.registry.execute("CREATE_PATH", {"path": str(target)})

        rollback_effect(
            self.registry, "CREATE_PATH", create_result.data, policy_engine=PolicyEngine(),
            action_ledger=self.ledger, trace_id="t1", private=True,
        )

        self.assertEqual(self.ledger.read_all(), [])

    def test_requested_by_defaults_to_a_plain_rollback_label_when_omitted(self):
        target = self.tmp_dir / "nuovo.txt"
        create_result = self.registry.execute("CREATE_PATH", {"path": str(target)})

        rollback_effect(
            self.registry, "CREATE_PATH", create_result.data, policy_engine=PolicyEngine(),
            action_ledger=self.ledger, trace_id="t1",
        )

        [receipt] = self.ledger.read_all()
        self.assertEqual(receipt["requested_by"], "rollback")


class IntentSafetyRegistryConsistencyTests(unittest.TestCase):
    """F1.3.1: VERIFIABLE_INTENTS e' DERIVATO da INTENT_SAFETY_REGISTRY (frozenset comprehension
    sui verifier non-None), non piu' un insieme mantenuto a mano in parallelo a verify_effect -
    quindi questi due non possono divergere per costruzione, non solo per disciplina. Questo test
    lo dimostra piuttosto che fidarsi solo della lettura del codice: se in futuro qualcuno
    reintroducesse un insieme separato, questo test lo scoprirebbe solo se quell'insieme si
    disallineasse da INTENT_SAFETY_REGISTRY - il che e' esattamente il rischio da coprire."""

    def test_verifiable_intents_matches_the_registry_exactly(self):
        from core.execution_safety import INTENT_SAFETY_REGISTRY, VERIFIABLE_INTENTS

        expected = {intent for intent, entry in INTENT_SAFETY_REGISTRY.items() if entry.verifier is not None}
        self.assertEqual(set(VERIFIABLE_INTENTS), expected)
        self.assertEqual(
            VERIFIABLE_INTENTS,
            {
                "CREATE_PATH", "RENAME_PATH", "MOVE_PATH", "DELETE_PATH", "KILL_PROCESS_BY_PORT", "CLOSE_WINDOW",
                "EXTRACT_ARCHIVE", "CREATE_SKILL", "DELETE_CREATED_SKILL", "RESTART_EXPLORER", "EMPTY_RECYCLE_BIN",
            },
        )

    def test_every_verifiable_intent_verifier_is_actually_callable(self):
        from core.execution_safety import INTENT_SAFETY_REGISTRY, VERIFIABLE_INTENTS

        for intent in VERIFIABLE_INTENTS:
            with self.subTest(intent=intent):
                self.assertTrue(callable(INTENT_SAFETY_REGISTRY[intent].verifier))


class CloseWindowVerificationTests(unittest.TestCase):
    """F1.3.2 ("prove forti per... finestre"): verifica indipendente che una finestra chiusa da
    CloseWindowSkill sia davvero sparita - stesso principio gia' applicato a
    _verify_process_terminated per KILL_PROCESS_BY_PORT, un secondo controllo indipendente dalla
    parola della skill stessa."""

    def test_a_hwnd_that_no_longer_exists_verifies_as_closed(self):
        win32gui = unittest.mock.MagicMock()
        win32gui.IsWindow.return_value = False
        with unittest.mock.patch.dict("sys.modules", {"win32gui": win32gui}):
            self.assertTrue(verify_effect("CLOSE_WINDOW", {"hwnd": 1}))

    def test_a_hwnd_that_still_exists_verifies_as_not_closed(self):
        win32gui = unittest.mock.MagicMock()
        win32gui.IsWindow.return_value = True
        with unittest.mock.patch.dict("sys.modules", {"win32gui": win32gui}):
            self.assertFalse(verify_effect("CLOSE_WINDOW", {"hwnd": 1}))


class _FakeProcess:
    def __init__(self, name):
        self.info = {"name": name}


class RestartExplorerVerificationTests(unittest.TestCase):
    """F1.3.2 (stesso pattern trovato una quarta volta in questa sessione, dopo processi/
    finestre/casa): verifica indipendente che almeno un processo explorer.exe sia davvero in
    esecuzione, invece di fidarsi del successo dichiarato da RestartExplorerSkill - stesso
    principio di CloseWindowVerificationTests sopra. Ignora `data` (sempre {}): non c'e' un PID
    noto in anticipo per RESTART_EXPLORER, a differenza di KILL_PROCESS_BY_PORT."""

    def test_explorer_present_verifies_as_running(self):
        with unittest.mock.patch("psutil.process_iter", return_value=[_FakeProcess("explorer.exe")]):
            self.assertTrue(verify_effect("RESTART_EXPLORER", {}))

    def test_explorer_absent_verifies_as_not_running(self):
        with unittest.mock.patch("psutil.process_iter", return_value=[_FakeProcess("notepad.exe")]):
            self.assertFalse(verify_effect("RESTART_EXPLORER", {}))

    def test_no_processes_at_all_verifies_as_not_running(self):
        with unittest.mock.patch("psutil.process_iter", return_value=[]):
            self.assertFalse(verify_effect("RESTART_EXPLORER", {}))


class EmptyRecycleBinVerificationTests(unittest.TestCase):
    """F1.3.2 (Gate G1, secondo criterio): verifica indipendente, con una seconda chiamata a
    SHQueryRecycleBinW (sola lettura - non SHEmptyRecycleBinW, gia' invocata dalla skill), che
    il cestino non contenga piu' elementi. Diverso dagli altri tre verificatori: SHEmptyRecycleBinW
    e' gia' sincrona per contratto documentato, quindi qui non c'e' un buco "dichiara successo
    senza aspettare" nella skill - solo una seconda prova indipendente in piu'."""

    @staticmethod
    def _fake_query(num_items: int, hresult: int = 0):
        def _query(root_path, info_ptr):
            info_ptr.contents.i64NumItems = num_items
            return hresult
        return _query

    def test_recycle_bin_reported_empty_verifies_as_true(self):
        with unittest.mock.patch.object(
            ctypes.windll.shell32, "SHQueryRecycleBinW", side_effect=self._fake_query(0),
        ):
            self.assertTrue(verify_effect("EMPTY_RECYCLE_BIN", {}))

    def test_recycle_bin_still_containing_items_verifies_as_false(self):
        """Il caso che, senza questo verificatore, resterebbe invisibile: SHEmptyRecycleBinW ha
        dichiarato successo ma - per un motivo qualsiasi - il cestino non e' davvero vuoto."""
        with unittest.mock.patch.object(
            ctypes.windll.shell32, "SHQueryRecycleBinW", side_effect=self._fake_query(3),
        ):
            self.assertFalse(verify_effect("EMPTY_RECYCLE_BIN", {}))

    def test_a_failed_query_verifies_as_false_not_as_an_assumed_success(self):
        """Fail-closed: un HRESULT diverso da S_OK non deve mai contare come "va bene", stesso
        principio "negare per default" gia' applicato altrove in questo modulo."""
        with unittest.mock.patch.object(
            ctypes.windll.shell32, "SHQueryRecycleBinW", side_effect=self._fake_query(0, hresult=-1),
        ):
            self.assertFalse(verify_effect("EMPTY_RECYCLE_BIN", {}))


class IsSafeToAutoRetryTests(unittest.TestCase):
    """F1.3.6 ("impedire retry automatico per azioni non idempotenti senza chiave deduplica"):
    solo READ_ONLY e gli intent di INTENT_SAFETY_REGISTRY (naturalmente idempotenti - vedi il
    docstring di is_safe_to_auto_retry) sono sicuri da ritentare alla cieca. Verificato con
    intent reali del catalogo, non finti, cosi' una riclassificazione futura di risk.py che
    cambiasse la categoria di uno di questi intent farebbe fallire il test invece di lasciarlo
    silenziosamente disallineato."""

    def test_read_only_intent_is_safe_to_retry(self):
        from core.execution_safety import is_safe_to_auto_retry

        self.assertTrue(is_safe_to_auto_retry("GET_TIME"))

    def test_filesystem_intents_in_the_safety_registry_are_safe_to_retry(self):
        from core.execution_safety import INTENT_SAFETY_REGISTRY, is_safe_to_auto_retry

        for intent in INTENT_SAFETY_REGISTRY:
            with self.subTest(intent=intent):
                self.assertTrue(is_safe_to_auto_retry(intent))

    def test_non_idempotent_local_reversible_intent_is_not_safe_to_retry(self):
        from core.execution_safety import is_safe_to_auto_retry

        self.assertFalse(is_safe_to_auto_retry("ADD_NOTE"))

    def test_destructive_intent_outside_the_registry_is_not_safe_to_retry(self):
        from core.execution_safety import is_safe_to_auto_retry

        self.assertFalse(is_safe_to_auto_retry("CLEAR_TEMP_FILES"))


class ExecuteWithRetryRespectsIdempotenceTests(unittest.TestCase):
    """Stessa garanzia di IsSafeToAutoRetryTests, ma osservata sul comportamento reale di
    execute_with_retry (numero di chiamate a execute_fn), non solo sulla funzione di
    classificazione isolata."""

    def test_retry_safe_intent_is_retried_up_to_max_attempts(self):
        from core.execution_safety import MAX_ATTEMPTS, execute_with_retry

        calls = []

        def execute_fn(intent, parameters):
            calls.append(intent)
            if len(calls) < MAX_ATTEMPTS:
                return SkillResult(success=False, data={}, error="OPERATION_FAILED")
            return SkillResult(success=True, data={})

        result, attempts = execute_with_retry(execute_fn, "CREATE_PATH", {"path": "C:/tmp/x.txt"})

        self.assertTrue(result.success)
        self.assertEqual(attempts, MAX_ATTEMPTS)
        self.assertEqual(len(calls), MAX_ATTEMPTS)

    def test_non_idempotent_intent_is_not_retried_even_on_a_transient_error(self):
        from core.execution_safety import execute_with_retry

        calls = []

        def execute_fn(intent, parameters):
            calls.append(intent)
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        result, attempts = execute_with_retry(execute_fn, "ADD_NOTE", {"text": "prova"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")
        self.assertEqual(attempts, 1)
        self.assertEqual(len(calls), 1, "execute_fn non deve essere richiamata una seconda volta")


if __name__ == "__main__":
    unittest.main()
