"""Test unitari per la rete di sicurezza dell'agente a passi (v3.3, Agent Engine 2.0): retry
automatico sugli errori transitori, verifica indipendente dell'effetto, rollback dopo un errore
fatale. Nessuna vera chiamata a Ollama: un client finto restituisce risposte gia' scritte in
JSON, cosi' come nei test esistenti per il resto della comprensione (vedi tests/test_agentics.py)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from core.agent import TaskAgent
from core.ollama_client import OllamaError
from core.skill_result import SkillResult

CAPABILITIES = {
    "ADD_NOTE": {"intent": "ADD_NOTE", "description": "Aggiunge un appunto.", "parameters": {
        "text": {"type": "string", "required": True, "description": "Testo dell'appunto."},
    }},
    "CREATE_PATH": {"intent": "CREATE_PATH", "description": "Crea un file o una cartella.", "parameters": {
        "path": {"type": "string", "required": True, "description": "Percorso da creare."},
    }},
}


class FakeRegistry:
    """Simula SkillRegistry: CREATE_PATH/DELETE_PATH toccano davvero il filesystem (serve a
    verify_effect/rollback_effect, che controllano lo stato reale su disco), ADD_NOTE pesca da
    una coda di risultati gia' pronti per simulare un fallimento transitorio seguito da successo."""

    def __init__(self, add_note_results: list = None):
        self._add_note_queue = list(add_note_results or [])
        self.calls = []

    def list_capabilities(self):
        return list(CAPABILITIES.values())

    def execute(self, intent, parameters=None):
        parameters = parameters or {}
        self.calls.append((intent, dict(parameters)))
        if intent == "CREATE_PATH":
            Path(parameters["path"]).touch()
            return SkillResult(success=True, data={"path": parameters["path"]})
        if intent == "DELETE_PATH":
            Path(parameters["path"]).unlink(missing_ok=True)
            return SkillResult(success=True, data={"path": parameters["path"]})
        if intent == "ADD_NOTE":
            return self._add_note_queue.pop(0)
        raise AssertionError(f"intent non atteso nel test: {intent}")


class FakeRetriever:
    def __init__(self, intents: list[str]):
        self.intents = intents

    def retrieve(self, request, max_capabilities=22, max_examples=0):
        class _Result:
            pass
        result = _Result()
        result.capabilities = [{"intent": intent} for intent in self.intents]
        return result


class ScriptedOllamaClient:
    """Ogni voce di `turns` e' o un dict payload (diventa la risposta JSON del modello) o
    un'eccezione (viene sollevata, per simulare un errore del modello/rete)."""

    def __init__(self, turns: list):
        self.turns = list(turns)
        self.calls = 0

    def chat(self, model, messages, format=None, options=None, timeout=None):
        self.calls += 1
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return {"message": {"content": json.dumps(turn, ensure_ascii=False)}}


def _agent(registry, client, executor=None) -> TaskAgent:
    return TaskAgent(
        registry, FakeRetriever(["ADD_NOTE", "CREATE_PATH"]), client, model_provider=lambda: "fake-model",
        format_result=lambda intent, result: str(result.data), executor=executor,
    )


class RetryOnTransientErrorTests(unittest.TestCase):
    def test_transient_failure_is_retried_and_succeeds(self):
        registry = FakeRegistry(add_note_results=[
            SkillResult(success=False, data={}, error="OPERATION_FAILED"),
            SkillResult(success=True, data={}),
        ])
        client = ScriptedOllamaClient([
            {"thought": "Aggiungo l'appunto", "action": {"intent": "ADD_NOTE", "parameters": {"text": "prova"}},
             "final_answer": "", "ask_user": ""},
            {"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Fatto.", "ask_user": ""},
        ])
        outcome = _agent(registry, client).run("aggiungi un appunto")

        self.assertEqual(len(outcome.steps), 1)
        self.assertEqual(outcome.steps[0].attempts, 2)
        self.assertTrue(outcome.steps[0].result.success)
        self.assertEqual(outcome.final_answer, "Fatto.")

    def test_non_transient_failure_is_not_retried(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=False, data={}, error="MISSING_PARAMETERS")])
        client = ScriptedOllamaClient([
            {"thought": "Aggiungo l'appunto", "action": {"intent": "ADD_NOTE", "parameters": {"text": "prova"}},
             "final_answer": "", "ask_user": ""},
            {"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Non riuscito.", "ask_user": ""},
        ])
        outcome = _agent(registry, client).run("aggiungi un appunto")

        self.assertEqual(outcome.steps[0].attempts, 1)
        self.assertFalse(outcome.steps[0].result.success)


class IndependentVerificationTests(unittest.TestCase):
    def test_success_claim_without_real_effect_is_downgraded_to_verification_failed(self):
        """CREATE_PATH che dichiara successo su un percorso che pero' non esiste davvero: la
        skill finta qui NON tocca il disco (a differenza di FakeRegistry.execute normale),
        simulando una skill che mente sul proprio risultato."""
        class LyingRegistry(FakeRegistry):
            def execute(self, intent, parameters=None):
                self.calls.append((intent, parameters))
                return SkillResult(success=True, data={"path": parameters["path"]})  # non crea nulla davvero

        registry = LyingRegistry()
        missing_path = str(Path(tempfile.gettempdir()) / "jake_test_non_esistente_9827.txt")
        client = ScriptedOllamaClient([
            {"thought": "Creo il file", "action": {"intent": "CREATE_PATH", "parameters": {"path": missing_path}},
             "final_answer": "", "ask_user": ""},
            {"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Fatto.", "ask_user": ""},
        ])
        outcome = _agent(registry, client).run("crea un file")

        self.assertFalse(outcome.steps[0].result.success)
        self.assertEqual(outcome.steps[0].result.error, "VERIFICATION_FAILED")


