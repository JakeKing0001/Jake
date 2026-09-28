"""F5.4 (consolidamento): lo stesso ricordo con la chiave scritta in modo diverso non diventa piu' ricordi separati.

Riprodotto prima della correzione: "caffe", "Caffè" e "caffè " erano tre righe con tre valori, le risposte le
ricevevano tutte e il versionamento (niente sovrascritture silenziose) non scattava mai; "dimentica il Caffè" non
toccava "caffe". Database vero."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.memory_manager import MemoryManager


class MemoryConsolidationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "memory.db"
        self.memory = MemoryManager(self.path)
        self.addCleanup(self.memory.close)

    def test_the_same_key_written_differently_is_one_memory_with_versions(self):
        self.memory.remember("caffe", "lo prendo amaro", category="preference")
        self.assertEqual(self.memory.remember("Caffè", "amaro senza zucchero", category="preference")["status"], "updated")
        self.memory.remember("caffè ", "amaro", category="preference")

        self.assertEqual([(r["key"], r["value"]) for r in self.memory.recall(category="preference")], [("caffe", "amaro")])
        self.assertEqual([v["value"] for v in self.memory.versions("CAFFÈ", "preference")],
                         ["amaro senza zucchero", "lo prendo amaro"])
        self.assertEqual([e["key"] for e in self.memory.relevant_for("come prendo il caffè?")], ["caffe"])
        self.assertEqual(self.memory.entry("Caffè", "preference")["value"], "amaro",
                         "la conferma prima di cambiare un ricordo importante lo trova anche scritto diversamente")

    def test_forgetting_works_whatever_the_spelling(self):
        self.memory.remember("caffe", "amaro", category="preference")
        self.assertTrue(self.memory.forget("il caffè".replace("il ", "")))
        self.assertEqual(self.memory.recall(category="preference"), [])

    def test_duplicates_already_in_an_old_database_are_merged_keeping_the_values_as_versions(self):
        self.memory.remember("sorella", "si chiama Marta", category="fact")
        self.memory.remember("lavoro", "sviluppatore", category="fact")
        self.memory.link("sorella", "fact", "lavora_come", "lavoro", "fact")
        # un database nato prima della chiave canonica: righe scritte direttamente, con date diverse
        connection = sqlite3.connect(self.path)
        connection.execute("INSERT INTO memories (key, value, category, importance, created_at, updated_at, source) "
                           "VALUES ('Sorella ', 'vive a Bologna', 'fact', 1, '2030-01-01T00:00:00+00:00', "
                           "'2030-01-01T00:00:00+00:00', 'user')")
        connection.commit()
        connection.close()

        self.assertEqual(self.memory.consolidate_duplicates(), 1)

        facts = {r["key"]: r["value"] for r in self.memory.recall(category="fact")}
        self.assertEqual(facts, {"Sorella ": "vive a Bologna", "lavoro": "sviluppatore"}, "resta il piu' recente")
        self.assertEqual([v["value"] for v in self.memory.versions("Sorella ", "fact")], ["si chiama Marta"])
        self.assertEqual([r["key"] for r in self.memory.related("Sorella ", "fact")], ["lavoro"],
                         "i collegamenti passano al superstite")
        self.assertEqual(self.memory.consolidate_duplicates(), 0)


if __name__ == "__main__":
    unittest.main()
