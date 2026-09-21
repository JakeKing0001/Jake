"""Test per core/voice/streaming_stt.py (F2.2.2-F2.2.6). Nessun modello: il provider e' finto e
restituisce ipotesi scritte a mano, l'orologio e' finto, cosi' stabilita', budget e backpressure
si provano in modo deterministico."""
import unittest

import numpy as np

from core.voice.streaming_stt import (
    KIND_FINAL, KIND_PARTIAL, TRANSCRIPT_SCHEMA_VERSION, BoundedAudioBuffer, LocalAgreementStabilizer,
    StreamingTranscriber, TranscriptEvent, TranscriptRouter,
)

SR = 16000


def _chunk(seconds: float, value: float = 0.1) -> np.ndarray:
    return np.full(int(seconds * SR), value, dtype=np.float32)


class ScriptedProvider:
    def __init__(self, hypotheses, detailed=False):
        self.hypotheses = list(hypotheses)
        self.calls = []
        if detailed:
            self.transcribe_detailed = self._detailed

    def transcribe(self, audio, sample_rate):
        self.calls.append(len(audio))
        return self.hypotheses.pop(0) if len(self.hypotheses) > 1 else self.hypotheses[0]

    def _detailed(self, audio, sample_rate):
        return self.transcribe(audio, sample_rate), 0.87


class StabilizerTests(unittest.TestCase):
    def test_nothing_is_stable_after_the_first_hypothesis(self):
        self.assertEqual(LocalAgreementStabilizer().update("apri spo"), "")

    def test_words_agreed_by_two_consecutive_hypotheses_become_stable(self):
        s = LocalAgreementStabilizer()
        s.update("apri spo")
        self.assertEqual(s.update("apri spotify e"), "apri")
        self.assertEqual(s.update("apri Spotify e metti"), "apri Spotify e")

    def test_agreement_ignores_case_and_trailing_punctuation(self):
        s = LocalAgreementStabilizer()
        s.update("che ore sono")
        self.assertEqual(s.update("Che ore sono?"), "Che ore sono?")  # vince la grafia piu' recente

    def test_confirmed_text_never_shrinks_even_if_a_later_hypothesis_disagrees(self):
        s = LocalAgreementStabilizer()
        s.update("uno due tre")
        s.update("uno due tre quattro")
        self.assertEqual(s.stable_text, "uno due tre")
        self.assertEqual(s.update("uno duo"), "uno due tre")  # l'ipotesi cambia, il confermato resta

    def test_reset_forgets_everything(self):
        s = LocalAgreementStabilizer()
        s.update("a b")
        s.update("a b c")
        s.reset()
        self.assertEqual(s.stable_text, "")


