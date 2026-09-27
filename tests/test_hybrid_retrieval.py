"""F5.5 (retrieval ibrido): la similarita' semantica entrava solo se le parole non trovavano nulla, quindi una parola
in comune con un ricordo sbagliato nascondeva quello giusto per significato. Memoria reale su database temporaneo;
embedding a mano (due dimensioni) al posto del modello, per un risultato deterministico."""
import tempfile
import unittest
from pathlib import Path

from core.memory_manager import MemoryManager


class HybridRetrievalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.memory = MemoryManager(Path(tmp.name) / "memory.db")
        self.addCleanup(self.memory.close)
        self.memory.remember("tessera della palestra", "numero 4412", embedding=[1.0, 0.0])
        self.memory.remember("abbonamento mezzi", "valido fino a marzo", embedding=[0.0, 1.0])

    def test_the_memory_right_by_meaning_is_not_hidden_by_a_word_in_common(self):
        chosen = self.memory.relevant_for("la tessera dei treni vale ancora?", query_embedding=[0.1, 0.99])
        self.assertEqual(chosen[0]["key"], "abbonamento mezzi")
        self.assertIn("simile per significato", chosen[0]["why"])

    def test_words_and_meaning_together_rank_highest(self):
        chosen = self.memory.relevant_for("il mio abbonamento dei mezzi", query_embedding=[0.0, 1.0])
        self.assertEqual(chosen[0]["key"], "abbonamento mezzi")
        self.assertIn("parole in comune e simile per significato", chosen[0]["why"])


if __name__ == "__main__":
    unittest.main()
