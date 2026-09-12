"""Test unitari per il replay anonimizzato/deterministico delle sessioni fallite (F0, vedi
core/session_recorder.py). Diverso da tests/test_logger.py: qui si controlla la redazione dei
parametri e i due interruttori (enabled/verbatim), non lo schema del record in se'."""
import json
import logging
import tempfile
import unittest
from pathlib import Path

from core.session_recorder import SessionRecorder, redact_value


class RedactTests(unittest.TestCase):
    def test_generic_string_becomes_a_length_placeholder(self):
        text = "questo e' un appunto qualsiasi, non un percorso ne' un url"
        self.assertEqual(redact_value(text), f"<str:{len(text)} caratteri>")

    def test_non_string_scalars_pass_through(self):
        self.assertEqual(redact_value(True), True)
        self.assertEqual(redact_value(42), 42)
        self.assertIsNone(redact_value(None))

    def test_nested_dict_and_list_are_redacted_recursively(self):
        redacted = redact_value({"note": "un appunto personale", "tags": ["personale", "urgente"], "confirmed": True})
        self.assertEqual(redacted, {
            "note": "<str:20 caratteri>", "tags": ["<str:9 caratteri>", "<str:7 caratteri>"], "confirmed": True,
        })


class StructuredRedactionByTypeTests(unittest.TestCase):
    """F1.7.4 ("redazione strutturata per tipo di dato, non solo lunghezza stringa"): un
    percorso, un URL o un email diventavano tutti lo stesso "<str:N caratteri>" - abbastanza per
    contare i caratteri, non per capire la FORMA del fallimento (es. "capita solo con i PDF")
    senza riaprire il file verbatim."""

    def test_windows_absolute_path_with_extension_shows_the_extension(self):
        value = "C:\\Users\\david\\Desktop\\tesi_finale.pdf"
        self.assertEqual(redact_value(value), f"<path:{len(value)} caratteri, estensione=.pdf>")

    def test_windows_path_without_an_extension_shows_no_extension(self):
        value = "C:\\Users\\david\\Desktop"
        self.assertEqual(redact_value(value), f"<path:{len(value)} caratteri>")

    def test_unc_path_is_recognized(self):
        value = "\\\\server\\condivisa\\report.docx"
        self.assertEqual(redact_value(value), f"<path:{len(value)} caratteri, estensione=.docx>")

    def test_forward_slash_path_with_extension_is_recognized(self):
        value = "documenti/tesi.pdf"
        self.assertEqual(redact_value(value), f"<path:{len(value)} caratteri, estensione=.pdf>")

    def test_a_bare_date_like_fraction_is_not_misclassified_as_a_path(self):
        """"/" da solo e' troppo ambiguo (date, frazioni) - senza un'estensione file
        riconoscibile alla fine, deve restare il segnaposto generico."""
        value = "10/09/2026"
        self.assertEqual(redact_value(value), f"<str:{len(value)} caratteri>")

    def test_url_shows_only_the_domain_not_the_path_or_query_string(self):
        value = "https://example.com/reset-password?token=segreto123"
        self.assertEqual(redact_value(value), f"<url:{len(value)} caratteri, dominio=example.com>")

    def test_www_url_without_a_scheme_is_recognized(self):
        value = "www.example.com/pagina"
        self.assertEqual(redact_value(value), f"<url:{len(value)} caratteri, dominio=example.com>")

    def test_email_shows_only_the_domain_not_the_local_part(self):
        value = "mario.rossi@example.com"
        self.assertEqual(redact_value(value), f"<email:{len(value)} caratteri, dominio=example.com>")

    def test_a_string_with_an_at_sign_that_is_not_a_whole_email_is_not_misclassified(self):
        value = "aspetta @qualcuno per favore"
        self.assertEqual(redact_value(value), f"<str:{len(value)} caratteri>")

    def test_a_bare_filename_without_a_separator_is_not_classified_as_a_path(self):
        """Coerente con l'uso reale (skills/find_file.py passa spesso solo il nome, non un
        percorso completo): senza un separatore, non c'e' abbastanza segnale per dire che SIA
        un percorso invece di una parola qualsiasi che finisce per coincidenza con un'estensione."""
        value = "tesi_finale.pdf"
        self.assertEqual(redact_value(value), f"<str:{len(value)} caratteri>")


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
