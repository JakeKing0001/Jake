"""Test unitari per RECALL (skills/recall.py), in particolare il nuovo parametro 'when' (F5,
Memory 2.0 - linguaggio naturale per il tempo, vedi core/temporal_parser.py e ROADMAP.md fase
F5). Usa un MemoryManager vero su sqlite temporaneo (mai il database di produzione), come
tests/test_link_memory_skill.py per lo stesso motivo - qui pero' e' dedicato alla skill, che non
aveva ancora una sua suite."""
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.memory_manager import MemoryManager
from skills.recall import RecallSkill


class RecallSkillTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_recall_skill_test_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.manager = MemoryManager(db_path=self.tmp_dir / "memory.db")
        self.addCleanup(self.manager.close)
        self.skill = RecallSkill(self.manager)

    def _set_updated_at(self, key: str, when: datetime) -> None:
        self.manager._connection.execute(
            "UPDATE memories SET updated_at = ? WHERE key = ?", (when.isoformat(), key),
        )
        self.manager._connection.commit()


class BasicRecallTests(RecallSkillTestCase):
    def test_exact_key_match(self):
        self.manager.remember("colore preferito", "blu")

        result = self.skill.execute({"key": "colore preferito"})

        self.assertTrue(result.success)
        self.assertEqual(result.data["results"][0]["value"], "blu")

    def test_nothing_found_is_reported(self):
        result = self.skill.execute({"key": "non esiste"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")


class WhenParameterTests(RecallSkillTestCase):
    """F5: 'when' filtra per intervallo di tempo relativo (core/temporal_parser.py), passato a
    MemoryManager.recall(since=..., until=...) - gia' esistente (v3.4) ma prima raggiungibile
    solo passando since/until gia' pronti in ISO 8601, mai da un'espressione detta dall'utente."""

    def test_recognized_when_filters_to_the_matching_day(self):
        now = datetime.now(timezone.utc)
        self.manager.remember("nota", "di ieri")
        self._set_updated_at("nota", now - timedelta(days=1))
        self.manager.remember("altra nota", "di oggi")
        self._set_updated_at("altra nota", now)

        result = self.skill.execute({"query": "nota", "when": "ieri"})

        self.assertTrue(result.success)
        values = {entry["value"] for entry in result.data["results"]}
        self.assertEqual(values, {"di ieri"})

    def test_unrecognized_when_does_not_fail_the_request(self):
        """Un'espressione che il parser non capisce ancora (es. riferita a un evento, non a
        adesso) non deve rifiutare l'intera richiesta: RECALL si comporta come se 'when' non
        fosse stato detto."""
        self.manager.remember("colore preferito", "blu")

        result = self.skill.execute({"key": "colore preferito", "when": "prima della riunione"})

        self.assertTrue(result.success)
        self.assertEqual(result.data["results"][0]["value"], "blu")

    def test_when_without_key_or_query_still_filters_by_time(self):
        now = datetime.now(timezone.utc)
        self.manager.remember("nota vecchia", "roba di ieri")
        self._set_updated_at("nota vecchia", now - timedelta(days=1))

        result = self.skill.execute({"query": "nota", "when": "oggi"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")

    def test_when_applies_to_the_key_fallback_search_too(self):
        """Il ripiego su recall(query=key) quando la chiave esatta non combacia (vedi
        skills/recall.py) deve rispettare comunque il vincolo temporale, non ignorarlo."""
        now = datetime.now(timezone.utc)
        self.manager.remember("nota su Mario", "e' un collega")
        self._set_updated_at("nota su Mario", now - timedelta(days=1))

        result = self.skill.execute({"key": "mario", "when": "oggi"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
