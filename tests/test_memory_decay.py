"""F5.4.5/F5.4.6: un ricordo mai usato ne' aggiornato da molto tempo pesa meno nella scelta dei ricordi pertinenti
(solo ranking: nulla viene cancellato o nascosto); i ricordi fissati non decadono; una chiave citata vince sempre.
Database temporaneo, dati sintetici, date spostate nel passato direttamente sul database."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.memory_manager import MemoryManager


class MemoryDecayTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.memory = MemoryManager(Path(tmp.name) / "memory.db")
        self.addCleanup(self.memory.close)
        # piu' importante: senza decadimento vincerebbe lui
        self.memory.remember("palestra vecchia", "palestra Olimpia in via Roma", importance=3)
        self.memory.remember("palestra nuova", "palestra Fitness Club in via Milano")
        self._age("palestra vecchia", days=720)

    def _age(self, key, days):
        old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.memory.lock:
            self.memory.connection.execute("UPDATE memories SET updated_at = ?, last_used_at = NULL WHERE key = ?", (old, key))
            self.memory.connection.commit()

    def test_a_forgotten_memory_ranks_below_a_fresh_one_but_is_still_there(self):
        chosen = self.memory.relevant_for("in che palestra vado?")
        self.assertEqual([e["key"] for e in chosen], ["palestra nuova", "palestra vecchia"])
        self.assertIn("non usato da tempo", chosen[1]["why"])

    def test_pinned_memories_do_not_decay_and_a_cited_key_always_wins(self):
        with self.memory.lock:
            self.memory.connection.execute("UPDATE memories SET pinned = 1 WHERE key = 'palestra vecchia'")
            self.memory.connection.commit()
        self.assertNotIn("non usato da tempo", self.memory.relevant_for("in che palestra vado?")[-1]["why"])
        self.assertEqual(self.memory.relevant_for("dimmi la palestra vecchia")[0]["key"], "palestra vecchia")


if __name__ == "__main__":
    unittest.main()
