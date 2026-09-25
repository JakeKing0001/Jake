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


def _green_profile(profile="headphones"):
    return {
        "method_version": 2, "profile": profile, "recorded_at": "2026-09-25T10:00:00",
        "barge_in": {"attempts": 20, "detected": 19, "latency_ms": [220.0] * 19,
                     "no_interruption_trials": 20, "false_stops": 0, "echo_commands": 0},
        "streaming": {"partial_latency_ms": [600.0] * 10, "duplicate_finals": 0},
        "tts": {"first_emission_ms": [1500.0] * 20},
        "wake": {"hours": 8.0, "false_wakes": 0, "intentional": 7, "missed": 0},
    }


class GateVerdictTests(unittest.TestCase):
    def test_a_green_profile_passes(self):
        self.assertEqual(evaluate_profile(_green_profile())["verdict"], "PASS")

    def test_eighteen_of_twenty_fails_and_too_few_attempts_is_verify(self):
        report = _green_profile()
        report["barge_in"]["detected"] = 18
        self.assertEqual(evaluate_profile(report)["checks"]["interruptions_detected"], "FAIL")
        report["barge_in"].update(attempts=12, detected=12)
        self.assertEqual(evaluate_profile(report)["checks"]["interruptions_detected"], "VERIFY")

    def test_one_echo_command_or_slow_stop_fails(self):
        report = _green_profile()
        report["barge_in"]["echo_commands"] = 1
        self.assertEqual(evaluate_profile(report)["verdict"], "FAIL")
        report = _green_profile()
        report["barge_in"]["latency_ms"] = [220.0] * 15 + [400.0] * 4
        self.assertEqual(evaluate_profile(report)["checks"]["stop_p95"], "FAIL")

    def test_no_partials_is_green_only_as_a_declared_fallback_without_duplicates(self):
        report = _green_profile()
        report["streaming"] = {"partial_latency_ms": [], "duplicate_finals": 0}
        self.assertEqual(evaluate_profile(report)["checks"]["partials"], "VERIFY")
        report["streaming"]["fallback_full_utterance"] = True
        self.assertEqual(evaluate_profile(report)["checks"]["partials"], "PASS")
        report["streaming"]["duplicate_finals"] = 1
        self.assertEqual(evaluate_profile(report)["checks"]["partials"], "FAIL")

    def test_wake_needs_24_hours_and_twenty_intentional_wakes(self):
        reports = [_green_profile(p) for p in ("headphones", "laptop", "bluetooth")]
        self.assertEqual(evaluate_wake(reports)["verdict"], "PASS")
        reports[0]["wake"]["false_wakes"] = 2
        self.assertEqual(evaluate_wake(reports)["checks"]["false_wake_rate"], "FAIL")
        short = [_green_profile()]
        self.assertEqual(evaluate_wake(short)["verdict"], "VERIFY")

    def test_the_gate_needs_all_three_profiles_and_the_latest_report_wins(self):
        reports = [_green_profile(p) for p in ("headphones", "laptop", "bluetooth")]
        self.assertEqual(evaluate_reports(reports)["gate"], "PASS")
        self.assertEqual(evaluate_reports(reports[:2])["gate"], "VERIFY")
        failing = _green_profile("laptop")
        failing["recorded_at"] = "2026-09-26T10:00:00"
        failing["barge_in"]["false_stops"] = 3
        self.assertEqual(evaluate_reports(reports + [failing])["gate"], "FAIL")


if __name__ == "__main__":
    unittest.main()
