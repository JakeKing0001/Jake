"""F1.1.8 (Action Contract 2.0, vedi ROADMAP_EXECUTION.md e core/action_ledger.py): contratto
minimo che ogni ActionReceipt deve rispettare, verificato sia in isolamento
(validate_action_receipt) sia sui 4 punti reali del codice che oggi costruiscono una ricevuta
(JakeCore._log_action_outcome/_log_denied_action, TaskAgent._log_step, PlanExecutor._log_step).
Se in futuro uno di questi punti smettesse di popolare un campo obbligatorio (es. idempotency_key)
- o ne comparisse un quinto che se ne dimenticasse - questo file fallisce, invece di lasciare che
una ricevuta incompleta finisca silenziosamente nel ledger append-only."""
import time
import unittest
import unittest.mock

from core.action_ledger import ACTION_RECEIPT_SCHEMA_VERSION, ActionReceipt, validate_action_receipt
from core.agent import TaskAgent
from core.jake_core import JakeCore
from core.plan_executor import PlanExecutor


def _valid_receipt(**overrides) -> ActionReceipt:
    defaults = {
        "action_id": "a1", "trace_id": "t1", "ts": 123.0, "intent": "OPEN_APP", "requested_by": "user",
        "risk_decision": "local_reversible", "authorization": "none", "result": "success",
        "idempotency_key": "k1",
    }
    defaults.update(overrides)
    return ActionReceipt(**defaults)


class ValidateActionReceiptTests(unittest.TestCase):
    def test_a_well_formed_receipt_passes(self):
        validate_action_receipt(_valid_receipt())

    def test_each_mandatory_string_field_empty_is_rejected(self):
        mandatory_fields = (
            "action_id", "trace_id", "intent", "requested_by", "risk_decision", "authorization",
            "result", "idempotency_key",
        )
        for field in mandatory_fields:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    validate_action_receipt(_valid_receipt(**{field: ""}))

    def test_non_positive_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_action_receipt(_valid_receipt(ts=0))

    def test_unsupported_schema_version_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_action_receipt(_valid_receipt(schema_version=ACTION_RECEIPT_SCHEMA_VERSION + 1))

    def test_default_verified_status_passes(self):
        validate_action_receipt(_valid_receipt())

    def test_unrecognized_verified_status_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_action_receipt(_valid_receipt(verified="yes"))


def _bare_jake_core() -> JakeCore:
    core = JakeCore.__new__(JakeCore)
    core.action_ledger = unittest.mock.Mock()
    core.session_recorder = unittest.mock.Mock()
    core.private_mode = False
    core.model = "test-model"
    return core


class ChokepointsProduceConformingReceiptsTests(unittest.TestCase):
    """Ognuno dei 4 punti che oggi scrivono nel ledger, esercitato direttamente (non l'intera
    pipeline dell'agente/esecutore, gia' coperta altrove) per isolare solo la costruzione della
    ricevuta."""

    def test_jake_core_log_action_outcome(self):
        core = _bare_jake_core()
        with unittest.mock.patch("core.jake_core.log_action"):
            core._log_action_outcome(
                "trace-1", time.monotonic(), "OPEN_APP", {"name": "spotify"}, result="success",
            )
        (receipt,), _ = core.action_ledger.record.call_args
        validate_action_receipt(receipt)

    def test_jake_core_log_denied_action(self):
        core = _bare_jake_core()
        action = {"trace_id": "trace-2", "intent": "DELETE_PATH", "parameters": {"path": "x"}}
        core._log_denied_action(action, result="denied_confirmation")
        (receipt,), _ = core.action_ledger.record.call_args
        validate_action_receipt(receipt)

    def test_task_agent_log_step(self):
        agent = TaskAgent.__new__(TaskAgent)
        agent.agent_name = "coding"
        agent.action_ledger = unittest.mock.Mock()
        agent.session_recorder = unittest.mock.Mock()
        with unittest.mock.patch("core.agent.log_action"):
            agent._log_step(
                "trace-3", False, time.monotonic(), "test-model", "ADD_NOTE", {"text": "x"},
                result="success", verified=True,
            )
        (receipt,), _ = agent.action_ledger.record.call_args
        validate_action_receipt(receipt)

    def test_plan_executor_log_step(self):
        executor = PlanExecutor.__new__(PlanExecutor)
        executor.action_ledger = unittest.mock.Mock()
        executor.session_recorder = unittest.mock.Mock()
        with unittest.mock.patch("core.plan_executor.log_action"):
            executor._log_step(
                "trace-4", False, "test-model", "trigger:buonanotte", time.monotonic(), "ADD_NOTE",
                {"text": "x"}, result="success", verified=True,
            )
        (receipt,), _ = executor.action_ledger.record.call_args
        validate_action_receipt(receipt)


if __name__ == "__main__":
    unittest.main()
