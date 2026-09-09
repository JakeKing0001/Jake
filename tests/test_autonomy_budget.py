"""Test unitari per core/autonomy_budget.py (F6, Proactive Intelligence & Autonomy - vedi
ROADMAP.md). time_source e' sempre un orologio finto controllato dal test: nessuna vera attesa."""
import unittest

from core.autonomy_budget import AutonomyBudget


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class AutonomyBudgetTests(unittest.TestCase):
    def test_starts_not_exceeded_with_full_remaining_budget(self):
        budget = AutonomyBudget(max_actions=3, window_seconds=3600, time_source=FakeClock())
        self.assertFalse(budget.is_exceeded())
        self.assertEqual(budget.remaining(), 3)

    def test_is_exceeded_after_max_actions_recorded(self):
        clock = FakeClock()
        budget = AutonomyBudget(max_actions=2, window_seconds=3600, time_source=clock)

        budget.record()
        budget.record()

        self.assertTrue(budget.is_exceeded())
        self.assertEqual(budget.remaining(), 0)

    def test_not_exceeded_one_below_the_limit(self):
        clock = FakeClock()
        budget = AutonomyBudget(max_actions=3, window_seconds=3600, time_source=clock)

        budget.record()
        budget.record()

        self.assertFalse(budget.is_exceeded())
        self.assertEqual(budget.remaining(), 1)

    def test_old_actions_roll_out_of_the_window(self):
        """Una finestra SCORREVOLE, non un contatore che si azzera a un orario fisso: un'azione
        registrata piu' di window_seconds fa non conta piu'."""
        clock = FakeClock()
        budget = AutonomyBudget(max_actions=2, window_seconds=60, time_source=clock)

        budget.record()
        clock.advance(61)
        budget.record()

        self.assertFalse(budget.is_exceeded())
        self.assertEqual(budget.remaining(), 1)

    def test_partial_window_expiry_frees_exactly_one_slot(self):
        clock = FakeClock()
        budget = AutonomyBudget(max_actions=2, window_seconds=60, time_source=clock)

        budget.record()
        clock.advance(30)
        budget.record()
        self.assertTrue(budget.is_exceeded())

        clock.advance(31)  # la prima azione (a t=0) e' fuori dalla finestra di 60s, la seconda (t=30) no
        self.assertFalse(budget.is_exceeded())
        self.assertEqual(budget.remaining(), 1)

    def test_reset_clears_the_window_immediately(self):
        clock = FakeClock()
        budget = AutonomyBudget(max_actions=1, window_seconds=3600, time_source=clock)
        budget.record()
        self.assertTrue(budget.is_exceeded())

        budget.reset()

        self.assertFalse(budget.is_exceeded())
        self.assertEqual(budget.remaining(), 1)


if __name__ == "__main__":
    unittest.main()
