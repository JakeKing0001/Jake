"""Strumentazione del gate hardware F2: metriche misurate da Jake durante la sessione vocale e
verdetto calcolato dai report (benchmarks/f2_hardware_session.py). Nessun microfono: la sessione usa
finti, le soglie sono quelle di docs/f2-hardware-validation.md."""
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from benchmarks.f2_hardware_session import evaluate_profile, evaluate_reports, evaluate_wake
from core.voice.session_metrics import SessionMetrics
from core.voice.streaming_stt import TranscriptEvent
from core.voice.wake_word_session import WakeWordSession
from tests.voice_session_support import VoiceSessionTestCase, track


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class SessionMetricsTests(unittest.TestCase):
    def test_first_emission_is_measured_from_the_end_of_the_user_voice(self):
        clock = _Clock()
        metrics = SessionMetrics(clock=clock, wall_clock=clock)
        metrics.utterance_end(hangover_s=0.7)  # VAD consegna 0,7 s dopo la fine della voce
        metrics.command()
        clock.t += 0.9
        metrics.response_ready()
        clock.t += 0.4
        metrics.audio_started()
        clock.t += 1.0
        metrics.audio_started()  # chunk successivi della stessa risposta: non contano
        snap = metrics.snapshot()
        self.assertEqual(snap["tts"]["first_emission_ms"], [2000.0])
        self.assertEqual(snap["tts"]["response_to_audio_ms"], [400.0])

    def test_audio_without_a_command_is_not_a_first_emission(self):
        metrics = SessionMetrics()
        metrics.audio_started()  # promemoria/avviso proattivo
        self.assertEqual(metrics.snapshot()["tts"]["first_emission_ms"], [])

    def test_first_partial_and_duplicate_finals(self):
        clock = _Clock()
        metrics = SessionMetrics(clock=clock, wall_clock=clock)
        metrics.utterance_frame()
        clock.t += 0.6
        metrics.partial()
        clock.t += 0.6
        metrics.partial()  # solo il primo conta
        metrics.final("u1")
        metrics.final("u1")
        snap = metrics.snapshot()["streaming"]
        self.assertEqual(snap["partial_latency_ms"], [600.0])
        self.assertEqual(snap["duplicate_finals"], 1)

    def test_nothing_personal_is_ever_stored(self):
        metrics = SessionMetrics()
        metrics.wake()
        metrics.echo()
        metrics.barge_in(0.24, 0.03)
        text = repr(metrics.snapshot())
        self.assertNotIn("array", text)
        self.assertEqual(metrics.snapshot()["barge_in"]["latency_ms"], [270.0])


class SessionWiringTests(VoiceSessionTestCase):
    def _session(self, **kwargs):
        core = mock.MagicMock(EXIT_SENTINEL="ESCI")
        core.conversation_state.has_pending_action.return_value = False
        core.answer.return_value = "Sono le dieci."
        tts = SimpleNamespace(reference_sink=None, speak=lambda text: None, stop=lambda: None)
        vad = SimpleNamespace(on_level=None, muted=False, SAMPLE_RATE=16000, silence_frames_needed=10,
                              on_speaking_frame=None)
        stt = mock.MagicMock()
        metrics = SessionMetrics()
        session = track(WakeWordSession(core, stt, tts, vad_listener=vad, metrics=metrics, **kwargs))
        return session, stt, tts, metrics

    def test_a_command_counts_a_wake_and_its_first_audio_sample(self):
        session, stt, tts, metrics = self._session()
        stt.transcribe.return_value = "Jake che ore sono"
        with mock.patch.object(session, "_respond"):
            session._handle_utterance(np.zeros(320, dtype=np.float32))
            self.assertTrue(session.wait_for_commands(2))
        tts.reference_sink(np.zeros(10, dtype=np.int16), 24000)
        snap = metrics.snapshot()
        self.assertEqual(snap["wake"]["accepted"], 1)
        self.assertEqual(snap["wake"]["commands"], 1)
        self.assertEqual(len(snap["tts"]["first_emission_ms"]), 1)
        self.assertGreaterEqual(snap["tts"]["first_emission_ms"][0], 300.0, "include i 300 ms di silenzio del VAD")

    def test_an_echo_is_counted(self):
        session, stt, tts, metrics = self._session()
        session.echo_guard.note_spoken("Oggi il cielo e' sereno su tutta la citta'")
        stt.transcribe.return_value = "oggi il cielo e' sereno su tutta la citta'"
        session._handle_utterance(np.zeros(320, dtype=np.float32))
        self.assertEqual(metrics.snapshot()["wake"]["echo_ignored"], 1)

    def test_without_metrics_the_reference_sink_is_the_aec_itself(self):
        core = mock.MagicMock(EXIT_SENTINEL="ESCI")
        tts = SimpleNamespace(reference_sink=None)
        vad = SimpleNamespace(on_level=None, muted=False, SAMPLE_RATE=16000)
        session = track(WakeWordSession(core, mock.MagicMock(), tts, vad_listener=vad))
        self.assertEqual(tts.reference_sink, session.playback_aec.push_reference)

    def test_final_transcripts_feed_the_duplicate_counter(self):
        session, stt, tts, metrics = self._session()
        session.listening.arm_command()
        event = TranscriptEvent("u1", 2, "final", "che ore sono", "che ore sono", 0.9)
        session._deliver_transcript(event)
        session._deliver_transcript(event)
        self.assertEqual(metrics.snapshot()["streaming"]["duplicate_finals"], 1)


