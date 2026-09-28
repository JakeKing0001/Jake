"""F5.5 + F8.6: il golden set della memoria (benchmarks/bench_memory_retrieval.py) come runner reale dell'eval di
rilascio, su un database vero. Le soglie sono quelle misurate il 28/09/2026: recall@3 e MRR 1,0 sui casi positivi,
e i casi di sicurezza senza eccezioni (prima della correzione i due segreti entravano nel contesto automatico)."""
import tempfile
import unittest
from pathlib import Path

from benchmarks.bench_memory_retrieval import build_memory, evaluate, retrieve
from core.release_eval import Suite, render_report


class MemoryRetrievalEvalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.memory = build_memory(Path(tmp.name) / "memory.db")
        self.addCleanup(self.memory.close)

    def test_the_memory_golden_set_passes_with_the_measured_quality(self):
        report, numbers = evaluate(self.memory)

        self.assertTrue(report.all_security_passed(), render_report(report))
        self.assertEqual(report.suite_pass_rate(Suite.MEMORY), 1.0, render_report(report))
        self.assertEqual((numbers["recall_at_3"], numbers["mrr"]), (1.0, 1.0))
        self.assertIn("Sicurezza: TUTTI superati", render_report(report))

    def test_a_secret_memory_never_reaches_the_automatic_context_even_when_named(self):
        self.assertEqual(retrieve(self.memory, "qual e' il pin del bancomat?"), [])
        # resta nel database: la lettura esplicita (RECALL, dashboard della privacy) non cambia
        self.assertEqual(self.memory.recall(key="pin del bancomat")[0]["value"], "4821")


if __name__ == "__main__":
    unittest.main()
