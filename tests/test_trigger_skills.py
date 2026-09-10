"""Test unitari per skills/trigger.py: nessuna suite esisteva finora. TriggerManager e' finto
(la sua vera implementazione ha gia' tests/test_trigger_manager.py) - qui si verifica solo la
logica della skill: validazione parametri, ed emblematicamente la normalizzazione dell'orario.

F6: buco reale trovato e corretto in questa sessione. TriggerScheduler._time_is_due() confronta
lo spec salvato con now.strftime("%H:%M"), che ha SEMPRE lo zero iniziale (es. "09:05", mai
"9:5"). SetTriggerSkill non normalizzava l'orario prima di salvarlo: un trigger creato con "9:5"
o "9:05" (la forma piu' naturale per dire un orario mattutino) veniva salvato con successo ma
non sarebbe MAI scattato, in silenzio - nessun errore, nessun avviso, l'utente crede che sia
tutto a posto."""
import unittest
from unittest import mock

from skills.trigger import DeleteTriggerSkill, ListTriggersSkill, SetTriggerSkill


class FakeTriggerManager:
    def __init__(self, existing_workflows=None, existing_triggers=None):
        self.existing_workflows = set(existing_workflows or [])
        self.existing_triggers = set(existing_triggers or [])
        self.saved = []
        self.deleted = []
        self.all_triggers = []

    def workflow_exists(self, name):
        return name in self.existing_workflows

    def save(self, name, workflow_name, trigger_type, spec):
        self.saved.append((name, workflow_name, trigger_type, spec))

    def list_all(self):
        return self.all_triggers

    def delete(self, name):
        if name not in self.existing_triggers:
            return False
        self.existing_triggers.discard(name)
        self.deleted.append(name)
        return True


class SetTriggerMissingParametersTests(unittest.TestCase):
    def _skill(self):
        return SetTriggerSkill(FakeTriggerManager(existing_workflows={"routine"}))

    def test_missing_name_fails(self):
        result = self._skill().execute({"workflow_name": "routine", "trigger_type": "time", "at_time": "09:00"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_missing_workflow_name_fails(self):
        result = self._skill().execute({"name": "t", "trigger_type": "time", "at_time": "09:00"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_invalid_trigger_type_fails(self):
        result = self._skill().execute({"name": "t", "workflow_name": "routine", "trigger_type": "qualcosa"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_time_trigger_without_at_time_fails(self):
        result = self._skill().execute({"name": "t", "workflow_name": "routine", "trigger_type": "time"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_app_focus_trigger_without_app_contains_fails(self):
        result = self._skill().execute({"name": "t", "workflow_name": "routine", "trigger_type": "app_focus"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")


class SetTriggerTimeNormalizationTests(unittest.TestCase):
    """Il buco reale trovato e corretto in questa sessione."""

    def _skill_and_manager(self):
        manager = FakeTriggerManager(existing_workflows={"routine"})
        return SetTriggerSkill(manager), manager

    def test_single_digit_hour_and_minute_are_zero_padded(self):
        skill, manager = self._skill_and_manager()
        result = skill.execute({"name": "t", "workflow_name": "routine", "trigger_type": "time", "at_time": "9:5"})
        self.assertTrue(result.success)
        self.assertEqual(manager.saved[0][3], {"at": "09:05"})

    def test_already_canonical_time_is_unchanged(self):
        skill, manager = self._skill_and_manager()
        skill.execute({"name": "t", "workflow_name": "routine", "trigger_type": "time", "at_time": "14:30"})
        self.assertEqual(manager.saved[0][3], {"at": "14:30"})

    def test_saved_time_matches_what_the_scheduler_will_actually_compare_against(self):
        """Il controllo di fondo: qualunque orario valido salvato deve risultare identico a
        datetime.strftime('%H:%M') per quell'ora/minuto - esattamente il confronto che
        TriggerScheduler._time_is_due() fa per davvero (core/trigger_scheduler.py)."""
        from datetime import datetime
        skill, manager = self._skill_and_manager()
        skill.execute({"name": "t", "workflow_name": "routine", "trigger_type": "time", "at_time": "9:5"})
        saved_at = manager.saved[0][3]["at"]
        self.assertEqual(saved_at, datetime(2026, 1, 1, 9, 5).strftime("%H:%M"))

    def test_nonsensical_time_is_rejected_with_invalid_time(self):
        skill, manager = self._skill_and_manager()
        result = skill.execute({"name": "t", "workflow_name": "routine", "trigger_type": "time", "at_time": "9am"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "INVALID_TIME")
        self.assertEqual(manager.saved, [])

    def test_out_of_range_hour_is_rejected(self):
        skill, manager = self._skill_and_manager()
        result = skill.execute({"name": "t", "workflow_name": "routine", "trigger_type": "time", "at_time": "25:00"})
        self.assertEqual(result.error, "INVALID_TIME")

    def test_out_of_range_minute_is_rejected(self):
        skill, manager = self._skill_and_manager()
        result = skill.execute({"name": "t", "workflow_name": "routine", "trigger_type": "time", "at_time": "10:99"})
        self.assertEqual(result.error, "INVALID_TIME")

    def test_app_focus_trigger_is_not_affected_by_time_validation(self):
        skill, manager = self._skill_and_manager()
        result = skill.execute({
            "name": "t", "workflow_name": "routine", "trigger_type": "app_focus", "app_contains": "spotify",
        })
        self.assertTrue(result.success)
        self.assertEqual(manager.saved[0][3], {"app_contains": "spotify"})


class SetTriggerWorkflowExistenceTests(unittest.TestCase):
    def test_referencing_an_unknown_workflow_fails(self):
        manager = FakeTriggerManager(existing_workflows=set())
        result = SetTriggerSkill(manager).execute({
            "name": "t", "workflow_name": "non_esiste", "trigger_type": "time", "at_time": "09:00",
        })
        self.assertEqual(result.error, "NOT_FOUND")
        self.assertEqual(manager.saved, [])


class ListAndDeleteTriggerTests(unittest.TestCase):
    def test_list_with_no_triggers_reports_not_found(self):
        result = ListTriggersSkill(FakeTriggerManager()).execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_list_returns_the_triggers_from_the_manager(self):
        manager = FakeTriggerManager()
        manager.all_triggers = [{"name": "t"}]
        result = ListTriggersSkill(manager).execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["triggers"], [{"name": "t"}])

    def test_delete_missing_name_fails(self):
        result = DeleteTriggerSkill(FakeTriggerManager()).execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_delete_unknown_trigger_reports_not_found(self):
        result = DeleteTriggerSkill(FakeTriggerManager()).execute({"name": "non_esiste"})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_delete_existing_trigger_succeeds(self):
        manager = mock.MagicMock()
        manager.delete.return_value = True
        result = DeleteTriggerSkill(manager).execute({"name": "t"})
        self.assertTrue(result.success)
        manager.delete.assert_called_once_with("t")


if __name__ == "__main__":
    unittest.main()