class BoundedBufferTests(unittest.TestCase):
    def test_drops_the_oldest_samples_and_counts_them(self):
        buf = BoundedAudioBuffer(max_seconds=1.0, sample_rate=SR)
        buf.push(np.full(SR, 1.0, dtype=np.float32))
        buf.push(np.full(SR // 2, 2.0, dtype=np.float32))
        self.assertEqual(len(buf), SR)
        self.assertEqual(buf.dropped_samples, SR // 2)
        snapshot = buf.snapshot()
        self.assertEqual(float(snapshot[-1]), 2.0)  # la coda piu' recente e' intatta
        self.assertEqual(float(snapshot[0]), 1.0)
        self.assertEqual(int(np.sum(snapshot == 2.0)), SR // 2)

    def test_a_chunk_larger_than_the_whole_buffer_keeps_only_its_tail(self):
        buf = BoundedAudioBuffer(max_seconds=0.5, sample_rate=SR)
        buf.push(np.arange(SR, dtype=np.float32))
        self.assertEqual(len(buf), SR // 2)
        self.assertEqual(buf.dropped_samples, SR // 2)
        self.assertEqual(float(buf.snapshot()[-1]), SR - 1)

    def test_clear_empties_the_buffer_and_the_counter(self):
        buf = BoundedAudioBuffer(1.0, SR)
        buf.push(_chunk(2.0))
        buf.clear()
        self.assertEqual((len(buf), buf.dropped_samples, buf.snapshot().size), (0, 0, 0))

    def test_accepts_multichannel_shaped_input(self):
        buf = BoundedAudioBuffer(1.0, SR)
        buf.push(np.zeros((160, 1), dtype=np.float32))
        self.assertEqual(len(buf), 160)


class PartialsTests(unittest.TestCase):
    def test_partials_are_emitted_at_the_configured_interval_with_growing_revisions(self):
        provider = ScriptedProvider(["apri", "apri spotify", "apri spotify e metti"])
        t = StreamingTranscriber(provider, partial_interval_s=0.6)
        self.assertEqual(t.push_audio(_chunk(0.3)), [])  # sotto l'intervallo: nessuna decodifica
        self.assertEqual(provider.calls, [])
        events = [e for _ in range(3) for e in t.push_audio(_chunk(0.6))]
        self.assertEqual([e.revision for e in events], [1, 2, 3])
        self.assertTrue(all(e.kind == KIND_PARTIAL for e in events))
        self.assertEqual({e.utterance_id for e in events}, {t.utterance_id})
        self.assertEqual(events[1].stable_text, "apri")
        self.assertEqual(events[2].stable_text, "apri spotify")

    def test_an_unchanged_hypothesis_does_not_emit_a_duplicate_partial(self):
        t = StreamingTranscriber(ScriptedProvider(["apri", "apri", "apri"]), partial_interval_s=0.5)
        events = [e for _ in range(3) for e in t.push_audio(_chunk(0.5))]
        self.assertEqual(len(events), 1)

    def test_an_empty_hypothesis_emits_nothing(self):
        t = StreamingTranscriber(ScriptedProvider([""]), partial_interval_s=0.5)
        self.assertEqual(t.push_audio(_chunk(0.5)), [])

    def test_partial_never_carries_kind_final(self):
        t = StreamingTranscriber(ScriptedProvider(["a b c"]), partial_interval_s=0.2)
        for _ in range(5):
            for event in t.push_audio(_chunk(0.2)):
                self.assertEqual(event.kind, KIND_PARTIAL)

    def test_detailed_provider_reports_a_real_confidence(self):
        t = StreamingTranscriber(ScriptedProvider(["ciao"], detailed=True), partial_interval_s=0.2)
        (event,) = t.push_audio(_chunk(0.2))
        self.assertEqual(event.confidence, 0.87)

    def test_plain_provider_reports_no_confidence_instead_of_inventing_one(self):
        t = StreamingTranscriber(ScriptedProvider(["ciao"]), partial_interval_s=0.2)
        (event,) = t.push_audio(_chunk(0.2))
        self.assertIsNone(event.confidence)


class FinalizationTests(unittest.TestCase):
    def test_finish_returns_one_final_and_empties_the_buffer(self):
        t = StreamingTranscriber(ScriptedProvider(["apri spotify"]), partial_interval_s=0.5)
        t.push_audio(_chunk(1.0))
        final = t.finish()
        assert final is not None
        self.assertEqual((final.kind, final.text, final.stable_text), (KIND_FINAL, "apri spotify", "apri spotify"))
        self.assertEqual(t.buffered_samples, 0)  # F2.2.5

    def test_final_revision_follows_the_partials(self):
        t = StreamingTranscriber(ScriptedProvider(["a", "a b", "a b c"]), partial_interval_s=0.5)
        partials = [e for _ in range(2) for e in t.push_audio(_chunk(0.5))]
        final = t.finish()
        assert final is not None
        self.assertEqual(final.revision, partials[-1].revision + 1)

    def test_a_second_finish_returns_none_never_a_duplicate_final(self):
        t = StreamingTranscriber(ScriptedProvider(["ciao"]), partial_interval_s=5)
        t.push_audio(_chunk(0.5))
        self.assertIsNotNone(t.finish())
        self.assertIsNone(t.finish())

    def test_pushing_after_finish_is_an_error_until_reset(self):
        t = StreamingTranscriber(ScriptedProvider(["ciao"]), partial_interval_s=5)
        t.push_audio(_chunk(0.5))
        t.finish()
        with self.assertRaises(RuntimeError):
            t.push_audio(_chunk(0.1))
        first_id = t.utterance_id
        t.reset()
        self.assertNotEqual(t.utterance_id, first_id)
        t.push_audio(_chunk(0.1))  # ora funziona

    def test_finish_without_audio_returns_none(self):
        self.assertIsNone(StreamingTranscriber(ScriptedProvider(["x"])).finish())

    def test_a_failing_final_decode_returns_none_and_still_clears_the_buffer(self):
        class Boom:
            def transcribe(self, audio, sample_rate):
                raise RuntimeError("modello")

        t = StreamingTranscriber(Boom(), partial_interval_s=5)
        t.push_audio(_chunk(0.5))
        self.assertIsNone(t.finish())
        self.assertEqual(t.buffered_samples, 0)


class DegradationTests(unittest.TestCase):
    def test_streaming_false_produces_only_the_final(self):
        provider = ScriptedProvider(["apri spotify"])
        t = StreamingTranscriber(provider, streaming=False, partial_interval_s=0.1)
        events = [e for _ in range(5) for e in t.push_audio(_chunk(0.5))]
        self.assertEqual(events, [])
        self.assertEqual(provider.calls, [])
        final = t.finish()
        assert final is not None
        self.assertEqual(final.text, "apri spotify")

    def test_a_partial_slower_than_the_budget_turns_streaming_off_for_the_utterance(self):
        now = {"t": 0.0}

        class SlowProvider:
            calls = 0

            def transcribe(self, audio, sample_rate):
                SlowProvider.calls += 1
                now["t"] += 2.5  # ogni decodifica "costa" 2,5 s di orologio finto
                return "testo lento"

        t = StreamingTranscriber(SlowProvider(), partial_interval_s=0.5, partial_budget_s=1.0, clock=lambda: now["t"])
        events = [e for _ in range(4) for e in t.push_audio(_chunk(0.5))]
        self.assertEqual(events, [])
        self.assertTrue(t.degraded)
        self.assertEqual(SlowProvider.calls, 1)  # dopo il primo sforamento non ritenta piu'
        self.assertIsNotNone(t.finish())  # il final funziona comunque

    def test_a_crash_in_a_partial_degrades_instead_of_raising(self):
        class Boom:
            def transcribe(self, audio, sample_rate):
                raise RuntimeError("gpu")

        t = StreamingTranscriber(Boom(), partial_interval_s=0.2)
        self.assertEqual(t.push_audio(_chunk(0.5)), [])
        self.assertTrue(t.degraded)

    def test_reset_restores_streaming_after_a_degraded_utterance(self):
        t = StreamingTranscriber(ScriptedProvider(["x"]), partial_interval_s=0.2)
        t.degraded = True
        t.reset()
        self.assertFalse(t.degraded)

    def test_reset_keeps_a_deliberately_non_streaming_transcriber_non_streaming(self):
        t = StreamingTranscriber(ScriptedProvider(["x"]), streaming=False)
        t.reset()
        self.assertTrue(t.degraded)


class BackpressureTests(unittest.TestCase):
    def test_a_long_utterance_never_holds_more_than_the_buffer_limit(self):
        t = StreamingTranscriber(ScriptedProvider(["x"]), max_buffer_s=2.0, streaming=False)
        for _ in range(10):
            t.push_audio(_chunk(1.0))
        self.assertEqual(t.buffered_samples, 2 * SR)
        self.assertEqual(t.dropped_samples, 8 * SR)


class PayloadTests(unittest.TestCase):
    def _event(self, **overrides):
        base = {
            "utterance_id": "abc", "revision": 2, "kind": KIND_PARTIAL, "text": "apri spo",
            "stable_text": "apri", "confidence": 0.5,
        }
        base.update(overrides)
        return TranscriptEvent(**base)

    def test_roundtrip_through_json_types(self):
        import json

        event = self._event()
        restored = TranscriptEvent.from_payload(json.loads(json.dumps(event.to_payload())))
        self.assertEqual(restored, event)
        self.assertEqual(event.to_payload()["transcript_version"], TRANSCRIPT_SCHEMA_VERSION)

    def test_malformed_payloads_are_rejected_with_a_clear_error(self):
        good = self._event().to_payload()
        bad_cases = [
            {**good, "transcript_version": 99},
            {**good, "kind": "maybe"},
            {**good, "revision": 0},
            {**good, "revision": True},
            {**good, "text": None},
            {**good, "confidence": "alta"},
            {k: v for k, v in good.items() if k != "utterance_id"},
        ]
        for payload in bad_cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                TranscriptEvent.from_payload(payload)


class HudEventTests(unittest.TestCase):
    def test_transcript_becomes_a_versioned_bus_event_that_survives_the_wire(self):
        from core.hud_protocol import EventType, HudEvent
        from core.voice.streaming_stt import to_hud_event

        event = TranscriptEvent("u1", 3, KIND_PARTIAL, "apri spo", "apri", 0.42)
        hud = to_hud_event(event)
        self.assertEqual(hud.type, EventType.TRANSCRIPT)
        restored = HudEvent.from_json(hud.to_json())
        self.assertEqual(TranscriptEvent.from_payload(restored.payload), event)


class RouterTests(unittest.TestCase):
    def _router(self, with_partial=True):
        finals, partials = [], []
        router = TranscriptRouter(finals.append, partials.append if with_partial else None)
        return router, finals, partials

    def _e(self, kind, text, revision=1, uid="u1"):
        return TranscriptEvent(uid, revision, kind, text, text if kind == KIND_FINAL else "")

    def test_a_partial_is_never_a_command(self):
        router, finals, partials = self._router()
        self.assertTrue(router.handle(self._e(KIND_PARTIAL, "apri spotify")))
        self.assertEqual((finals, len(partials)), ([], 1))

    def test_only_the_final_reaches_the_command_callback(self):
        router, finals, _ = self._router()
        router.handle(self._e(KIND_PARTIAL, "apri", 1))
        router.handle(self._e(KIND_FINAL, "apri spotify", 2))
        self.assertEqual([e.text for e in finals], ["apri spotify"])

    def test_a_repeated_final_for_the_same_utterance_is_dropped(self):
        router, finals, _ = self._router()
        event = self._e(KIND_FINAL, "ciao")
        self.assertTrue(router.handle(event))
        self.assertFalse(router.handle(event))
        self.assertEqual(len(finals), 1)

    def test_an_empty_final_is_dropped(self):
        router, finals, _ = self._router()
        self.assertFalse(router.handle(self._e(KIND_FINAL, "   ")))
        self.assertEqual(finals, [])

    def test_an_out_of_order_partial_revision_is_dropped(self):
        router, _, partials = self._router()
        router.handle(self._e(KIND_PARTIAL, "b", revision=3))
        self.assertFalse(router.handle(self._e(KIND_PARTIAL, "a", revision=2)))
        self.assertEqual([p.revision for p in partials], [3])

    def test_without_a_partial_callback_partials_are_ignored(self):
        router, finals, _ = self._router(with_partial=False)
        self.assertFalse(router.handle(self._e(KIND_PARTIAL, "x")))
        self.assertEqual(finals, [])

    def test_a_new_utterance_can_finalize_after_the_previous_one(self):
        router, finals, _ = self._router()
        router.handle(self._e(KIND_FINAL, "uno", uid="u1"))
        router.handle(self._e(KIND_FINAL, "due", uid="u2"))
        self.assertEqual([e.text for e in finals], ["uno", "due"])


if __name__ == "__main__":
    unittest.main()
