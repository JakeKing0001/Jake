"""Test unitari per la valutazione dello smoke test (F0, vedi tools/smoke_test.py::evaluate).
Non spawna un vero processo main.py --cli (quello lo fa tools/smoke_test.py stesso, non e' un
test della suite - richiederebbe Ollama/un processo reale): qui si controlla solo che
l'interpretazione dell'output sia corretta, con output finti che imitano quello vero."""
import unittest

from tools.smoke_test import evaluate

REAL_SUCCESSFUL_OUTPUT = (
    "Jake 5.9 avviato (modalita' testo). Scrivi 'esci' per chiudere.\n"
    "Tu > Jake > Attualmente sono le 20:57:08\n"
    "Tu > Jake > Chiusura...\n"
)


class EvaluateTests(unittest.TestCase):
    def test_realistic_successful_run_passes(self):
        ok, report = evaluate(REAL_SUCCESSFUL_OUTPUT, returncode=0, elapsed=1.4)
        self.assertTrue(ok)
        self.assertNotIn("FALLITO", report)

    def test_missing_banner_fails(self):
        stdout = REAL_SUCCESSFUL_OUTPUT.replace("avviato", "")
        ok, report = evaluate(stdout, returncode=0, elapsed=1.0)
        self.assertFalse(ok)
        self.assertIn("banner", report)

    def test_no_response_before_closing_fails(self):
        """Solo un "Jake > " (quello di Chiusura...): il comando non ha mai avuto risposta."""
        stdout = "Jake 5.9 avviato (modalita' testo).\nTu > Jake > Chiusura...\n"
        ok, report = evaluate(stdout, returncode=0, elapsed=1.0)
        self.assertFalse(ok)
        self.assertIn("ha risposto al comando", report)

    def test_nonzero_exit_code_fails_even_with_good_output(self):
        ok, report = evaluate(REAL_SUCCESSFUL_OUTPUT, returncode=1, elapsed=1.0)
        self.assertFalse(ok)
        self.assertIn("exit code 0", report)

    def test_crash_with_traceback_and_no_markers_fails(self):
        stdout = "Traceback (most recent call last):\n  ...\nModuleNotFoundError: no module\n"
        ok, report = evaluate(stdout, returncode=1, elapsed=0.2)
        self.assertFalse(ok)
        self.assertIn("Output completo", report)  # il report include l'output per capire perche'


if __name__ == "__main__":
    unittest.main()