class RollbackAfterFatalErrorTests(unittest.TestCase):
    def test_completed_reversible_step_is_undone_after_a_model_error(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_agent_rollback_"))
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        target = tmp_dir / "nuovo_file.txt"

        registry = FakeRegistry()
        client = ScriptedOllamaClient([
            {"thought": "Creo il file", "action": {"intent": "CREATE_PATH", "parameters": {"path": str(target)}},
             "final_answer": "", "ask_user": ""},
            OllamaError("il modello non risponde"),
        ])
        outcome = _agent(registry, client).run("crea un file e poi fai qualcos'altro di rischioso")

        # Il rollback avviene dentro run(), quindi a questo punto e' gia' concluso: la prova che
        # il passo sia davvero riuscito PRIMA di essere annullato e' che CREATE_PATH compare tra
        # le chiamate fatte al registry (altrimenti target non sarebbe mai esistito).
        self.assertIn(("CREATE_PATH", {"path": str(target)}), registry.calls)
        self.assertEqual(outcome.error, "MODEL_ERROR: OllamaError")
        self.assertEqual(len(outcome.rolled_back), 1)
        self.assertFalse(target.exists(), "il rollback doveva cancellare il file creato dal passo riuscito")
        self.assertIn("annullato", outcome.final_answer.lower())

    def test_no_rollback_when_the_run_finishes_normally(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_agent_no_rollback_"))
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        target = tmp_dir / "nuovo_file.txt"

        registry = FakeRegistry()
        client = ScriptedOllamaClient([
            {"thought": "Creo il file", "action": {"intent": "CREATE_PATH", "parameters": {"path": str(target)}},
             "final_answer": "", "ask_user": ""},
            {"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Fatto.", "ask_user": ""},
        ])
        outcome = _agent(registry, client).run("crea un file")

        self.assertTrue(target.exists())
        self.assertEqual(outcome.rolled_back, [])
        self.assertIsNone(outcome.error)


class AuthRequiredPropagationTests(unittest.TestCase):
    """v5.4/5.5: un passo che torna AUTH_REQUIRED (vedi JakeCore._resolve_and_execute) deve
    fermare l'agente e riportare il tipo giusto in pending_confirmation, non essere trattato
    come un fallimento qualsiasi ne' confuso con una semplice conferma si'/no."""

    def test_auth_required_step_sets_pending_confirmation_with_the_right_kind(self):
        registry = FakeRegistry(add_note_results=[
            SkillResult(
                success=False,
                data={"message": "Serve la passphrase.", "confirm_parameters": {"text": "prova", "authenticated": True}},
                error="AUTH_REQUIRED",
            ),
        ])
        client = ScriptedOllamaClient([
            {"thought": "Aggiungo l'appunto", "action": {"intent": "ADD_NOTE", "parameters": {"text": "prova"}},
             "final_answer": "", "ask_user": ""},
        ])
        outcome = _agent(registry, client).run("aggiungi un appunto rischioso")

        self.assertIsNotNone(outcome.pending_confirmation)
        self.assertEqual(outcome.pending_confirmation["kind"], "AUTH_REQUIRED")
        self.assertEqual(outcome.pending_confirmation["message"], "Serve la passphrase.")


class SpecializedAgentConfigurationTests(unittest.TestCase):
    """v5.0/5.1: un agente 'di dominio' (es. CodingAgent) e' lo stesso TaskAgent con
    fixed_tools/persona_line impostati, non una classe diversa (vedi core/orchestrator.py)."""

    def test_fixed_tools_bypasses_the_retriever_entirely(self):
        registry = FakeRegistry()
        retriever_calls = []

        class ExplodingRetriever:
            def retrieve(self, *args, **kwargs):
                retriever_calls.append(1)
                raise AssertionError("il recupero semantico non deve essere chiamato con fixed_tools impostato")

        agent = TaskAgent(
            registry, ExplodingRetriever(), ScriptedOllamaClient([]), model_provider=lambda: "fake-model",
            format_result=lambda intent, result: str(result.data), fixed_tools=["CREATE_PATH"],
        )

        tools = agent._tools("qualsiasi richiesta")

        self.assertEqual(retriever_calls, [])
        self.assertEqual([t["intent"] for t in tools], ["CREATE_PATH"])

    def test_fixed_tools_ignores_intents_not_in_the_registry(self):
        registry = FakeRegistry()
        agent = TaskAgent(
            registry, None, ScriptedOllamaClient([]), model_provider=lambda: "fake-model",
            format_result=lambda intent, result: str(result.data), fixed_tools=["CREATE_PATH", "NON_ESISTE"],
        )
        tools = agent._tools("qualsiasi richiesta")
        self.assertEqual([t["intent"] for t in tools], ["CREATE_PATH"])

    def test_persona_line_overrides_the_first_prompt_line(self):
        registry = FakeRegistry()
        agent = TaskAgent(
            registry, None, ScriptedOllamaClient([]), model_provider=lambda: "fake-model",
            format_result=lambda intent, result: str(result.data), fixed_tools=["CREATE_PATH"],
            persona_line="Sei Jake, un agente di sviluppo.",
        )
        prompt = agent._system_prompt(agent._tools("qualsiasi richiesta"))
        self.assertTrue(prompt.startswith("Sei Jake, un agente di sviluppo."))

    def test_default_persona_line_is_unchanged_without_a_domain(self):
        registry = FakeRegistry()
        agent = _agent(registry, ScriptedOllamaClient([]))
        prompt = agent._system_prompt(agent._tools("qualsiasi richiesta"))
        self.assertTrue(prompt.startswith("Sei Jake, un agente che controlla un PC Windows"))


if __name__ == "__main__":
    unittest.main()
