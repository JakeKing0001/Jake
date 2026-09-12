"""Test unitari per skills/run_command.py: nessuna suite esisteva finora, nonostante sia
esplicitamente dichiarata "la skill piu' potente e piu' rischiosa di Jake" nel suo stesso
docstring - RUN_COMMAND e' ADMIN (core/risk.py) e SELF_CONFIRMING (la propria richiesta di
conferma, non quella generica del gate centrale). Esegue comandi reali (echo/exit), non li
mocka: e' proprio subprocess.run(shell=True) a essere la parte interessante da verificare.

F1: buco reale trovato e corretto in questa sessione. Un comando che fallisce per davvero
(codice di uscita diverso da zero) veniva comunque riportato come success=True - l'utente se ne
accorgeva leggendo "codice N" nella risposta testuale, ma il campo strutturato result.success,
di cui si fidano il ledger di audit e la dashboard (successi/fallimenti per skill), mentiva."""
import threading
import time
import unittest
from unittest import mock

from core.kill_switch import KillSwitch
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


class KillSwitchCancellationTests(unittest.TestCase):
    """F1.8.3 ("propagare cancellazione dal kill switch a... subprocess"): buco reale, riprodotto
    prima del fix - subprocess.run(..., timeout=30) e' una chiamata bloccante che TaskAgent/
    PlanExecutor non possono interrompere (controllano kill_switch.is_active() solo TRA un passo
    e il successivo), quindi un comando lungo continuava fino alla fine anche con l'utente che
    aveva gia' premuto il kill switch (riprodotto: un comando che dorme 3s finiva comunque dopo
    ~3s con il kill switch attivo dopo 0.3s). Processi VERI, non mock: e' proprio l'interazione
    con subprocess.Popen/communicate/kill a essere la parte interessante da verificare."""

    LONG_SLEEP_COMMAND = 'python -c "import time; time.sleep(3)"'

    def _skill_with_kill_switch_activated_after(self, delay: float) -> RunCommandSkill:
        skill = RunCommandSkill()
        skill.kill_switch = KillSwitch()

        def _activate_soon():
            time.sleep(delay)
            skill.kill_switch.activate()

        threading.Thread(target=_activate_soon, daemon=True).start()
        return skill

    def test_activating_the_kill_switch_stops_a_long_running_command_quickly(self):
        skill = self._skill_with_kill_switch_activated_after(0.3)
        start = time.time()
        result = skill.execute({"command": self.LONG_SLEEP_COMMAND, "confirmed": True})
        elapsed = time.time() - start

        self.assertEqual(result.error, "KILLED")
        self.assertFalse(result.success)
        self.assertLess(elapsed, 1.5, "il comando ha ignorato il kill switch (bloccato per l'intera durata)")

    def test_without_a_kill_switch_a_long_command_is_not_cancellable(self):
        """Comportamento invariato quando nessun kill switch e' stato iniettato (self.kill_switch
        resta None: contesti di test o percorsi che non lo passano ancora) - stesso
        subprocess.run bloccante di prima, nessuna regressione per chi non usa questa capacita'."""
        skill = RunCommandSkill()
        self.assertIsNone(skill.kill_switch)
        result = skill.execute({"command": "exit 0", "confirmed": True})
        self.assertTrue(result.success)

    def test_a_command_that_finishes_on_its_own_is_not_affected_by_an_unused_kill_switch(self):
        skill = RunCommandSkill()
        skill.kill_switch = KillSwitch()  # presente ma mai attivato
        result = skill.execute({"command": "echo ciao", "confirmed": True})
        self.assertTrue(result.success)
        self.assertIn("ciao", result.data["output"])

    def test_a_failing_command_still_reports_the_real_exit_code_through_the_cancellable_path(self):
        skill = RunCommandSkill()
        skill.kill_switch = KillSwitch()
        result = skill.execute({"command": "exit 3", "confirmed": True})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NONZERO_EXIT")
        self.assertEqual(result.data["return_code"], 3)

    def test_timeout_still_fires_through_the_cancellable_path_when_the_kill_switch_never_activates(self):
        skill = RunCommandSkill()
        skill.kill_switch = KillSwitch()  # mai attivato: deve scattare il timeout, non il kill switch
        with mock.patch("skills.run_command.COMMAND_TIMEOUT_SECONDS", 1):
            start = time.time()
            result = skill.execute({"command": self.LONG_SLEEP_COMMAND, "confirmed": True})
            elapsed = time.time() - start

        self.assertEqual(result.error, "TIMEOUT")
        self.assertLess(elapsed, 2.0)


if __name__ == "__main__":
    unittest.main()
