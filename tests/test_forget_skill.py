"""Test unitari per skills/forget.py: nessuna suite esisteva finora, nonostante FORGET sia
DESTRUCTIVE (core/risk.py) e NON self-confirming - a differenza di EMPTY_RECYCLE_BIN/
RUN_COMMAND, qui l'unica barriera e' il gate centrale (JakeCore._resolve_and_execute), la skill
stessa esegue subito se chiamata. Usa un MemoryManager vero su file temporaneo."""
import tempfile
import unittest
from pathlib import Path

from core.memory_manager import MemoryManager
from skills.forget import ForgetSkill


class _WithSkill(unittest.TestCase):
    def setUp(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_forget_skill_test_"))
        self.memory_manager = MemoryManager(db_path=tmp_dir / "memory.db")
        self.skill = ForgetSkill(self.memory_manager)


class ForgetSkillTests(_WithSkill):
    def test_missing_key_fails(self):
        self.assertEqual(self.skill.execute({}).error, "MISSING_PARAMETERS")
        self.assertEqual(self.skill.execute({"key": "  "}).error, "MISSING_PARAMETERS")

    def test_forgetting_an_unknown_key_reports_not_found(self):
        result = self.skill.execute({"key": "non esiste"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")

    def test_forgetting_an_existing_memory_actually_removes_it(self):
        self.memory_manager.remember("password wifi", "abc123")
        result = self.skill.execute({"key": "password wifi"})
        self.assertTrue(result.success)
        self.assertEqual(self.memory_manager.recall(key="password wifi"), [])

    def test_only_the_targeted_key_is_removed(self):
        self.memory_manager.remember("a", "1")
        self.memory_manager.remember("b", "2")
        self.skill.execute({"key": "a"})
        self.assertNotEqual(self.memory_manager.recall(key="b"), [])


if __name__ == "__main__":
    unittest.main()
