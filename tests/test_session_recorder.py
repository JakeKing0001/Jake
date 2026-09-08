"""Test unitari per il replay anonimizzato/deterministico delle sessioni fallite (F0, vedi
core/session_recorder.py). Diverso da tests/test_logger.py: qui si controlla la redazione dei
parametri e i due interruttori (enabled/verbatim), non lo schema del record in se'."""
import json
import logging
import tempfile
import unittest
from pathlib import Path

from core.session_recorder import SessionRecorder, _redact


class RedactTests(unittest.TestCase):
    def test_string_becomes_a_length_placeholder(self):
        text = "documenti/tesi.pdf"
        self.assertEqual(_redact(text), f"<str:{len(text)} caratteri>")

    def test_non_string_scalars_pass_through(self):
        self.assertEqual(_redact(True), True)
        self.assertEqual(_redact(42), 42)
        self.assertIsNone(_redact(None))

    def test_nested_dict_and_list_are_redacted_recursively(self):
        redacted = _redact({"path": "c:/nota.txt", "tags": ["personale", "urgente"], "confirmed": True})
        self.assertEqual(redacted, {
            "path": "<str:11 caratteri>", "tags": ["<str:9 caratteri>", "<str:7 caratteri>"], "confirmed": True,
        })


class SessionRecorderTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.path = Path(self._tmpdir.name) / "sessions.jsonl"

    def _read_records(self) -> list[dict]:
        # Chiude e rimuove gli handler del logger interno di SessionRecorder prima di leggere:
        # su Windows il file resta bloccato finche' l'handler e' aperto (stesso problema visto
        # in tests/test_logger.py).
        for name in list(logging.Logger.manager.loggerDict):
            if name.startswith("jake.sessions."):
                logger = logging.getLogger(name)
                for handler in list(logger.handlers):
                    handler.close()
                    logger.removeHandler(handler)
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line]


class DisabledByDefaultTests(SessionRecorderTestCase):
    def test_writes_nothing_when_not_enabled(self):
        recorder = SessionRecorder(enabled=False, path=self.path)
        recorder.record_failure("t1", intent="FIND_FILE", parameters={"name": "tesi.pdf"}, error="NOT_FOUND", risk_decision="read_only")
        self.assertFalse(self.path.exists())


class RedactedModeTests(SessionRecorderTestCase):
    def test_enabled_without_verbatim_redacts_string_parameters(self):
        recorder = SessionRecorder(enabled=True, verbatim=False, path=self.path)
        recorder.record_failure(
            "t1", intent="FIND_FILE", parameters={"name": "tesi_finale.pdf"}, error="NOT_FOUND", risk_decision="read_only",
        )
        records = self._read_records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["trace_id"], "t1")
        self.assertEqual(record["intent"], "FIND_FILE")
        self.assertEqual(record["error"], "NOT_FOUND")
        self.assertFalse(record["verbatim"])
        self.assertEqual(record["parameters"], {"name": "<str:15 caratteri>"})


class VerbatimModeTests(SessionRecorderTestCase):
    def test_verbatim_keeps_real_parameter_values(self):
        recorder = SessionRecorder(enabled=True, verbatim=True, path=self.path)
        recorder.record_failure(
            "t1", intent="FIND_FILE", parameters={"name": "tesi_finale.pdf"}, error="NOT_FOUND", risk_decision="read_only",
        )
        record = self._read_records()[0]
        self.assertTrue(record["verbatim"])
        self.assertEqual(record["parameters"], {"name": "tesi_finale.pdf"})

    def test_verbatim_flag_is_ignored_when_not_enabled(self):
        """verbatim=True senza enabled=True non deve ne' scrivere ne' comportarsi come se fosse
        attivo: enabled resta l'interruttore principale (vedi SessionRecorder.__init__)."""
        recorder = SessionRecorder(enabled=False, verbatim=True, path=self.path)
        self.assertFalse(recorder.verbatim)
        recorder.record_failure("t1", intent="FIND_FILE", parameters={"name": "x"}, error="NOT_FOUND", risk_decision="read_only")
        self.assertFalse(self.path.exists())


class PrivateModeTests(SessionRecorderTestCase):
    def test_private_flag_suppresses_recording_even_when_enabled(self):
        recorder = SessionRecorder(enabled=True, verbatim=True, path=self.path)
        recorder.record_failure(
            "t1", intent="FIND_FILE", parameters={"name": "x"}, error="NOT_FOUND", risk_decision="read_only", private=True,
        )
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
