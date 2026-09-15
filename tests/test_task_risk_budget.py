"""Test unitari per core/task_risk_budget.py (F1.5.8, fase 8/10 del piano multi-device): le sei
combinazioni di escalation della specifica, ciascuna verificata sia nel caso positivo (la
combinazione scatta) sia in quello negativo (un passo isolato, o la combinazione INVERTITA, non
deve mai scattare per errore)."""
import unittest

from core.risk import RiskLevel
from core.task_risk_budget import TaskRiskBudget


def _budget(max_authorized_risk: RiskLevel = RiskLevel.READ_ONLY) -> TaskRiskBudget:
    return TaskRiskBudget(max_authorized_risk=max_authorized_risk)


class NoEscalationByDefaultTests(unittest.TestCase):
    def test_a_fresh_budget_flags_no_escalation_for_an_ordinary_intent(self):
        budget = _budget()
        self.assertIsNone(budget.escalation_reason("GET_TIME"))

    def test_a_fresh_budget_flags_no_escalation_even_for_an_external_action(self):
        """Un EXTERNAL_ACTION isolato, senza nulla di sensibile letto prima, non e' di per se'
        un'escalation - la specifica riguarda la CATENA, non il singolo passo."""
        budget = _budget()
        self.assertIsNone(budget.escalation_reason("SEND_EMAIL"))

    def test_recording_an_unrelated_step_does_not_create_a_false_escalation(self):
        budget = _budget()
        budget.record_step("GET_TIME")
        self.assertIsNone(budget.escalation_reason("SEND_EMAIL"))


class ExternalContentThenExternalEffectTests(unittest.TestCase):
    """Combo 1/2 unificate: 'leggere dati privati -> inviarli fuori' e 'clipboard/file/schermo
    -> email/messaggio/web upload'."""

    def test_reading_the_clipboard_then_sending_an_email_is_flagged(self):
        budget = _budget()
        budget.record_step("CLIPBOARD_READ")
        self.assertIsNotNone(budget.escalation_reason("SEND_EMAIL"))

    def test_reading_the_screen_then_opening_a_url_is_flagged(self):
        budget = _budget()
        budget.record_step("READ_SCREEN")
        self.assertIsNotNone(budget.escalation_reason("OPEN_URL"))

    def test_reading_a_file_then_printing_is_flagged(self):
        budget = _budget()
        budget.record_step("READ_FILE_TEXT")
        self.assertIsNotNone(budget.escalation_reason("PRINT_FILE"))

    def test_recalling_a_memory_then_sending_it_externally_is_flagged(self):
        """'Dati privati' (memoria/rubrica/appunti dell'utente), non solo contenuto esterno."""
        budget = _budget()
        budget.record_step("RECALL", {"key": "compleanno mamma"})
        self.assertIsNotNone(budget.escalation_reason("SEND_WHATSAPP"))

    def test_reading_the_clipboard_then_another_read_only_step_is_not_flagged(self):
        budget = _budget()
        budget.record_step("CLIPBOARD_READ")
        self.assertIsNone(budget.escalation_reason("GET_TIME"))

    def test_sending_an_email_before_ever_reading_anything_sensitive_is_not_flagged(self):
        budget = _budget()
        self.assertIsNone(budget.escalation_reason("SEND_EMAIL"))


class CredentialAccessThenExternalEffectTests(unittest.TestCase):
    """Combo 5: 'accesso credenziali -> trasmissione esterna'."""

    def test_recalling_a_password_then_sending_it_externally_is_flagged(self):
        budget = _budget()
        budget.record_step("RECALL", {"key": "password wifi"})
        self.assertIsNotNone(budget.escalation_reason("SEND_EMAIL"))

    def test_recalling_a_pin_then_opening_a_url_is_flagged(self):
        budget = _budget()
        budget.record_step("RECALL", {"key": "pin bancomat"})
        self.assertIsNotNone(budget.escalation_reason("OPEN_URL"))

    def test_recalling_a_non_credential_memory_does_not_set_the_credential_flag(self):
        budget = _budget()
        budget.record_step("RECALL", {"key": "compleanno mamma"})
        self.assertFalse(budget.credential_accessed)

    def test_a_key_without_any_credential_marker_word_is_not_treated_as_a_credential(self):
        budget = _budget()
        budget.record_step("RECALL", {"key": "ricetta della torta"})
        self.assertFalse(budget.credential_accessed)


