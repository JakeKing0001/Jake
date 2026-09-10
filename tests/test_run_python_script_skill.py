"""Test unitari per skills/dev_tools.py::RunPythonScriptSkill: nessuna suite esisteva finora per
questa skill (RUN_PYTHON_SCRIPT, ADMIN in core/risk.py, self-confirming come RUN_COMMAND - "uno
script puo' fare altrettanto danno di un comando", vedi il suo stesso docstring). Esegue script
Python veri su file temporanei con l'interprete corrente, non li mocka.

F1: stesso buco reale di skills/run_command.py corretto in questa sessione - uno script che
termina con un'eccezione non catturata (codice di uscita 1) veniva comunque riportato come
success=True."""
import tempfile
import unittest
from pathlib import Path

from skills.dev_tools import RunPythonScriptSkill


class _WithSkill(unittest.TestCase):
    def setUp(self):
        self.skill = RunPythonScriptSkill()
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_run_python_script_test_"))

    def _write_script(self, name: str, content: str) -> str:
        path = self.tmp_dir / name
        path.write_text(content, encoding="utf-8")
        return str(path)


class MissingParametersAndPathTests(_WithSkill):
    def test_empty_path_fails(self):
        self.assertEqual(self.skill.execute({"path": ""}).error, "MISSING_PARAMETERS")

    def test_nonexistent_path_fails(self):
        result = self.skill.execute({"path": str(self.tmp_dir / "non_esiste.py")})
        self.assertEqual(result.error, "PATH_NOT_FOUND")


class ConfirmationFlowTests(_WithSkill):
    def test_without_confirmation_asks_for_it_and_does_not_run(self):
        path = self._write_script("prova.py", "print('non dovrebbe uscire')\n")
        result = self.skill.execute({"path": path})
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(result.data["confirm_parameters"]["path"], path)
        self.assertTrue(result.data["confirm_parameters"]["confirmed"])

    def test_confirmed_execution_actually_runs_the_script(self):
        path = self._write_script("prova.py", "print('ciao dal test')\n")
        result = self.skill.execute({"path": path, "confirmed": True})
        self.assertTrue(result.success)
        self.assertIn("ciao dal test", result.data["output"])


class ExitCodeReflectsRealOutcomeTests(_WithSkill):
    """Il buco reale trovato e corretto in questa sessione (stesso di RUN_COMMAND)."""

    def test_a_script_that_exits_cleanly_is_reported_as_success(self):
        path = self._write_script("ok.py", "import sys\nsys.exit(0)\n")
        result = self.skill.execute({"path": path, "confirmed": True})
        self.assertTrue(result.success)
        self.assertIsNone(result.error)

    def test_a_script_that_raises_an_uncaught_exception_is_reported_as_a_failure(self):
        path = self._write_script("rotto.py", "raise RuntimeError('boom')\n")
        result = self.skill.execute({"path": path, "confirmed": True})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NONZERO_EXIT")
        self.assertEqual(result.data["return_code"], 1)
        self.assertIn("boom", result.data["output"])

    def test_a_script_with_an_explicit_nonzero_exit_is_reported_as_a_failure(self):
        path = self._write_script("exit2.py", "import sys\nsys.exit(2)\n")
        result = self.skill.execute({"path": path, "confirmed": True})
        self.assertFalse(result.success)
        self.assertEqual(result.data["return_code"], 2)


if __name__ == "__main__":
    unittest.main()
