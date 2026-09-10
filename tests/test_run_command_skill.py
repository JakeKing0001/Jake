"""Test unitari per skills/run_command.py: nessuna suite esisteva finora, nonostante sia
esplicitamente dichiarata "la skill piu' potente e piu' rischiosa di Jake" nel suo stesso
docstring - RUN_COMMAND e' ADMIN (core/risk.py) e SELF_CONFIRMING (la propria richiesta di
conferma, non quella generica del gate centrale). Esegue comandi reali (echo/exit), non li
mocka: e' proprio subprocess.run(shell=True) a essere la parte interessante da verificare.

F1: buco reale trovato e corretto in questa sessione. Un comando che fallisce per davvero
(codice di uscita diverso da zero) veniva comunque riportato come success=True - l'utente se ne
accorgeva leggendo "codice N" nella risposta testuale, ma il campo strutturato result.success,
di cui si fidano il ledger di audit e la dashboard (successi/fallimenti per skill), mentiva."""
import unittest

from core.response_formatter import format_skill_result
from skills.run_command import RunCommandSkill


class MissingParametersTests(unittest.TestCase):
    def test_empty_command_fails(self):
        result = RunCommandSkill().execute({"command": ""})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_missing_command_key_fails(self):
        result = RunCommandSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")


class SafetyBlocklistTests(unittest.TestCase):
    def test_a_destructive_command_is_blocked_even_before_asking_for_confirmation(self):
        result = RunCommandSkill().execute({"command": "format c:"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "BLOCKED")

    def test_blocklist_applies_even_if_the_caller_already_marks_it_confirmed(self):
        """Una skill 'auto-confermante' non deve mai lasciare che 'confirmed=True' bypassi il
        blocklist di sicurezza - il controllo va fatto PRIMA di guardare quel flag."""
        result = RunCommandSkill().execute({"command": "format c:", "confirmed": True})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "BLOCKED")


class ConfirmationFlowTests(unittest.TestCase):
    def test_a_safe_command_without_confirmation_asks_for_it_and_does_not_run(self):
        result = RunCommandSkill().execute({"command": "echo non_dovrebbe_uscire_senza_conferma"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(result.data["confirm_parameters"]["command"], "echo non_dovrebbe_uscire_senza_conferma")
        self.assertTrue(result.data["confirm_parameters"]["confirmed"])

    def test_a_confirmed_safe_command_actually_runs(self):
        result = RunCommandSkill().execute({"command": "echo ciao", "confirmed": True})
        self.assertTrue(result.success)
        self.assertIn("ciao", result.data["output"])


class ExitCodeReflectsRealOutcomeTests(unittest.TestCase):
    """Il buco reale trovato e corretto in questa sessione."""

    def test_a_successful_command_is_reported_as_success(self):
        result = RunCommandSkill().execute({"command": "exit 0", "confirmed": True})
        self.assertTrue(result.success)
        self.assertIsNone(result.error)
        self.assertEqual(result.data["return_code"], 0)

    def test_a_failing_command_is_reported_as_a_failure_not_a_success(self):
        result = RunCommandSkill().execute({"command": "exit 1", "confirmed": True})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NONZERO_EXIT")
        self.assertEqual(result.data["return_code"], 1)

    def test_the_user_visible_message_still_shows_the_output_on_failure(self):
        """La correzione non deve far perdere all'utente l'output del comando fallito dietro un
        generico 'si e' verificato un errore' - deve vederlo esattamente come prima."""
        result = RunCommandSkill().execute({"command": "exit 3", "confirmed": True})
        message = format_skill_result("RUN_COMMAND", result, None)
        self.assertIn("codice 3", message)


class OutputTruncationTests(unittest.TestCase):
    def test_very_long_output_is_truncated(self):
        result = RunCommandSkill().execute({
            "command": 'python -c "print(\'x\' * 3000)"', "confirmed": True,
        })
        self.assertTrue(result.success)
        self.assertLessEqual(len(result.data["output"]), 1600)
        self.assertIn("troncato", result.data["output"])


if __name__ == "__main__":
    unittest.main()
