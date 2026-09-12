"""Test unitari per il bundle diagnostico redatto (F1.7.7, vedi tools/diagnostic_bundle.py).
Solo le funzioni pure (_load_jsonl/build_bundle): niente file veri di produzione, niente
scrittura su disco al di fuori di una cartella temporanea."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.diagnostic_bundle import _load_jsonl, build_bundle


class LoadJsonlTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.path = Path(self._tmpdir.name) / "records.jsonl"

    def test_missing_file_returns_empty_list(self):
        self.assertEqual(_load_jsonl(self.path, limit=10), [])

    def test_malformed_lines_are_skipped_not_fatal(self):
        self.path.write_text('{"a": 1}\nnon e json valido\n{"a": 2}\n', encoding="utf-8")

        records = _load_jsonl(self.path, limit=10)

        self.assertEqual(records, [{"a": 1}, {"a": 2}])

    def test_only_the_most_recent_lines_up_to_limit_are_kept(self):
        lines = "\n".join(json.dumps({"n": i}) for i in range(10))
        self.path.write_text(lines, encoding="utf-8")

        records = _load_jsonl(self.path, limit=3)

        self.assertEqual(records, [{"n": 7}, {"n": 8}, {"n": 9}])

    def test_limit_zero_means_all_lines(self):
        lines = "\n".join(json.dumps({"n": i}) for i in range(5))
        self.path.write_text(lines, encoding="utf-8")

        records = _load_jsonl(self.path, limit=0)

        self.assertEqual(len(records), 5)


class BuildBundleTests(unittest.TestCase):
    def test_includes_version_and_platform_metadata(self):
        from core.version import PROTOCOL_VERSION, VERSION

        bundle = build_bundle([], [], [])

        self.assertEqual(bundle["jake_version"], VERSION)
        self.assertEqual(bundle["protocol_version"], PROTOCOL_VERSION)
        self.assertTrue(bundle["python_version"])
        self.assertTrue(bundle["platform"])

    def test_actions_and_ledger_pass_through_unchanged(self):
        """F1.7.7: ne' i record di log_action ne' quelli del ledger contengono mai parametri
        veri per costruzione (vedi core/logger.py/core/action_ledger.py) - nulla da redigere."""
        actions = [{"skill": "OPEN_APP", "result": "success"}]
        ledger = [{"intent": "OPEN_APP", "result": "success"}]

        bundle = build_bundle(actions, ledger, [])

        self.assertEqual(bundle["actions"], actions)
        self.assertEqual(bundle["ledger"], ledger)

    def test_session_parameters_are_redacted_by_default(self):
        """Buco reale che questo strumento evita di introdurre: jake_sessions.jsonl PUO'
        contenere parametri verbatim (session_recording_verbatim, una scelta locale di debug) -
        un bundle pensato per la condivisione non deve propagarli senza un opt-in esplicito."""
        sessions = [{"intent": "ADD_NOTE", "parameters": {"text": "appunto privato davvero"}, "verbatim": True}]

        bundle = build_bundle([], [], sessions)

        self.assertEqual(bundle["sessions"][0]["parameters"], {"text": "<str:23 caratteri>"})
        self.assertNotIn("appunto privato davvero", json.dumps(bundle))

    def test_already_redacted_session_parameters_stay_redacted(self):
        sessions = [{"intent": "ADD_NOTE", "parameters": {"text": "<str:10 caratteri>"}, "verbatim": False}]

        bundle = build_bundle([], [], sessions)

        self.assertEqual(bundle["sessions"][0]["parameters"], {"text": "<str:18 caratteri>"})

    def test_include_verbatim_sessions_opt_in_skips_re_redaction(self):
        sessions = [{"intent": "ADD_NOTE", "parameters": {"text": "appunto privato davvero"}, "verbatim": True}]

        bundle = build_bundle([], [], sessions, include_verbatim_sessions=True)

        self.assertEqual(bundle["sessions"][0]["parameters"], {"text": "appunto privato davvero"})

    def test_session_record_without_parameters_is_left_untouched(self):
        sessions = [{"intent": "GET_TIME", "error": "OPERATION_FAILED"}]

        bundle = build_bundle([], [], sessions)

        self.assertEqual(bundle["sessions"], sessions)


if __name__ == "__main__":
    unittest.main()
