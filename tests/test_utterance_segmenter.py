"""Test unitari per core/voice/utterance_segmenter.py (F2.2.1). Nessun audio vero e nessun VAD:
la macchina a stati riceve frame sintetici e il giudizio "parlato/non parlato" gia' deciso, cosi'
ogni transizione si controlla in modo deterministico."""
import unittest

import numpy as np

from core.voice.utterance_segmenter import UtteranceSegmenter, to_float32

FRAME = 480


def _frame(value: int = 1000) -> np.ndarray:
    return np.full((FRAME, 1), value, dtype=np.int16)


class FeedTests(unittest.TestCase):
    def test_silence_before_any_speech_produces_nothing_and_buffers_nothing(self):
        segmenter = UtteranceSegmenter(silence_frames_needed=2, max_frames=100)
        for _ in range(10):
            self.assertIsNone(segmenter.feed(_frame(0), False))
        self.assertEqual(segmenter.buffered_frames, 0)
        self.assertFalse(segmenter.in_speech)

    def test_utterance_ends_after_the_required_silence_and_includes_the_tail(self):
        segmenter = UtteranceSegmenter(silence_frames_needed=2, max_frames=100)
        self.assertIsNone(segmenter.feed(_frame(), True))
        self.assertIsNone(segmenter.feed(_frame(), True))
        self.assertIsNone(segmenter.feed(_frame(0), False))
        utterance = segmenter.feed(_frame(0), False)
        self.assertIsNotNone(utterance)
        assert utterance is not None
        self.assertEqual(utterance.dtype, np.float32)
        self.assertEqual(len(utterance), FRAME * 4)  # 2 di parlato + 2 di coda

    def test_a_short_pause_inside_speech_does_not_split_the_utterance(self):
        segmenter = UtteranceSegmenter(silence_frames_needed=3, max_frames=100)
        outputs = [segmenter.feed(_frame(), s) for s in (True, False, False, True, False, False, False)]
        finished = [o for o in outputs if o is not None]
        self.assertEqual(len(finished), 1)
        self.assertEqual(len(finished[0]), FRAME * 7)

    def test_max_length_forces_the_end_of_a_never_ending_utterance(self):
        segmenter = UtteranceSegmenter(silence_frames_needed=50, max_frames=5)
        results = [segmenter.feed(_frame(), True) for _ in range(5)]
        # in parlato continuo la lunghezza massima si controlla solo su un frame di silenzio: lo
        # stesso comportamento storico di VadListener, che qui e' fissato da un test.
        self.assertTrue(all(r is None for r in results))
        forced = segmenter.feed(_frame(0), False)
        self.assertIsNotNone(forced)
        self.assertEqual(segmenter.buffered_frames, 0)

    def test_two_consecutive_utterances_are_independent(self):
        segmenter = UtteranceSegmenter(silence_frames_needed=1, max_frames=100)
        first = [segmenter.feed(_frame(100), s) for s in (True, False)][-1]
        second = [segmenter.feed(_frame(200), s) for s in (True, True, False)][-1]
        assert first is not None and second is not None
        self.assertEqual(len(first), FRAME * 2)
        self.assertEqual(len(second), FRAME * 3)


class VolatileBufferTests(unittest.TestCase):
    """F2.2.5: il buffer vive in RAM e si svuota appena la frase e' consegnata o annullata."""

    def test_buffer_is_empty_right_after_finalization(self):
        segmenter = UtteranceSegmenter(silence_frames_needed=1, max_frames=100)
        segmenter.feed(_frame(), True)
        self.assertEqual(segmenter.buffered_frames, 1)
        self.assertIsNotNone(segmenter.feed(_frame(0), False))
        self.assertEqual(segmenter.buffered_frames, 0)
        self.assertFalse(segmenter.in_speech)

    def test_reset_discards_a_partial_utterance(self):
        segmenter = UtteranceSegmenter(silence_frames_needed=5, max_frames=100)
        segmenter.feed(_frame(), True)
        segmenter.feed(_frame(), True)
        segmenter.reset()
        self.assertEqual(segmenter.buffered_frames, 0)
        self.assertFalse(segmenter.in_speech)
        # dopo il reset non deve uscire nulla dal silenzio successivo
        self.assertIsNone(segmenter.feed(_frame(0), False))

    def test_flush_closes_the_utterance_in_progress(self):
        segmenter = UtteranceSegmenter(silence_frames_needed=5, max_frames=100)
        segmenter.feed(_frame(), True)
        utterance = segmenter.flush()
        assert utterance is not None
        self.assertEqual(len(utterance), FRAME)
        self.assertEqual(segmenter.buffered_frames, 0)

    def test_flush_without_speech_returns_none(self):
        segmenter = UtteranceSegmenter(silence_frames_needed=5, max_frames=100)
        self.assertIsNone(segmenter.flush())

    def test_thresholds_are_clamped_to_at_least_one_frame(self):
        segmenter = UtteranceSegmenter(silence_frames_needed=0, max_frames=0)
        self.assertEqual(segmenter.silence_frames_needed, 1)
        self.assertEqual(segmenter.max_frames, 1)


class ToFloat32Tests(unittest.TestCase):
    def test_normalizes_pcm16(self):
        result = to_float32([np.full((4, 1), -32768, dtype=np.int16)])
        np.testing.assert_allclose(result, [-1.0] * 4)


if __name__ == "__main__":
    unittest.main()
