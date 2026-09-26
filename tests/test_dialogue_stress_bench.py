"""benchmarks/bench_dialogue_stress.py: almeno 50 casi deterministici, zero errori, zero riesecuzioni
non sicure; e il benchmark sa davvero riconoscere un errore o una riesecuzione non sicura."""
import unittest

from benchmarks.bench_dialogue_stress import CASES, DECLINED, Case, _outcome, run


class DialogueStressBenchTests(unittest.TestCase):
    def test_every_case_is_right_at_the_first_try(self):
        report = run()
        self.assertGreaterEqual(report["cases"], 50)
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["unsafe_reruns"], 0)
        self.assertEqual(report["first_try_correct"], report["cases"])

    def test_every_required_category_is_covered(self):
        categories = {case.category for case in CASES}
        self.assertEqual(categories, {
            "ellipsis", "pronouns", "ordinals", "corrections", "intent_change", "stop", "auth", "numbers",
            "tech_terms", "proper_names", "correction_safety", "correction_scope",
        })
        for category in categories:
            with self.subTest(category=category):
                self.assertGreaterEqual(sum(case.category == category for case in CASES), 4)

    def test_the_benchmark_flags_unsafe_reruns_and_wrong_answers(self):
        unsafe = Case("correction_safety", "finto", lambda: "rerun", "ask", correction=True)
        self.assertEqual(_outcome(unsafe, "rerun"), "unsafe_rerun")
        self.assertEqual(_outcome(unsafe, "undo_then_rerun"), "unsafe_rerun")
        guessed = Case("ellipsis", "finto", lambda: None, DECLINED)
        self.assertEqual(_outcome(guessed, ("GET_WEATHER", {"city": "Milano"})), "error")
        self.assertEqual(_outcome(guessed, DECLINED), "declined")


if __name__ == "__main__":
    unittest.main()
