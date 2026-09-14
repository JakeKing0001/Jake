"""Test unitari per core/agent_checkpoint.py (F1.8.4, "checkpoint... da cui riprendere")."""
import json
import tempfile
import unittest
from pathlib import Path

from core.agent_checkpoint import AgentCheckpoint, AgentCheckpointStore


class AgentCheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "agent_checkpoint.json"
        self.store = AgentCheckpointStore(path=self.path)

    def test_load_returns_none_when_no_checkpoint_exists(self):
        self.assertIsNone(self.store.load())

    def test_save_then_load_round_trips(self):
        checkpoint = AgentCheckpoint(
            trace_id="abc123", agent_name="general", request="crea un file e poi aprilo",
            completed_steps=[{"intent": "CREATE_PATH", "parameters": {"path": "C:\\x.txt"}, "success": True}],
        )

        self.store.save(checkpoint)
        loaded = self.store.load()

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.trace_id, "abc123")
        self.assertEqual(loaded.agent_name, "general")
        self.assertEqual(loaded.request, "crea un file e poi aprilo")
        self.assertEqual(loaded.completed_steps, [{"intent": "CREATE_PATH", "parameters": {"path": "C:\\x.txt"}, "success": True}])

    def test_save_is_atomic_no_tmp_file_left_behind_on_success(self):
        self.store.save(AgentCheckpoint(trace_id="a", agent_name="general", request="x"))
        self.assertTrue(self.path.is_file())
        self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())

    def test_a_second_save_overwrites_the_first_not_appends(self):
        self.store.save(AgentCheckpoint(trace_id="a", agent_name="general", request="primo"))
        self.store.save(AgentCheckpoint(trace_id="b", agent_name="general", request="secondo"))

        loaded = self.store.load()

        self.assertEqual(loaded.trace_id, "b")
        self.assertEqual(loaded.request, "secondo")

    def test_clear_removes_the_file(self):
        self.store.save(AgentCheckpoint(trace_id="a", agent_name="general", request="x"))
        self.store.clear()
        self.assertIsNone(self.store.load())

    def test_clear_is_safe_when_nothing_was_ever_saved(self):
        self.store.clear()  # non deve sollevare

    def test_a_corrupted_file_is_treated_as_no_checkpoint_not_a_crash(self):
        """Nega per difetto: un file manomesso/da un formato futuro non deve mai far crashare
        l'avvio di Jake ne' la lettura del checkpoint - stesso principio gia' applicato altrove
        in F1 (es. AgentCheckpointStore.load(), core/config.py)."""
        self.path.write_text("{questo non e' JSON valido", encoding="utf-8")
        self.assertIsNone(self.store.load())

    def test_a_valid_json_file_missing_required_fields_is_treated_as_no_checkpoint(self):
        self.path.write_text(json.dumps({"trace_id": "a"}), encoding="utf-8")
        self.assertIsNone(self.store.load())


if __name__ == "__main__":
    unittest.main()
