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


class SharedBundlePrivacyTests(unittest.TestCase):
    """Baseline pre-sperimentazione: il bundle si condivide, quindi niente credenziali, utente o testo."""

    def test_config_credentials_are_redacted_but_their_presence_is_visible(self):
        from tools.diagnostic_bundle import redact_config

        redacted = redact_config({"admin_passphrase": "dpapi:1:abc", "home_assistant_token": "", "news_api_key": "k",
                                  "ollama_model": "qwen2.5:7b", "hud_hotkey": "ctrl+shift+j"})
        self.assertEqual(redacted, {"admin_passphrase": "<redacted>", "home_assistant_token": "",
                                    "news_api_key": "<redacted>", "ollama_model": "qwen2.5:7b",
                                    "hud_hotkey": "ctrl+shift+j"})

    def test_ledger_user_is_redacted_and_summary_classifies_errors_and_timings(self):
        actions = [
            {"ts": 1, "kind": "state", "state": "LISTENING"},
            {"ts": 2, "kind": "turn", "trace_id": "t1", "route": "exact", "intent": "GET_TIME", "outcome": "ok",
             "timings_ms": {"routing": 2.0, "total": 40.0}},
            {"ts": 3, "kind": "turn", "trace_id": "t2", "route": "llm", "outcome": "ok", "error": "model_timeout",
             "timings_ms": {"routing": 6000.0, "total": 6100.0}, "memory_used": True},
            {"ts": 4, "kind": "speech", "trace_id": "t2", "tts_ms": 900.0},
            {"ts": 5, "kind": "turn", "private": True},
        ]
        bundle = build_bundle(actions, [{"intent": "OPEN_APP", "windows_user": "mario"}], [])
        self.assertEqual(bundle["ledger"][0]["windows_user"], "<redacted>")
        summary = bundle["summary"]
        self.assertEqual((summary["turns"], summary["private_turns"], summary["memory_used_turns"]), (2, 1, 1))
        self.assertEqual(summary["routes"], {"exact": 1, "llm": 1})
        self.assertEqual([e["error"] for e in summary["errors"]], ["model_timeout"])
        self.assertEqual(summary["timings_ms"]["tts"]["n"], 1)
        self.assertEqual(summary["state_timeline"][0]["state"], "LISTENING")


if __name__ == "__main__":
    unittest.main()
