"""Il registro dei 100 task del benchmark F3 resta coerente con i test reali che lo verificano:
un test rinominato o rimosso deve far fallire QUESTO test, non sparire in silenzio dal benchmark."""
import importlib
import unittest

from benchmarks.bench_computer_use_tasks import summarize
from benchmarks.computer_use_tasks import KINDS, TASKS


class TaskRegistryTests(unittest.TestCase):
    def test_exactly_the_tasks_one_to_one_hundred(self):
        self.assertEqual([task.number for task in TASKS], list(range(1, 101)))

    def test_every_task_has_a_valid_kind_and_at_least_one_test(self):
        for task in TASKS:
            with self.subTest(task=task.number):
                self.assertIn(task.kind, KINDS)
                self.assertTrue(task.tests)
                self.assertTrue(task.title.strip())

    def test_every_referenced_test_really_exists(self):
        for task in TASKS:
            for test_id in task.tests:
                with self.subTest(task=task.number, test=test_id):
                    module_name, class_name, method_name = test_id.rsplit(".", 2)
                    cls = getattr(importlib.import_module(module_name), class_name)
                    self.assertTrue(callable(getattr(cls, method_name, None)))

    def test_no_test_is_counted_for_two_tasks(self):
        seen = {}
        for task in TASKS:
            for test_id in task.tests:
                self.assertNotIn(test_id, seen, f"{test_id} gia' usato dal task {seen.get(test_id)}")
                seen[test_id] = task.number


class SummaryTests(unittest.TestCase):
    def _result(self, number, status):
        return {"task": number, "title": "t", "status": status, "success": status == "passed", "diagnosis": []}

    def test_limitations_and_skips_are_not_successes(self):
        results = [self._result(n, "passed") for n in range(1, 91)]
        results += [self._result(n, "limitation") for n in range(91, 96)]
        results += [self._result(n, "skipped") for n in range(96, 101)]
        summary = summarize(results)
        self.assertEqual(summary["succeeded"], 90)
        self.assertEqual(summary["gate"], "PASS")
        results[0] = self._result(1, "failed")
        self.assertEqual(summarize(results)["gate"], "FAIL")

    def test_a_partial_run_never_claims_the_gate(self):
        self.assertEqual(summarize([self._result(1, "passed")])["gate"], "PARTIAL")


if __name__ == "__main__":
    unittest.main()
