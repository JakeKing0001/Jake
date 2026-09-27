"""F5.5 (retrieval temporale): "cosa ti ho detto ieri sulla palestra?" - l'espressione temporale dentro una domanda
qualsiasi restringe i ricordi pertinenti al periodo (e non viene cercata come parola). Memoria reale su database
temporaneo, date spostate sul database."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.memory_manager import MemoryManager
from core.temporal_parser import find_relative_range

NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


class FindRelativeRangeTests(unittest.TestCase):
    def test_the_expression_is_found_inside_a_question(self):
        (since, until), phrase = find_relative_range("cosa ti ho detto ieri sulla palestra?", NOW)
        self.assertEqual((phrase, since[:10], until[:10]), ("ieri", "2026-09-26", "2026-09-27"), "fine esclusiva: mezzanotte di oggi")
        self.assertEqual(find_relative_range("e la settimana scorsa?", NOW)[1], "la settimana scorsa")
        self.assertEqual(find_relative_range("negli ultimi 3 giorni cosa e' successo", NOW)[1], "negli ultimi 3 giorni")
        self.assertIsNone(find_relative_range("dimmi la palestra", NOW))
        self.assertIsNone(find_relative_range("il giornale di oggiani", NOW), "solo parole intere")


class TemporalRelevantForTests(unittest.TestCase):
    def test_yesterday_limits_the_memories_to_yesterday(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = MemoryManager(Path(tmp.name) / "memory.db")
        self.addCleanup(memory.close)
        memory.remember("palestra nuova", "iscritto alla palestra di via Milano")
        memory.remember("palestra vecchia", "la palestra di via Roma chiude")
        now = datetime.now(timezone.utc)
        with memory.lock:
            for key, days in (("palestra nuova", 1), ("palestra vecchia", 10)):
                memory.connection.execute("UPDATE memories SET updated_at = ? WHERE key = ?",
                                          ((now - timedelta(days=days)).replace(hour=12).isoformat(), key))
            memory.connection.commit()

        chosen = memory.relevant_for("cosa ti ho detto ieri sulla palestra?")
        self.assertEqual([e["key"] for e in chosen], ["palestra nuova"])
        self.assertIn("detto ieri", chosen[0]["why"])
        self.assertEqual({e["key"] for e in memory.relevant_for("cosa sai della palestra?")},
                         {"palestra nuova", "palestra vecchia"}, "senza tempo nella domanda, nessun filtro")


if __name__ == "__main__":
    unittest.main()