class DownloadThenExecuteTests(unittest.TestCase):
    """Combo 3: 'download -> execute'."""

    def test_git_pull_then_run_command_is_flagged(self):
        budget = _budget()
        budget.record_step("GIT_PULL")
        self.assertIsNotNone(budget.escalation_reason("RUN_COMMAND"))

    def test_git_pull_then_run_python_script_is_flagged(self):
        budget = _budget()
        budget.record_step("GIT_PULL")
        self.assertIsNotNone(budget.escalation_reason("RUN_PYTHON_SCRIPT"))

    def test_git_pull_then_a_read_only_step_is_not_flagged(self):
        budget = _budget()
        budget.record_step("GIT_PULL")
        self.assertIsNone(budget.escalation_reason("GIT_STATUS"))

    def test_running_a_command_without_a_prior_download_is_not_flagged_by_this_rule(self):
        budget = _budget()
        self.assertIsNone(budget.escalation_reason("RUN_COMMAND"))


class CreateThenExecuteTests(unittest.TestCase):
    """Combo 4: 'creare file/script -> execute'."""

    def test_creating_a_path_then_running_a_command_is_flagged(self):
        budget = _budget()
        budget.record_step("CREATE_PATH")
        self.assertIsNotNone(budget.escalation_reason("RUN_COMMAND"))

    def test_creating_a_skill_then_running_a_python_script_is_flagged(self):
        budget = _budget()
        budget.record_step("CREATE_SKILL")
        self.assertIsNotNone(budget.escalation_reason("RUN_PYTHON_SCRIPT"))

    def test_creating_a_path_then_a_read_only_step_is_not_flagged(self):
        budget = _budget()
        budget.record_step("CREATE_PATH")
        self.assertIsNone(budget.escalation_reason("GET_FILE_INFO"))


class SecurityReducedThenPrivilegedActionTests(unittest.TestCase):
    """Combo 6: 'disabilitare sicurezza -> azioni privilegiate' (qui: modalita' privata attiva,
    che sospende l'audit - vedi il docstring del modulo sul perche')."""

    def test_enabling_private_mode_then_a_destructive_action_is_flagged(self):
        budget = _budget()
        budget.record_step("SET_PRIVATE_MODE", {"enabled": True})
        self.assertIsNotNone(budget.escalation_reason("DELETE_PATH"))

    def test_enabling_private_mode_then_an_admin_action_is_flagged(self):
        budget = _budget()
        budget.record_step("SET_PRIVATE_MODE", {"enabled": True})
        self.assertIsNotNone(budget.escalation_reason("RUN_COMMAND"))

    def test_disabling_private_mode_does_not_set_the_flag(self):
        """SET_PRIVATE_MODE(enabled=False) e' l'OPPOSTO - torna alla sorveglianza normale, non la
        riduce - non deve mai scatenare questa regola."""
        budget = _budget()
        budget.record_step("SET_PRIVATE_MODE", {"enabled": False})
        self.assertFalse(budget.security_reduced)

    def test_enabling_private_mode_then_a_read_only_action_is_not_flagged(self):
        budget = _budget()
        budget.record_step("SET_PRIVATE_MODE", {"enabled": True})
        self.assertIsNone(budget.escalation_reason("GET_TIME"))

    def test_a_destructive_action_without_private_mode_ever_enabled_is_not_flagged_by_this_rule(self):
        budget = _budget()
        self.assertIsNone(budget.escalation_reason("DELETE_PATH"))


class ResourcesTouchedTests(unittest.TestCase):
    def test_resource_keys_accumulate_across_steps(self):
        budget = _budget()
        budget.record_step("CREATE_PATH", resource_keys=("filesystem:/a",))
        budget.record_step("RENAME_PATH", resource_keys=("filesystem:/b",))
        self.assertEqual(budget.resources_touched, {"filesystem:/a", "filesystem:/b"})

    def test_no_resource_keys_is_the_default(self):
        budget = _budget()
        budget.record_step("GET_TIME")
        self.assertEqual(budget.resources_touched, set())


if __name__ == "__main__":
    unittest.main()