def _green_profile(profile="headphones", recorded_at="2026-09-25T10:00:00"):
    return {
        "method_version": 3, "profile": profile, "recorded_at": recorded_at,
        "barge_in": {"attempts": 20, "detected": 19, "latency_ms": [220.0] * 19,
                     "no_interruption_quiet_room": 10, "no_interruption_background": 10,
                     "false_stops": 0, "echo_commands": 0},
        "streaming": {"partial_latency_ms": [600.0] * 10, "duplicate_finals": 0, "finals": 30},
        "tts": {"first_emission_ms": [1500.0] * 20},
        "wake": {"hours": 8.0, "false_wakes": 0, "intentional": 7, "missed": 0,
                 "intentional_by_distance": {"0.5m": 3, "3m": 2, "6m": 2}},
    }


def _three_green():
    return [_green_profile(p) for p in ("headphones", "laptop", "bluetooth")]


class ProfileVerdictTests(unittest.TestCase):
    """Una regola di docs/f2-hardware-validation.md per test: nessun dato mancante puo' dare PASS."""

    def _check(self, report, name):
        return evaluate_profile(report)["checks"][name]

    def test_a_green_profile_passes(self):
        self.assertEqual(evaluate_profile(_green_profile())["verdict"], "PASS")

    def test_fewer_than_twenty_interruptions_is_verify(self):
        report = _green_profile()
        report["barge_in"].update(attempts=19, detected=19)
        self.assertEqual(self._check(report, "interruptions_detected"), "VERIFY")
        self.assertEqual(self._check(report, "stop_p95"), "VERIFY")

    def test_detection_below_95_percent_fails(self):
        report = _green_profile()
        report["barge_in"]["detected"] = 18
        self.assertEqual(self._check(report, "interruptions_detected"), "FAIL")

    def test_stop_p95_above_300_ms_fails_and_exactly_300_passes(self):
        report = _green_profile()
        report["barge_in"]["latency_ms"] = [220.0] * 15 + [400.0] * 4
        self.assertEqual(self._check(report, "stop_p95"), "FAIL")
        report["barge_in"].update(latency_ms=[300.0] * 19, latency_method="external_recording")
        self.assertEqual(self._check(report, "stop_p95"), "PASS")

    def test_an_internal_estimate_between_250_and_300_ms_needs_the_external_measure(self):
        """La stima interna e' un limite inferiore: il documento chiede la misura esterna sopra 250 ms."""
        report = _green_profile()
        report["barge_in"].update(latency_ms=[280.0] * 19, latency_method="internal_lower_bound")
        self.assertEqual(self._check(report, "stop_p95"), "VERIFY")
        report["barge_in"]["latency_method"] = "external_recording"
        self.assertEqual(self._check(report, "stop_p95"), "PASS")
        report["barge_in"].update(latency_ms=[320.0] * 19, latency_method="internal_lower_bound")
        self.assertEqual(self._check(report, "stop_p95"), "FAIL", "un limite inferiore oltre 300 ms e' gia' un fallimento")

    def test_one_echo_command_fails(self):
        report = _green_profile()
        report["barge_in"]["echo_commands"] = 1
        self.assertEqual(self._check(report, "no_echo_commands"), "FAIL")
        self.assertEqual(evaluate_profile(report)["verdict"], "FAIL")

    def test_one_false_stop_fails(self):
        report = _green_profile()
        report["barge_in"]["false_stops"] = 1
        self.assertEqual(self._check(report, "no_false_stops"), "FAIL")

    def test_quiet_trials_need_ten_in_a_quiet_room_and_ten_with_background_speech(self):
        report = _green_profile()
        report["barge_in"].update(no_interruption_quiet_room=20, no_interruption_background=0)
        self.assertEqual(self._check(report, "no_false_stops"), "VERIFY", "il documento chiede anche 10 prove con TV")
        report["barge_in"].update(no_interruption_quiet_room=9, no_interruption_background=11)
        self.assertEqual(self._check(report, "no_echo_commands"), "VERIFY")

    def test_first_emission_at_two_seconds_or_more_fails(self):
        report = _green_profile()
        report["tts"]["first_emission_ms"] = [2000.0] * 20
        self.assertEqual(self._check(report, "first_emission_p95"), "FAIL")
        report["tts"]["first_emission_ms"] = []
        self.assertEqual(self._check(report, "first_emission_p95"), "VERIFY")

    def test_partials_at_one_second_or_more_fail(self):
        report = _green_profile()
        report["streaming"]["partial_latency_ms"] = [1000.0] * 10
        self.assertEqual(self._check(report, "partials"), "FAIL")

    def test_no_partials_passes_only_as_a_real_fallback_with_finals(self):
        report = _green_profile()
        report["streaming"] = {"partial_latency_ms": [], "duplicate_finals": 0, "finals": 30}
        self.assertEqual(self._check(report, "partials"), "VERIFY", "assenza di dati non e' un fallback")
        report["streaming"]["fallback_full_utterance"] = True
        self.assertEqual(self._check(report, "partials"), "PASS")
        report["streaming"]["finals"] = 0
        self.assertEqual(self._check(report, "partials"), "VERIFY", "fallback senza nessuna frase finale")
        report["streaming"].update(finals=30, duplicate_finals=1)
        self.assertEqual(self._check(report, "partials"), "FAIL")

    def test_an_empty_or_malformed_audio_report_never_passes(self):
        for report in ({"profile": "laptop"}, {"profile": "laptop", "barge_in": {}, "tts": {}, "streaming": {}}):
            with self.subTest(report=report):
                self.assertEqual(evaluate_profile(report)["verdict"], "VERIFY")


