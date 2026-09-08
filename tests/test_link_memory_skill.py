"""Test unitari per LINK_MEMORY (skills/link_memory.py, v4.4) e per il salto nel grafo di
conoscenza personale dentro RECALL (skills/recall.py). Usa un MemoryManager vero su sqlite
temporaneo, mai il database di produzione."""
import shutil
import tempfile
import unittest
from pathlib import Path

from core.memory_manager import MemoryManager
from skills.link_memory import LinkMemorySkill
from skills.recall import RecallSkill


class MemoryBackedTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_link_memory_test_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.manager = MemoryManager(db_path=self.tmp_dir / "memory.db")
        self.addCleanup(self.manager.close)


class LinkMemorySkillTests(MemoryBackedTestCase):
    def test_links_two_existing_memories(self):
        self.manager.remember("Mario", "un collega")
        self.manager.remember("Acme", "un'azienda")
        skill = LinkMemorySkill(self.manager)

        result = skill.execute({"subject": "Mario", "predicate": "lavora per", "object": "Acme"})

        self.assertTrue(result.success)
        self.assertEqual(self.manager.related("Mario", "fact")[0]["key"], "Acme")

    def test_missing_subject_is_reported(self):
        self.manager.remember("Acme", "un'azienda")
        skill = LinkMemorySkill(self.manager)

        result = skill.execute({"subject": "Non esiste", "predicate": "lavora per", "object": "Acme"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")
        self.assertEqual(result.data["key"], "Non esiste")

    def test_missing_object_is_reported(self):
        self.manager.remember("Mario", "un collega")
        skill = LinkMemorySkill(self.manager)

        result = skill.execute({"subject": "Mario", "predicate": "lavora per", "object": "Non esiste"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")
        self.assertEqual(result.data["key"], "Non esiste")

    def test_missing_parameters(self):
        skill = LinkMemorySkill(self.manager)
        result = skill.execute({"subject": "Mario", "predicate": "", "object": "Acme"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "MISSING_PARAMETERS")


class RecallWithRelatedMemoriesTests(MemoryBackedTestCase):
    def test_recall_includes_one_hop_of_related_memories(self):
        self.manager.remember("Mario", "un collega")
        self.manager.remember("Acme", "un'azienda")
        self.manager.link("Mario", "fact", "lavora per", "Acme", "fact")
        skill = RecallSkill(self.manager)

        result = skill.execute({"key": "Mario"})

        self.assertTrue(result.success)
        self.assertEqual(result.data["results"][0]["related"], [
            {"predicate": "lavora per", "key": "Acme", "category": "fact", "value": "un'azienda"},
        ])

    def test_recall_without_links_has_no_related_key(self):
        self.manager.remember("Mario", "un collega")
        skill = RecallSkill(self.manager)

        result = skill.execute({"key": "Mario"})

        self.assertNotIn("related", result.data["results"][0])


if __name__ == "__main__":
    unittest.main()
