"""Test unitari per il pilota di adozione del contratto azione (F1.1.6): un intent reale per
ciascun RiskLevel (read-only, reversibile, external, destructive, admin), passato per davvero da
JakeCore._resolve_and_execute/_log_action_outcome/_log_denied_action - non un fake isolato - per
dimostrare che ActionProposal/ActionError (F1.1.2) funzionano lungo l'intero spettro di rischio,
non solo per un caso facile. Stesso approccio minimale di tests/test_jake_core_permissions.py
(JakeCore.__new__ invece di __init__, per non istanziare l'intero registro di skill/Ollama/NEST)."""
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

from core.action_contracts import ActionProposal
from core.action_ledger import ActionLedger
from core.auth_gate import AuthGate
from core.command import Command
from core.jake_core import JakeCore
from core.policy_engine import PolicyEngine
from core.risk import RiskLevel, risk_of
from core.session_recorder import SessionRecorder
from core.skill_result import SkillResult

# Un intent reale del catalogo per ciascun livello di rischio (F1.1.6, elenco letterale dalla
# roadmap: "un intent read-only, uno reversibile, uno external, uno destructive e uno admin").
PILOT_INTENTS = {
    RiskLevel.READ_ONLY: "GET_TIME",
    RiskLevel.LOCAL_REVERSIBLE: "ADD_NOTE",
    RiskLevel.EXTERNAL_ACTION: "CONTROL_SMART_DEVICE",
    RiskLevel.DESTRUCTIVE: "DELETE_PATH",
    RiskLevel.ADMIN: "SYSTEM_POWER",
}


class FakeSkill:
    metadata = {"intent": "FAKE", "description": "Una skill finta.", "parameters": {}}

    def __init__(self, result=None):
        self.result = result if result is not None else SkillResult(success=True, data={})
        self.calls = []

    def execute(self, parameters=None):
        self.calls.append(parameters)
        return self.result


class FakeRegistry:
    def __init__(self, skills: dict):
        self._skills = skills

    def get_skill(self, intent):
        return self._skills.get(intent)

    def has_skill(self, intent):
        return intent in self._skills

    def execute(self, intent, parameters=None, policy_engine=None):
        skill = self.get_skill(intent)
        return None if skill is None else skill.execute(parameters)


def _bare_core(skill_registry) -> JakeCore:
    core = JakeCore.__new__(JakeCore)
    core.skill_registry = skill_registry
    core.auth_gate = AuthGate()
    core.policy_engine = PolicyEngine(auth_gate=core.auth_gate)
    return core


class PilotIntentsHaveTheExpectedRiskLevelTests(unittest.TestCase):
    def test_pilot_intents_actually_have_the_expected_risk_level(self):
        """Guardia contro un domani in cui risk.py riclassificasse uno di questi 5 intent: i
        test sotto perderebbero senso senza che nessuno se ne accorga."""
        for risk_level, intent in PILOT_INTENTS.items():
            with self.subTest(intent=intent):
                self.assertEqual(risk_of(intent), risk_level)


class ResolveAndExecuteBuildsAValidProposalTests(unittest.TestCase):
    """F1.1.6: ActionProposal.for_intent()/validate_action_proposal() sono chiamate per davvero
    da JakeCore._resolve_and_execute, non simulate - vedi core/jake_core.py."""

    def test_each_pilot_intent_produces_a_valid_proposal_with_the_matching_risk(self):
        for risk_level, intent in PILOT_INTENTS.items():
            with self.subTest(intent=intent, risk=risk_level.value):
                registry = FakeRegistry({intent: FakeSkill()})
                core = _bare_core(registry)

                with unittest.mock.patch("core.jake_core.validate_action_proposal") as mock_validate:
                    core._resolve_and_execute(Command(intent, {"testo": "prova"}))

                mock_validate.assert_called_once()
                (proposal,), _ = mock_validate.call_args
                self.assertIsInstance(proposal, ActionProposal)
                self.assertEqual(proposal.intent, intent)
                self.assertEqual(proposal.risk, risk_level.value)

    def test_proposal_parameters_do_not_affect_the_parameters_actually_executed(self):
        """proposal.parameters e' una COPIA (vedi ActionProposal.for_intent): mutarla non deve
        toccare i parametri che la skill riceve davvero."""
        skill = FakeSkill()
        registry = FakeRegistry({"ADD_NOTE": skill})
        core = _bare_core(registry)
        from core import jake_core as jake_core_module

        original_validate = jake_core_module.validate_action_proposal

        def _tamper_then_validate(proposal):
            proposal.parameters["tampered"] = True
            return original_validate(proposal)

        with unittest.mock.patch("core.jake_core.validate_action_proposal", side_effect=_tamper_then_validate):
            core._resolve_and_execute(Command("ADD_NOTE", {"text": "originale"}))

        self.assertEqual(skill.calls, [{"text": "originale"}])


class ActionErrorWiredIntoTheLedgerTests(unittest.TestCase):
    """F1.1.6: _log_action_outcome/_log_denied_action costruiscono un ActionError reale (F1.1.2)
    e ne usano la categoria per la ricevuta - stesso valore di prima (error_category_of), ma ora
    attraverso il tipo condiviso. Un ActionLedger vero su file temporaneo, non mockato: verifica
    cosa finisce SU DISCO, non solo cosa la funzione calcola in memoria."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.core = JakeCore.__new__(JakeCore)
        self.core.private_mode = False
        self.core.model = None
        self.core.action_ledger = ActionLedger(path=Path(self._tmpdir.name) / "ledger.jsonl")
        self.core.session_recorder = SessionRecorder()  # disattivato per default: no-op

    def test_successful_outcome_is_categorized_as_success_for_every_pilot_intent(self):
        for _, intent in PILOT_INTENTS.items():
            with self.subTest(intent=intent):
                self.core._log_action_outcome(
                    trace_id=f"t-{intent}", started=time.monotonic(), intent=intent, parameters={}, result="success",
                )

        receipts = self.core.action_ledger.read_all()
        self.assertEqual(len(receipts), len(PILOT_INTENTS))
        self.assertTrue(all(r["error_category"] == "success" for r in receipts))

    def test_transient_failure_is_categorized_as_transient_for_every_pilot_intent(self):
        for _, intent in PILOT_INTENTS.items():
            with self.subTest(intent=intent):
                self.core._log_action_outcome(
                    trace_id=f"t-{intent}", started=time.monotonic(), intent=intent, parameters={},
                    result="error:OPERATION_FAILED",
                )

        receipts = self.core.action_ledger.read_all()
        self.assertTrue(all(r["error_category"] == "transient" for r in receipts))

    def test_denied_confirmation_is_categorized_as_denied_for_every_pilot_intent(self):
        for _, intent in PILOT_INTENTS.items():
            with self.subTest(intent=intent):
                self.core._log_denied_action({"intent": intent, "parameters": {}}, result="denied_confirmation")

        receipts = self.core.action_ledger.read_all()
        self.assertEqual(len(receipts), len(PILOT_INTENTS))
        self.assertTrue(all(r["error_category"] == "denied" for r in receipts))


if __name__ == "__main__":
    unittest.main()