class WakeVerdictTests(unittest.TestCase):
    def _wake(self, **wake):
        base = {"hours": 24.0, "false_wakes": 0, "intentional": 21, "missed": 1,
                "intentional_by_distance": {"0.5m": 7, "3m": 7, "6m": 7}}
        return evaluate_wake([{"wake": {**base, **wake}}])

    def test_a_green_wake_session_passes(self):
        self.assertEqual(self._wake()["verdict"], "PASS")

    def test_less_than_24_hours_is_verify(self):
        self.assertEqual(self._wake(hours=23.9)["checks"]["false_wake_rate"], "VERIFY")

    def test_more_than_one_false_wake_per_24_hours_fails(self):
        self.assertEqual(self._wake(false_wakes=2)["checks"]["false_wake_rate"], "FAIL")
        self.assertEqual(self._wake(hours=48.0, false_wakes=2)["checks"]["false_wake_rate"], "PASS")

    def test_fewer_than_twenty_intentional_wakes_is_verify(self):
        self.assertEqual(self._wake(intentional=19, missed=0)["checks"]["miss_rate"], "VERIFY")

    def test_miss_rate_above_5_percent_fails(self):
        self.assertEqual(self._wake(intentional=20, missed=2)["checks"]["miss_rate"], "FAIL")
        self.assertEqual(self._wake(intentional=20, missed=1)["checks"]["miss_rate"], "PASS")

    def test_every_distance_must_have_been_tried(self):
        result = self._wake(intentional_by_distance={"0.5m": 21, "3m": 0, "6m": 0})
        self.assertEqual(result["checks"]["wake_distances"], "VERIFY")


