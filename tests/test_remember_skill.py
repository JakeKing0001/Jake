"""Test unitari per skills/remember.py: nessuna suite esisteva finora. Usa un MemoryManager
vero su file temporaneo (stesso motore reale di REMEMBER/RECALL/FORGET), non un finto - la parte
interessante e' proprio la propagazione dei parametri fino al ricordo salvato per davvero.

F5 (Memory 2.0): questa sessione aveva aggiunto ttl_days a MemoryManager.remember() (scadenza,
vedi ROADMAP.md), ma nessuna skill lo esponeva mai all'utente - "in attesa del primo chiamante
reale", dichiarato onestamente li'. Qui si completa quel collegamento: REMEMBER accetta ora un
ttl_days opzionale e lo passa al motore vero."""
import tempfile
import unittest
from pathlib import Path

from core.memory_manager import MemoryManager
from skills.remember import RememberSkill


class _WithSkill(unittest.TestCase):
    def setUp(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_remember_skill_test_"))
        self.memory_manager = MemoryManager(db_path=tmp_dir / "memory.db")
        self.skill = RememberSkill(self.memory_manager)


class BasicRememberTests(_WithSkill):
    def test_saves_the_key_and_value(self):
        result = self.skill.execute({"key": "colore preferito", "value": "blu"})
        self.assertTrue(result.success)
        [memory] = self.memory_manager.recall(key="colore preferito")
        self.assertEqual(memory["value"], "blu")

    def test_missing_key_or_value_fails(self):
        self.assertEqual(self.skill.execute({"value": "blu"}).error, "MISSING_PARAMETERS")
        self.assertEqual(self.skill.execute({"key": "colore"}).error, "MISSING_PARAMETERS")

    def test_defaults_category_to_fact_and_importance_to_one(self):
        self.skill.execute({"key": "k", "value": "v"})
        [memory] = self.memory_manager.recall(key="k")
        self.assertEqual(memory["category"], "fact")
        self.assertEqual(memory["importance"], 1)

    def test_explicit_category_project_and_importance_are_forwarded(self):
        self.skill.execute({"key": "k", "value": "v", "category": "preference", "project": "NEST", "importance": 4})
        [memory] = self.memory_manager.recall(key="k")
        self.assertEqual(memory["category"], "preference")
        self.assertEqual(memory["project"], "NEST")
        self.assertEqual(memory["importance"], 4)


class TtlDaysTests(_WithSkill):
    """Il collegamento reale aggiunto in questa sessione."""

    def test_ttl_days_is_forwarded_and_produces_an_expiry(self):
        result = self.skill.execute({"key": "oggi piove", "value": "vero", "ttl_days": 1})
        self.assertTrue(result.success)
        self.assertEqual(result.data["ttl_days"], 1)
        [memory] = self.memory_manager.recall(key="oggi piove", include_expired=True)
        self.assertIsNotNone(memory["expires_at"])

    def test_without_ttl_days_the_memory_never_expires(self):
        self.skill.execute({"key": "compleanno", "value": "5 marzo"})
        [memory] = self.memory_manager.recall(key="compleanno")
        self.assertIsNone(memory["expires_at"])

    def test_a_memory_with_an_already_past_ttl_is_purged_by_purge_expired(self):
        import time
        self.skill.execute({"key": "scaduto subito", "value": "x", "ttl_days": 0.0000001})
        time.sleep(0.01)
        removed = self.memory_manager.purge_expired()
        self.assertGreaterEqual(removed, 1)
        self.assertEqual(self.memory_manager.recall(key="scaduto subito", include_expired=True), [])

    def test_zero_ttl_days_is_treated_as_no_expiry_not_an_error(self):
        result = self.skill.execute({"key": "k", "value": "v", "ttl_days": 0})
        self.assertTrue(result.success)
        self.assertIsNone(result.data["ttl_days"])
        [memory] = self.memory_manager.recall(key="k")
        self.assertIsNone(memory["expires_at"])

    def test_negative_ttl_days_is_treated_as_no_expiry_not_an_error(self):
        result = self.skill.execute({"key": "k", "value": "v", "ttl_days": -5})
        self.assertTrue(result.success)
        self.assertIsNone(result.data["ttl_days"])

    def test_non_numeric_ttl_days_is_treated_as_no_expiry_not_a_crash(self):
        result = self.skill.execute({"key": "k", "value": "v", "ttl_days": "presto"})
        self.assertTrue(result.success)
        self.assertIsNone(result.data["ttl_days"])


if __name__ == "__main__":
    unittest.main()
