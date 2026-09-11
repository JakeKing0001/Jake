"""Test unitari per benchmarks/_report.py: nessuna suite esisteva finora, nessun bug trovato.
Copre solo le funzioni di utilita' pure (percentile/latency_stats/save_report), non i benchmark
veri e propri: quelli restano deliberatamente fuori da tests/ perche' richiedono Ollama/hardware
reale in esecuzione (vedi il docstring del modulo), non perche' non siano stati controllati."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks import _report


class PercentileTests(unittest.TestCase):
    def test_empty_list_returns_zero(self):
        self.assertEqual(_report.percentile([], 50), 0.0)

    def test_the_50th_percentile_of_a_sorted_list(self):
        self.assertEqual(_report.percentile([1, 2, 3, 4, 5], 50), 3)

    def test_the_0th_and_100th_percentiles_are_the_extremes(self):
        values = [5, 1, 3, 2, 4]
        self.assertEqual(_report.percentile(values, 0), 1)
        self.assertEqual(_report.percentile(values, 100), 5)


class LatencyStatsTests(unittest.TestCase):
    def test_empty_list_reports_zero_count(self):
        self.assertEqual(_report.latency_stats([]), {"count": 0})

    def test_computes_mean_and_extremes(self):
        stats = _report.latency_stats([10.0, 20.0, 30.0])
        self.assertEqual(stats["count"], 3)
        self.assertEqual(stats["mean_ms"], 20.0)
        self.assertEqual(stats["min_ms"], 10.0)
        self.assertEqual(stats["max_ms"], 30.0)


class SaveReportTests(unittest.TestCase):
    def test_writes_a_json_report_with_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(_report, "RESULTS_DIR", Path(tmp)):
                path = _report.save_report("bench_prova", {"count": 5})
            self.assertTrue(path.is_file())
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["benchmark"], "bench_prova")
            self.assertEqual(payload["count"], 5)
            self.assertIn("timestamp", payload)
            self.assertIn("platform", payload)


if __name__ == "__main__":
    unittest.main()