class GlobalVerdictTests(unittest.TestCase):
    def test_all_three_profiles_and_the_wake_are_needed(self):
        reports = _three_green()
        self.assertEqual(evaluate_reports(reports)["gate"], "PASS")
        self.assertEqual(evaluate_reports(reports[:2])["gate"], "VERIFY", "un profilo mancante impedisce il PASS")

    def test_a_later_failing_run_is_never_hidden_by_an_earlier_better_one(self):
        reports = _three_green()
        failing = _green_profile("laptop", recorded_at="2026-09-26T10:00:00")
        failing["barge_in"]["false_stops"] = 3
        self.assertEqual(evaluate_reports(reports + [failing])["gate"], "FAIL")
        older_but_better = _green_profile("laptop", recorded_at="2026-09-24T10:00:00")
        self.assertEqual(evaluate_reports([failing, older_but_better, reports[0], reports[2]])["gate"], "FAIL")

    def test_reports_of_an_older_method_are_ignored(self):
        import json
        import tempfile
        from pathlib import Path

        from benchmarks.f2_hardware_session import load_reports

        with tempfile.TemporaryDirectory() as tmp:
            old = _green_profile()
            old["method_version"] = 2
            Path(tmp, "f2_hardware_headphones_old.json").write_text(json.dumps(old), encoding="utf-8")
            self.assertEqual(load_reports(Path(tmp)), [])


class ReportContentTests(unittest.TestCase):
    """Nessun audio, nessun testo trascritto nel report: solo numeri, conteggi e hardware."""

    def _measured(self):
        metrics = SessionMetrics()
        metrics.barge_in(0.24, 0.02)
        metrics.utterance_frame()
        metrics.partial()
        metrics.final("u1")
        metrics.wake()
        return metrics.snapshot()

    def test_the_report_holds_no_audio_and_no_transcribed_text(self):
        import re

        from benchmarks.f2_hardware_session import build_report

        answers = {"attempts": 20, "quiet_room": 10, "background": 10, "false_stops": 0, "echo_commands": 0,
                   "false_wakes": 0, "intentional_0.5m": 7, "intentional_3m": 7, "intentional_6m": 6, "missed": 0}
        report = build_report("headphones", self._measured(), answers, {"input": "Mic", "output": "Cuffie"})
        forbidden = re.compile(r"audio_data|wav|transcript|samples|^text$")

        def walk(value, path="report"):
            if isinstance(value, dict):
                for key, inner in value.items():
                    self.assertIsNone(forbidden.search(key.lower()), f"{path}.{key}")
                    walk(inner, f"{path}.{key}")
            elif isinstance(value, list):
                for inner in value:
                    self.assertIsInstance(inner, (int, float), f"{path}: solo numeri nelle liste")

        walk(report)
        self.assertFalse(report["raw_audio_retained"])
        self.assertEqual(report["wake"]["intentional"], 20)
        self.assertEqual(report["wake"]["intentional_by_distance"], {"0.5m": 7, "3m": 7, "6m": 6})

    def test_false_stops_are_not_counted_as_detected_interruptions(self):
        from benchmarks.f2_hardware_session import build_report

        measured = self._measured()
        measured["barge_in"]["detected"] = 21  # 19 interruzioni vere + 2 falsi stop
        report = build_report("laptop", measured, {"attempts": 20, "false_stops": 2}, {})
        self.assertEqual(report["barge_in"]["detected"], 19)

    def test_an_external_latency_measure_replaces_the_internal_estimate(self):
        from benchmarks.f2_hardware_session import build_report, parse_latencies_ms

        measured = self._measured()
        internal = build_report("laptop", measured, {"attempts": 20}, {})
        self.assertEqual(internal["barge_in"]["latency_method"], "internal_lower_bound")
        external = build_report("laptop", measured, {"attempts": 20, "external_latency_ms": [210.0, 260.0]}, {})
        self.assertEqual(external["barge_in"]["latency_ms"], [210.0, 260.0])
        self.assertEqual(external["barge_in"]["latency_method"], "external_recording")
        self.assertEqual(external["measured_by_jake"]["barge_in"]["latency_ms"], measured["barge_in"]["latency_ms"])
        self.assertEqual(parse_latencies_ms("210, 235;250"), [210.0, 235.0, 250.0])
        self.assertEqual(parse_latencies_ms(""), [])
        self.assertIsNone(parse_latencies_ms("210, tanti"))
        self.assertIsNone(parse_latencies_ms("-5"))

    def test_the_fallback_is_claimed_only_when_partials_were_really_unavailable(self):
        from benchmarks.f2_hardware_session import build_report

        measured = self._measured()
        measured["streaming"]["partial_latency_ms"] = []
        not_degraded = build_report("laptop", measured, {}, {}, partials_degraded=False)
        degraded = build_report("laptop", measured, {}, {}, partials_degraded=True)
        self.assertFalse(not_degraded["streaming"]["fallback_full_utterance"])
        self.assertTrue(degraded["streaming"]["fallback_full_utterance"])


if __name__ == "__main__":
    unittest.main()
