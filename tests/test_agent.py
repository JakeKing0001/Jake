"""Test unitari per la rete di sicurezza dell'agente a passi (v3.3, Agent Engine 2.0): retry
automatico sugli errori transitori, verifica indipendente dell'effetto, rollback dopo un errore
fatale. Nessuna vera chiamata a Ollama: un client finto restituisce risposte gia' scritte in
JSON, cosi' come nei test esistenti per il resto della comprensione (vedi tests/test_agentics.py)."""
import json
import shutil
import tempfile
import unittest
import unittest.mock
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


class StructuredLoggingTests(unittest.TestCase):
    """F0: ogni passo dell'agente scrive un record in jake_actions.jsonl (core/logger.log_
    action), condividendo un solo trace_id per tutta la run - vedi anche tests/test_logger.py
    per il formato del record. Qui si controlla solo CHE venga chiamato con i valori giusti,
    non il file JSONL scritto davvero (gia' coperto da test_logger.py)."""

    def test_verifiable_intent_records_verified_true_on_real_success(self):
        registry = FakeRegistry()
        target = str(Path(tempfile.gettempdir()) / "jake_test_structured_log_9911.txt")
        self.addCleanup(lambda: Path(target).unlink(missing_ok=True))
        client = ScriptedOllamaClient([
            {"thought": "Creo il file", "action": {"intent": "CREATE_PATH", "parameters": {"path": target}},
             "final_answer": "", "ask_user": ""},
            {"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Fatto.", "ask_user": ""},
        ])
        with unittest.mock.patch("core.agent.log_action") as mock_log:
            _agent(registry, client).run("crea un file", trace_id="trace-1")

        mock_log.assert_called_once()
        _, kwargs = mock_log.call_args
        self.assertEqual(kwargs["skill"], "CREATE_PATH")
        self.assertEqual(kwargs["verified"], True)
        self.assertEqual(kwargs["result"], "success")
        self.assertEqual(mock_log.call_args.args[0], "trace-1")

    def test_verifiable_intent_records_verified_false_when_effect_not_confirmed(self):
        class LyingRegistry(FakeRegistry):
            def execute(self, intent, parameters=None):
                self.calls.append((intent, parameters))
                return SkillResult(success=True, data={"path": parameters["path"]})

        registry = LyingRegistry()
        missing_path = str(Path(tempfile.gettempdir()) / "jake_test_non_esistente_5541.txt")
        client = ScriptedOllamaClient([
            {"thought": "Creo il file", "action": {"intent": "CREATE_PATH", "parameters": {"path": missing_path}},
             "final_answer": "", "ask_user": ""},
            {"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Fatto.", "ask_user": ""},
        ])
        with unittest.mock.patch("core.agent.log_action") as mock_log:
            _agent(registry, client).run("crea un file")

        _, kwargs = mock_log.call_args
        self.assertEqual(kwargs["verified"], False)

    def test_non_verifiable_intent_leaves_verified_absent_instead_of_a_fabricated_true(self):
        """ADD_NOTE non ha un controllo indipendente (vedi execution_safety.verify_effect):
        anche se ha successo, verified deve restare None invece di ereditare il default
        'nessuna verifica disponibile' di verify_effect come se fosse una prova vera."""
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        client = ScriptedOllamaClient([
            {"thought": "Aggiungo l'appunto", "action": {"intent": "ADD_NOTE", "parameters": {"text": "prova"}},
             "final_answer": "", "ask_user": ""},
            {"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Fatto.", "ask_user": ""},
        ])
        with unittest.mock.patch("core.agent.log_action") as mock_log:
            _agent(registry, client).run("aggiungi un appunto")

        _, kwargs = mock_log.call_args
        self.assertIsNone(kwargs["verified"])

    def test_private_flag_is_forwarded_to_every_step(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        client = ScriptedOllamaClient([
            {"thought": "Aggiungo l'appunto", "action": {"intent": "ADD_NOTE", "parameters": {"text": "prova"}},
             "final_answer": "", "ask_user": ""},
            {"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Fatto.", "ask_user": ""},
        ])
        with unittest.mock.patch("core.agent.log_action") as mock_log:
            _agent(registry, client).run("aggiungi un appunto", private=True)

        _, kwargs = mock_log.call_args
        self.assertTrue(kwargs["private"])

    def test_multiple_steps_share_the_same_trace_id(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        target = str(Path(tempfile.gettempdir()) / "jake_test_shared_trace_2231.txt")
        self.addCleanup(lambda: Path(target).unlink(missing_ok=True))
        client = ScriptedOllamaClient([
            {"thought": "Aggiungo l'appunto", "action": {"intent": "ADD_NOTE", "parameters": {"text": "prova"}},
             "final_answer": "", "ask_user": ""},
            {"thought": "Creo il file", "action": {"intent": "CREATE_PATH", "parameters": {"path": target}},
             "final_answer": "", "ask_user": ""},
            {"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Fatto.", "ask_user": ""},
        ])
        with unittest.mock.patch("core.agent.log_action") as mock_log:
            _agent(registry, client).run("fai due cose", trace_id="shared-trace")

        self.assertEqual(mock_log.call_count, 2)
        trace_ids_used = {call.args[0] for call in mock_log.call_args_list}
        self.assertEqual(trace_ids_used, {"shared-trace"})


class SessionRecorderWiringTests(unittest.TestCase):
    """A differenza di StructuredLoggingTests sopra (che verifica log_action), qui si verifica
    che un passo fallito raggiunga davvero session_recorder.record_failure (F0, core/session_
    recorder.py) - il pezzo che rende possibile tools/replay_session.py."""

    def test_failed_step_calls_record_failure_with_the_real_parameters(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=False, data={}, error="MISSING_PARAMETERS")])
        client = ScriptedOllamaClient([
            {"thought": "Aggiungo l'appunto", "action": {"intent": "ADD_NOTE", "parameters": {"text": "prova"}},
             "final_answer": "", "ask_user": ""},
            {"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Non riuscito.", "ask_user": ""},
        ])
        recorder = unittest.mock.Mock()
        agent = _agent(registry, client)
        agent.session_recorder = recorder

        # log_action mascherato: non e' quello sotto test qui (vedi StructuredLoggingTests) e,
        # se non mascherato, scriverebbe davvero su data/jake_actions.jsonl del contributore.
        with unittest.mock.patch("core.agent.log_action"):
            agent.run("aggiungi un appunto", trace_id="trace-fail")

        recorder.record_failure.assert_called_once()
        args, kwargs = recorder.record_failure.call_args
        self.assertEqual(args[0], "trace-fail")
        self.assertEqual(kwargs["intent"], "ADD_NOTE")
        self.assertEqual(kwargs["parameters"], {"text": "prova"})
        self.assertEqual(kwargs["error"], "error:MISSING_PARAMETERS")

    def test_successful_step_does_not_call_record_failure(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        client = ScriptedOllamaClient([
            {"thought": "Aggiungo l'appunto", "action": {"intent": "ADD_NOTE", "parameters": {"text": "prova"}},
             "final_answer": "", "ask_user": ""},
            {"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Fatto.", "ask_user": ""},
        ])
        recorder = unittest.mock.Mock()
        agent = _agent(registry, client)
        agent.session_recorder = recorder

        with unittest.mock.patch("core.agent.log_action"):
            agent.run("aggiungi un appunto")

        recorder.record_failure.assert_not_called()


class ActionLedgerWiringTests(unittest.TestCase):
    """F1 (Trustworthy Agent Core 3.0, vedi core/action_ledger.py): ogni passo produce una
    ricevuta nel ledger, con requested_by che identifica QUESTO agente ("agent:<agent_name>",
    non un generico "agent")."""

    def test_step_records_a_receipt_with_agent_requested_by(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        client = ScriptedOllamaClient([
            {"thought": "Aggiungo l'appunto", "action": {"intent": "ADD_NOTE", "parameters": {"text": "prova"}},
             "final_answer": "", "ask_user": ""},
            {"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Fatto.", "ask_user": ""},
        ])
        ledger = unittest.mock.Mock()
        agent = _agent(registry, client)
        agent.action_ledger = ledger
        agent.agent_name = "coding"

        with unittest.mock.patch("core.agent.log_action"):
            agent.run("aggiungi un appunto", trace_id="trace-ledger")

        ledger.record.assert_called_once()
        (receipt,), kwargs = ledger.record.call_args
        self.assertEqual(receipt.trace_id, "trace-ledger")
        self.assertEqual(receipt.requested_by, "agent:coding")
        self.assertEqual(receipt.intent, "ADD_NOTE")
        self.assertEqual(receipt.authorization, "none")
        self.assertTrue(receipt.action_id)
        self.assertFalse(kwargs["private"])

    def test_private_run_passes_private_true_to_the_ledger(self):
        registry = FakeRegistry(add_note_results=[SkillResult(success=True, data={})])
        client = ScriptedOllamaClient([
            {"thought": "Aggiungo l'appunto", "action": {"intent": "ADD_NOTE", "parameters": {"text": "prova"}},
             "final_answer": "", "ask_user": ""},
            {"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Fatto.", "ask_user": ""},
        ])
        ledger = unittest.mock.Mock()
        agent = _agent(registry, client)
        agent.action_ledger = ledger

        with unittest.mock.patch("core.agent.log_action"):
            agent.run("aggiungi un appunto", private=True)

        self.assertTrue(ledger.record.call_args.kwargs["private"])


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
