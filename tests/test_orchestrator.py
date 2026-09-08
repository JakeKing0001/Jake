"""Test unitari per l'instradamento multi-agente (v5.0/5.1/5.2, core/orchestrator.py). Agenti
finti: qui si testa solo la LOGICA DI SCELTA, non l'esecuzione vera di TaskAgent (gia' coperta
da tests/test_agent.py)."""
import unittest

from core.orchestrator import JakeOrchestrator


class FakeAgent:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def run(self, request, history=None):
        self.calls.append((request, history))
        return f"outcome-di-{self.name}"


class PickAgentTests(unittest.TestCase):
    def setUp(self):
        self.general = FakeAgent("generale")
        self.coding = FakeAgent("coding")
        self.research = FakeAgent("ricerca")
        self.orchestrator = JakeOrchestrator(self.general, self.coding, self.research)

    def test_coding_request_picks_the_coding_agent(self):
        agent = self.orchestrator.pick_agent("fai il commit delle modifiche su git")
        self.assertIs(agent, self.coding)

    def test_python_script_request_picks_the_coding_agent(self):
        agent = self.orchestrator.pick_agent("esegui questo script python e dimmi l'output")
        self.assertIs(agent, self.coding)

    def test_research_request_picks_the_research_agent(self):
        agent = self.orchestrator.pick_agent("cerca online le ultime notizie sul cambiamento climatico")
        self.assertIs(agent, self.research)

    def test_generic_request_picks_the_general_agent(self):
        agent = self.orchestrator.pick_agent("apri spotify e metti in pausa la musica")
        self.assertIs(agent, self.general)

    def test_coding_signal_wins_when_both_patterns_match(self):
        agent = self.orchestrator.pick_agent("cerca online il bug nel mio script python")
        self.assertIs(agent, self.coding)


class RunDelegatesToPickedAgentTests(unittest.TestCase):
    def test_run_forwards_request_and_history_to_the_chosen_agent(self):
        general, coding, research = FakeAgent("g"), FakeAgent("c"), FakeAgent("r")
        orchestrator = JakeOrchestrator(general, coding, research)
        history = [{"role": "user", "text": "ciao"}]

        result = orchestrator.run("fai un commit git", history=history)

        self.assertEqual(coding.calls, [("fai un commit git", history)])
        self.assertEqual(general.calls, [])
        self.assertEqual(research.calls, [])
        self.assertEqual(result, "outcome-di-c")


if __name__ == "__main__":
    unittest.main()
