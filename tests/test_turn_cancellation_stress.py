"""Stress della cancellazione del turno vocale: 54 iterazioni di benchmarks/bench_turn_cancellation.py
(9 per ciascuno dei 6 scenari: modello, visione, skill, TaskAgent, PlanExecutor, voce), con tempi di
stop variati in modo deterministico. Ogni iterazione verifica: secondo comando eseguito una volta,
mai due answer() insieme, risultato vecchio scartato, nessun effetto tardivo, KillSwitch spento,
nessun ContextVar fuori dal turno, parlante/confidenza del turno giusto, nessun thread rimasto vivo."""
import unittest

from benchmarks.bench_turn_cancellation import SCENARIOS, run


class TurnCancellationStressTests(unittest.TestCase):
    def test_fifty_four_cancellations_across_every_scenario_are_all_clean(self):
        report = run(iterations=54, seed=20260926)
        self.assertEqual(report["iterations"], 54)
        self.assertEqual(set(report["by_scenario"]), set(SCENARIOS))
        self.assertTrue(all(row["iterations"] == 9 for row in report["by_scenario"].values()))
        self.assertEqual(report["failures"], [], "iterazioni fallite")


if __name__ == "__main__":
    unittest.main()
