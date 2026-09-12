"""Test unitari per la dashboard locale (F0, criterio di uscita "dashboard locale con errori e
latenze" - vedi tools/dashboard.py). Solo le funzioni pure (build_report/render_html): niente
file su disco, ne' apertura del browser."""
import unittest

from tools.dashboard import build_report, render_html


class BuildReportTests(unittest.TestCase):
    def test_counts_successes_and_failures_separately(self):
        actions = [
            {"skill": "OPEN_APP", "result": "success", "duration_ms": 10},
            {"skill": "OPEN_APP", "result": "error:NOT_FOUND", "duration_ms": 20},
            {"skill": "FIND_FILE", "result": "success", "duration_ms": 30},
        ]
        report = build_report(actions, sessions=[])
        self.assertEqual(report["total"], 3)
        self.assertEqual(report["successes"], 2)
        self.assertEqual(report["failures"], 1)

    def test_private_actions_are_excluded_from_visible_stats_but_counted(self):
        """Una riga privata (core/logger.log_action con private=True) non ha skill/result: non
        deve ne' comparire nelle statistiche per skill ne' essere contata come fallimento, ma il
        numero totale di azioni deve comunque riflettere che e' passata di qui."""
        actions = [
            {"private": True},
            {"skill": "OPEN_APP", "result": "success", "duration_ms": 10},
        ]
        report = build_report(actions, sessions=[])
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["private_count"], 1)
        self.assertEqual(report["successes"], 1)
        self.assertEqual(report["failures"], 0)  # non 1: la riga privata non e' un fallimento
        self.assertEqual(len(report["skill_rows"]), 1)

    def test_per_skill_latency_percentiles(self):
        actions = [{"skill": "FIND_FILE", "result": "success", "duration_ms": ms} for ms in [10, 20, 30, 40, 50]]
        report = build_report(actions, sessions=[])
        row = report["skill_rows"][0]
        self.assertEqual(row["skill"], "FIND_FILE")
        self.assertEqual(row["count"], 5)
        self.assertEqual(row["errors"], 0)
        self.assertIsNotNone(row["p50_ms"])
        self.assertIsNotNone(row["p95_ms"])

    def test_verified_field_is_bucketed_into_three_states(self):
        actions = [
            {"skill": "CREATE_PATH", "result": "success", "verified": True},
            {"skill": "CREATE_PATH", "result": "error:VERIFICATION_FAILED", "verified": False},
            {"skill": "ADD_NOTE", "result": "success"},  # verified assente: "non verificato"
        ]
        report = build_report(actions, sessions=[])
        self.assertEqual(report["verified_counts"]["verificato"], 1)
        self.assertEqual(report["verified_counts"]["verifica fallita"], 1)
        self.assertEqual(report["verified_counts"]["non verificato"], 1)

    def test_recent_failures_excludes_successes(self):
        actions = [
            {"skill": "A", "result": "success", "trace_id": "t1"},
            {"skill": "B", "result": "error:X", "trace_id": "t2"},
        ]
        report = build_report(actions, sessions=[])
        self.assertEqual(len(report["recent_failures"]), 1)
        self.assertEqual(report["recent_failures"][0]["trace_id"], "t2")

    def test_empty_input_does_not_crash(self):
        report = build_report([], sessions=[])
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["skill_rows"], [])
        self.assertIsNone(report["overall_p50_ms"])

    def test_failure_category_counts_use_the_shared_taxonomy(self):
        """F1.7.6 ("failure taxonomy"): stessa error_category_of() dell'action ledger (F1.1.4),
        applicata al campo `result` gia' scritto da core/logger.log_action nello stesso formato."""
        actions = [
            {"skill": "ADD_NOTE", "result": "success"},
            {"skill": "CREATE_PATH", "result": "error:VERIFICATION_FAILED"},
            {"skill": "ADD_NOTE", "result": "error:OPERATION_FAILED"},
            {"skill": "OPEN_APP", "result": "error:PATH_NOT_FOUND"},  # codice bespoke, non mappato
        ]
        report = build_report(actions, sessions=[])
        self.assertEqual(report["failure_category_counts"]["success"], 1)
        self.assertEqual(report["failure_category_counts"]["verification_failed"], 1)
        self.assertEqual(report["failure_category_counts"]["transient"], 1)
        self.assertEqual(report["failure_category_counts"]["uncategorized"], 1)


class RenderHtmlTests(unittest.TestCase):
    def test_produces_valid_looking_self_contained_html(self):
        report = build_report(
            [{"skill": "OPEN_APP", "result": "success", "duration_ms": 12.3}], sessions=[],
        )
        from pathlib import Path
        output = render_html(report, Path("data/jake_actions.jsonl"), Path("data/jake_sessions.jsonl"))

        self.assertIn("<!doctype html>", output)
        self.assertIn("OPEN_APP", output)
        # Nessuna dipendenza esterna (CDN, script remoto): Jake e' local-first per principio.
        self.assertNotIn("http://", output)
        self.assertNotIn("https://", output)

    def test_html_escapes_untrusted_skill_names(self):
        """skill/trace_id finiscono nel log strutturato con qualunque stringa passi un chiamante
        (incluso, in teoria, un intent inventato da un plugin di terze parti): devono essere
        scappati, non inseriti a crudo nell'HTML."""
        report = build_report(
            [{"skill": "<script>alert(1)</script>", "result": "error:X", "trace_id": "t1"}], sessions=[],
        )
        from pathlib import Path
        output = render_html(report, Path("x"), Path("y"))

        self.assertNotIn("<script>alert(1)</script>", output)
        self.assertIn("&lt;script&gt;", output)

    def test_failure_category_section_is_rendered(self):
        report = build_report(
            [{"skill": "ADD_NOTE", "result": "error:OPERATION_FAILED", "duration_ms": 5}], sessions=[],
        )
        from pathlib import Path
        output = render_html(report, Path("x"), Path("y"))

        self.assertIn("Categoria di errore", output)
        self.assertIn("transient", output)


if __name__ == "__main__":
    unittest.main()
