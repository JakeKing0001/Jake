"""F5.1.2: ogni ricordo ha un tipo esplicito - entita', episodio, procedura, record - migrato anche sui database
esistenti e usato dal retrieval: episodi solo per domande sul passato, procedure solo per domande su come si fa.
Database vero."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.memory_manager import MemoryManager
from core.memory_schema import kind_of


class MemoryKindTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "memory.db"
        self.memory = MemoryManager(self.path)
        self.addCleanup(self.memory.close)
        self.memory.remember("sorella", "si chiama Marta", category="fact")
        self.memory.remember("riassunto conversazione del 2026-09-26", "abbiamo parlato del backup del PC",
                             category="summary")
        self.memory.remember("backup serale", '{"steps": ["apri la cartella", "copia su disco esterno"]}',
                             category="workflow")

    def test_every_memory_has_its_kind_in_the_database(self):
        connection = sqlite3.connect(self.path)
        kinds = dict(connection.execute("SELECT category, kind FROM memories").fetchall())
        connection.close()
        self.assertEqual(kinds, {"fact": "entity", "summary": "episode", "workflow": "procedure"})
        self.assertEqual([r["key"] for r in self.memory.recall(kind="procedure")], ["backup serale"])
        self.assertEqual(kind_of("qualcosa di nuovo"), "record")

    def test_procedures_and_episodes_enter_an_answer_only_when_the_question_is_about_them(self):
        def keys(question):
            return [e["key"] for e in self.memory.relevant_for(question)]

        self.assertEqual(keys("mi serve un backup del PC stasera?"), [], "ne' la procedura ne' l'episodio")
        self.assertIn("backup serale", keys("come faccio il backup serale?"))
        self.assertIn("riassunto conversazione del 2026-09-26", keys("di cosa abbiamo parlato l'altra volta sul backup?"))
