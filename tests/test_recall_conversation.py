"""F5.2 (memoria episodica): "di cosa abbiamo parlato ieri?" legge la cronologia reale (database temporaneo, turni
sintetici con date spostate) per periodo e argomento."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.memory_manager import MemoryManager
from core.response_formatter import format_skill_result
from skills.recall_conversation import RecallConversationSkill


class RecallConversationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.memory = MemoryManager(Path(tmp.name) / "memory.db")
        self.addCleanup(self.memory.close)
        for text, role in (("orari della palestra", "user"), ("La palestra apre alle 7.", "jake"),
                           ("che tempo fa", "user"), ("promemoria per il dentista", "user")):
            self.memory.log_turn(role, text)
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).replace(hour=10, minute=15)
        with self.memory.lock:
            self.memory.connection.execute("UPDATE conversation_history SET created_at = ? WHERE text != ?",
                                           (yesterday.isoformat(), "promemoria per il dentista"))
            self.memory.connection.commit()

    def test_yesterday_lists_the_user_requests_of_yesterday_only(self):
        result = RecallConversationSkill(self.memory).execute({"when": "ieri"})
        self.assertEqual([i["text"] for i in result.data["items"]], ["orari della palestra", "che tempo fa"])
        reply = format_skill_result("RECALL_CONVERSATION", result)
        self.assertTrue(reply.startswith('Ieri mi hai chiesto: alle '), reply)
        self.assertNotIn("dentista", reply)

    def test_topic_and_empty_period(self):
        result = RecallConversationSkill(self.memory).execute({"when": "ieri", "topic": "palestra"})
        self.assertEqual([i["text"] for i in result.data["items"]], ["orari della palestra"])
        empty = RecallConversationSkill(self.memory).execute({"when": "la settimana scorsa"})
        self.assertEqual(format_skill_result("RECALL_CONVERSATION", empty),
                         'Non trovo conversazioni nostre per "la settimana scorsa".')


if __name__ == "__main__":
    unittest.main()
