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
import shutil
import tempfile
import unittest
from pathlib import Path

from core.execution_safety import rollback_effect, verify_effect
from core.policy_engine import PolicyEngine
from core.skill_result import SkillResult
from skills.create_path import CreatePathSkill
from skills.delete_path import DeletePathSkill
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
        }

    def execute(self, intent, parameters=None):
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

        rolled_back = rollback_effect(registry, "CREATE_PATH", create_result.data)

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

        rolled_back = rollback_effect(self.registry, "MOVE_PATH", move_result.data)

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

        rolled_back = rollback_effect(self.registry, "RENAME_PATH", rename_result.data)

        self.assertTrue(rolled_back)
        self.assertTrue(original.exists(), "il rollback doveva ripristinare il nome originale")
        self.assertEqual(original.read_text(), "contenuto")
        self.assertFalse(renamed.exists())


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
        rollback_effect): qui il "registry" esplode per qualunque execute()."""
        class ExplodingRegistry:
            def execute(self, intent, parameters=None):
                raise RuntimeError("boom")

        self.assertFalse(rollback_effect(ExplodingRegistry(), "CREATE_PATH", {"path": "x"}))


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
        self.assertEqual(VERIFIABLE_INTENTS, {"CREATE_PATH", "RENAME_PATH", "MOVE_PATH", "DELETE_PATH"})

    def test_every_verifiable_intent_verifier_is_actually_callable(self):
        from core.execution_safety import INTENT_SAFETY_REGISTRY, VERIFIABLE_INTENTS

        for intent in VERIFIABLE_INTENTS:
            with self.subTest(intent=intent):
                self.assertTrue(callable(INTENT_SAFETY_REGISTRY[intent].verifier))


class IsSafeToAutoRetryTests(unittest.TestCase):
    """F1.3.6 ("impedire retry automatico per azioni non idempotenti senza chiave deduplica"):
    solo READ_ONLY e i quattro intent filesystem di INTENT_SAFETY_REGISTRY (naturalmente
    idempotenti - vedi il docstring di is_safe_to_auto_retry) sono sicuri da ritentare alla
    cieca. Verificato con intent reali del catalogo, non finti, cosi' una riclassificazione
    futura di risk.py che cambiasse la categoria di uno di questi intent farebbe fallire il
    test invece di lasciarlo silenziosamente disallineato."""

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

        self.assertFalse(is_safe_to_auto_retry("EMPTY_RECYCLE_BIN"))


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
