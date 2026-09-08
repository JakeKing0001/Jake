"""Test unitari per il logging strutturato (F0, vedi core/logger.py): un trace_id per
correlare le righe di un'azione, e un file JSONL separato da jake.log con durata, modello,
skill, decisione di rischio, risultato e prova di verifica - o "non verificato" quando manca."""
import json
import logging
import tempfile
import unittest
from pathlib import Path

from core.logger import get_action_logger, log_action, new_trace_id


class NewTraceIdTests(unittest.TestCase):
    def test_returns_a_non_empty_hex_string(self):
        trace_id = new_trace_id()
        self.assertTrue(trace_id)
        int(trace_id, 16)  # non solleva se e' davvero esadecimale

    def test_two_calls_do_not_collide(self):
        self.assertNotEqual(new_trace_id(), new_trace_id())


class LogActionTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.log_path = Path(self._tmpdir.name) / "actions.jsonl"
        # Un logger indipendente per test, cosi' i vari test non condividono handler (get_
        # action_logger() di default configura "jake.actions" una sola volta per processo).
        self.logger = logging.getLogger(f"test.actions.{id(self)}")
        self.logger.handlers.clear()
        handler = logging.FileHandler(self.log_path, encoding="utf-8")
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        # Windows tiene il file aperto finche' l'handler non e' chiuso esplicitamente: la
        # cleanup di TemporaryDirectory (registrata sopra, gira dopo per LIFO) fallirebbe
        # altrimenti con "file usato da un altro processo" nel cancellare actions.jsonl.
        self.addCleanup(handler.close)

    def _read_records(self) -> list[dict]:
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line]

    def test_writes_the_declared_fields_as_one_json_line(self):
        log_action(
            "abc123",
            duration_ms=12.5,
            model="qwen2.5:7b",
            skill="OPEN_APP",
            risk_decision="local_reversible",
            result="success",
            logger=self.logger,
        )
        records = self._read_records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["trace_id"], "abc123")
        self.assertEqual(record["model"], "qwen2.5:7b")
        self.assertEqual(record["skill"], "OPEN_APP")
        self.assertEqual(record["risk_decision"], "local_reversible")
        self.assertEqual(record["result"], "success")
        self.assertIn("ts", record)

    def test_verified_is_absent_by_default_instead_of_a_fabricated_value(self):
        """verified=None (il default) deve dichiarare esplicitamente "non verificato" restando
        assente dal JSON, non apparire come verified: false (che affermerebbe una verifica
        fallita mai avvenuta)."""
        log_action("abc123", result="success", logger=self.logger)
        record = self._read_records()[0]
        self.assertNotIn("verified", record)

    def test_verified_true_is_recorded_when_the_caller_actually_checked(self):
        log_action("abc123", result="success", verified=True, logger=self.logger)
        record = self._read_records()[0]
        self.assertTrue(record["verified"])

    def test_private_mode_writes_no_trace_id_or_content(self):
        log_action(
            "abc123", private=True, skill="OPEN_APP", result="success", logger=self.logger,
        )
        records = self._read_records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record, {"ts": record["ts"], "private": True})
        self.assertNotIn("trace_id", record)
        self.assertNotIn("skill", record)


class GetActionLoggerTests(unittest.TestCase):
    def test_returns_the_same_configured_logger_on_repeated_calls(self):
        first = get_action_logger()
        second = get_action_logger()
        self.assertIs(first, second)
        self.assertEqual(len(first.handlers), 1)


if __name__ == "__main__":
    unittest.main()
