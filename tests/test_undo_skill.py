"""Test unitari per skills/undo.py::UndoLastActionSkill (F1.3.5, il consumatore che rende
visibile all'utente il meccanismo gia' costruito/adottato in core/undo_store.py)."""
import unittest

from core.undo_store import UndoStore, generate_undo_descriptor
from skills.undo import UndoLastActionSkill, _describe_undo


class FakeCore:
    def __init__(self, undo_store):
        self.undo_store = undo_store


class UndoLastActionSkillTests(unittest.TestCase):
    def test_an_empty_store_reports_no_undo_available(self):
        core = FakeCore(UndoStore())

        result = UndoLastActionSkill(core).execute({})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "NO_UNDO_AVAILABLE")

    def test_an_all_expired_store_reports_no_undo_available_too(self):
        store = UndoStore()
        store.save(generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "x"}, ttl_seconds=-1))
        core = FakeCore(store)

        result = UndoLastActionSkill(core).execute({})

        self.assertEqual(result.error, "NO_UNDO_AVAILABLE")

    def test_a_usable_descriptor_produces_a_confirmation_envelope_not_a_direct_execution(self):
        """La skill non esegue MAI l'intent compensatorio da sola - propone solo la busta, che
        deve rispettare esattamente il contratto di core/schema_validation.py::
        validate_confirm_envelope (message: str, confirm_parameters: dict)."""
        store = UndoStore()
        descriptor = generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "C:\\nuovo.txt"})
        store.save(descriptor)
        core = FakeCore(store)

        result = UndoLastActionSkill(core).execute({})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(result.data["confirm_intent"], "DELETE_PATH")
        self.assertEqual(result.data["confirm_parameters"], {"path": "C:\\nuovo.txt", "confirmed": True})
        self.assertIn("C:\\nuovo.txt", result.data["message"])

    def test_confirm_parameters_returned_is_a_copy_not_the_descriptor_s_own_dict(self):
        """Un chiamante che modificasse la busta ricevuta (es. schema_validation la arricchisce
        altrove) non deve mai corrompere il descrittore ancora vivo nello store."""
        store = UndoStore()
        store.save(generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "x"}))
        core = FakeCore(store)

        result = UndoLastActionSkill(core).execute({})
        result.data["confirm_parameters"]["path"] = "manomesso"

        self.assertEqual(store.get("action-1").compensating_parameters["path"], "x")

    def test_picks_the_most_recently_saved_descriptor_across_the_whole_store(self):
        store = UndoStore()
        store.save(generate_undo_descriptor("action-1", "CREATE_PATH", {"path": "vecchio.txt"}))
        store.save(generate_undo_descriptor("action-2", "CREATE_PATH", {"path": "nuovo.txt"}))
        core = FakeCore(store)

        result = UndoLastActionSkill(core).execute({})

        self.assertEqual(result.data["confirm_parameters"]["path"], "nuovo.txt")


class DescribeUndoTests(unittest.TestCase):
    def test_delete_path_is_described_in_italian(self):
        self.assertEqual(_describe_undo("DELETE_PATH", {"path": "C:\\x.txt"}), "eliminare C:\\x.txt")

    def test_move_path_is_described_with_source_and_destination(self):
        description = _describe_undo("MOVE_PATH", {"path": "C:\\a\\file.txt", "destination": "C:\\b"})
        self.assertEqual(description, "spostare C:\\a\\file.txt in C:\\b")

    def test_rename_path_is_described_with_old_and_new_name(self):
        description = _describe_undo("RENAME_PATH", {"path": "C:\\nuovo.txt", "new_name": "vecchio.txt"})
        self.assertEqual(description, "rinominare C:\\nuovo.txt in vecchio.txt")

    def test_an_unknown_compensating_intent_falls_back_to_a_generic_but_honest_description(self):
        """Nessuno dei quattro intent compensatori noti oggi manca mai a questo dizionario (sono
        tutti in UNDO_PARAMS_BY_INTENT) - questo copre solo un futuro quinto intent compensatorio
        non ancora aggiunto qui, mai un crash su un valore inatteso."""
        self.assertEqual(_describe_undo("SOME_FUTURE_INTENT", {}), "eseguire SOME_FUTURE_INTENT")


if __name__ == "__main__":
    unittest.main()
