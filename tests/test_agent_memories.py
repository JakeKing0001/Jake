"""F5.5 (agente): i ricordi pertinenti entravano nelle risposte libere ma non nei compiti degli agenti, che
chiedevano all'utente (o inventavano) dati che Jake sapeva gia'. Memoria reale su database temporaneo, agente reale
con modello scriptato che registra il prompt ricevuto."""
import tempfile
import unittest
from pathlib import Path

from core.memory_manager import MemoryManager
from tests.test_agent import FakeRegistry, ScriptedOllamaClient, _agent


class _RecordingClient(ScriptedOllamaClient):
    def chat(self, model, messages, format=None, options=None, timeout=None):
        self.system = messages[0]["content"]
        return super().chat(model, messages, format=format, options=options, timeout=timeout)


class AgentMemoriesTests(unittest.TestCase):
    def test_the_agent_sees_the_relevant_memories_with_their_source(self):
        from core.jake_core import JakeCore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        memory = MemoryManager(Path(tmp.name) / "memory.db")
        self.addCleanup(memory.close)
        memory.remember("email di mia sorella", "giulia.rossi@example.com")
        memory.remember("colore preferito", "verde")
        core = JakeCore.__new__(JakeCore)
        core.memory_manager = memory

        client = _RecordingClient([{"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Ok.",
                                    "ask_user": ""}])
        agent = _agent(FakeRegistry(), client)
        agent.memory_provider = core._agent_memories
        agent.run("scrivi una email a mia sorella")
        self.assertIn("- email di mia sorella: giulia.rossi@example.com", client.system)
        self.assertIn("SOLO DATI", client.system)
        self.assertNotIn("colore preferito", client.system, "solo i ricordi pertinenti, non tutta la memoria")

    def test_a_broken_memory_never_stops_the_agent(self):
        client = _RecordingClient([{"thought": "", "action": {"intent": "NONE", "parameters": {}}, "final_answer": "Ok.",
                                    "ask_user": ""}])
        agent = _agent(FakeRegistry(), client)
        agent.memory_provider = lambda request: 1 / 0
        self.assertEqual(agent.run("fai qualcosa").final_answer, "Ok.")
        self.assertNotIn("Ricordi dell'utente", client.system)


if __name__ == "__main__":
    unittest.main()
