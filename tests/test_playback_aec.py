"""Test per core/voice/playback_aec.py: l'AEC nel percorso reale, con riferimento piazzato sulla linea
del tempo e ritardo stimato dalla riproduzione stessa. Segnali sintetici, orologio finto."""
import unittest

import numpy as np

from benchmarks.voice_corpus import _speechlike
from core.voice.audio_frontend import SAMPLE_RATE, erle_db
from core.voice.playback_aec import PlaybackAec, to_reference

SR = SAMPLE_RATE
FRAME = 480


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _voice(seconds, seed, f0=120, rms=0.1):
    x = _speechlike(seconds, np.random.default_rng(seed), f0=f0).astype(np.float64) / 32768
    return (x * rms / np.sqrt(np.mean(x**2))).astype(np.float32)


def _echo(reference, delay, gain):
    h = np.zeros(delay + 4)
    for i, tap in enumerate((1.0, 0.4, 0.2, 0.1)):
        h[delay + i] = tap * gain
    return np.convolve(reference, h)[: len(reference)].astype(np.float32)


class ToReferenceTests(unittest.TestCase):
    def test_int16_is_normalised_to_float(self):
        ref = to_reference(np.array([16384, -16384], dtype=np.int16), SR)
        np.testing.assert_allclose(ref, [0.5, -0.5])

    def test_resamples_24k_to_16k(self):
        ref = to_reference(np.zeros(2400, dtype=np.int16), 24000)
        self.assertEqual(len(ref), 1600)

    def test_stereo_is_mixed_down(self):
        ref = to_reference(np.array([[1000, 3000], [1000, 3000]], dtype=np.int16), SR)
        self.assertEqual(ref.shape, (2,))

    def test_float_input_is_kept(self):
        np.testing.assert_allclose(to_reference(np.array([0.25, -0.25], dtype=np.float32), SR), [0.25, -0.25])

    def test_a_single_sample_survives(self):
        self.assertEqual(len(to_reference(np.array([100], dtype=np.int16), 24000)), 1)


class PlaybackAecTests(unittest.TestCase):
    def _run(self, delay_samples, gain, gap_s=0.0, seconds=6.0):
        """Jake parla `seconds` s (con un eventuale vuoto tra due frasi); ritorna eco, residuo e l'AEC."""
        clock = Clock()
        aec = PlaybackAec(clock=clock)
        first, second = _voice(2.0, 1), _voice(seconds - 2.0 - gap_s, 2, f0=170)
        total = int(seconds * SR)
        reference = np.zeros(total, dtype=np.float32)
        reference[: len(first)] = first
        start2 = len(first) + int(gap_s * SR)
        reference[start2:start2 + len(second)] = second
        echo = _echo(reference, delay_samples, gain)
        outputs, ready_flags = [], []
        pushed_first = pushed_second = False
        for i in range(0, total - FRAME + 1, FRAME):
            clock.t = 100.0 + i / SR
            if not pushed_first:
                aec.push_reference(first, SR)
                pushed_first = True
            if not pushed_second and i >= start2:
                aec.push_reference(second, SR)
                pushed_second = True
            residual, ready = aec.process(echo[i:i + FRAME])
            outputs.append(residual)
            ready_flags.append(ready)
        return echo[: len(outputs) * FRAME], np.concatenate(outputs), ready_flags, aec

    def test_becomes_ready_after_the_calibration_period_and_cancels_the_echo(self):
        echo, out, ready, aec = self._run(240, 0.5)
        self.assertFalse(ready[0])
        self.assertTrue(ready[-1])
        self.assertEqual(aec.delay_samples, 0)  # 240 - 320 < 0: il margine porta il ritardo a zero, la finestra copre l'eco
        n = len(out)
        self.assertGreater(erle_db(echo[n - 2 * SR:n], out[n - 2 * SR:n]), 20.0)

    def test_the_grace_period_is_reported_as_not_ready(self):
        _, _, ready, _ = self._run(240, 0.5)
        first_ready = ready.index(True)
        self.assertGreaterEqual(first_ready * FRAME / SR, 1.0)  # mai pronta prima di aver sentito abbastanza
        self.assertLess(first_ready * FRAME / SR, 2.0)

    def test_a_bluetooth_delay_is_found_from_the_playback_itself(self):
        echo, out, ready, aec = self._run(2900, 0.4)
        self.assertAlmostEqual(aec.delay_samples, 2900 - PlaybackAec.DELAY_MARGIN, delta=3)
        n = len(out)
        self.assertGreater(erle_db(echo[n - 2 * SR:n], out[n - 2 * SR:n]), 20.0)

    def test_a_silence_between_two_sentences_does_not_misalign_the_second_one(self):
        """Il caso Edge TTS: 0,4 s di vuoto (rete) tra le frasi. Con il riferimento ACCODATO invece che
        piazzato nel tempo la seconda frase sarebbe sfasata e non verrebbe cancellata."""
        echo, out, _, _ = self._run(240, 0.5, gap_s=0.4)
        n = len(out)
        self.assertGreater(erle_db(echo[n - int(1.5 * SR):n], out[n - int(1.5 * SR):n]), 15.0)

    def test_a_reference_pushed_a_few_milliseconds_late_still_cancels(self):
        """Il provider pubblica il riferimento poco prima di sd.play, e la latenza cambia da una frase
        all'altra: la seconda frase piazzata 10 ms dopo l'istante reale non deve rompere l'AEC (con un
        margine di ritardo di 24 campioni l'eco calava di ~7 dB invece di ~30)."""
        echo, out, _, _ = self._run(240, 0.5)  # _run spinge la seconda frase al primo frame >= 2 s (10 ms dopo)
        n = len(out)
        self.assertGreater(erle_db(echo[n - SR:n], out[n - SR:n]), 20.0)

    def test_without_any_reference_the_microphone_passes_through_and_is_never_ready(self):
        aec = PlaybackAec(clock=Clock())
        mic = _voice(0.03, 3)
        residual, ready = aec.process(mic)
        np.testing.assert_allclose(residual, mic)
        self.assertFalse(ready)
        self.assertFalse(aec.has_reference)

    def test_reset_starts_a_new_playback_from_scratch(self):
        _, _, _, aec = self._run(240, 0.5)
        self.assertTrue(aec.ready)
        aec.reset()
        self.assertFalse(aec.ready)
        self.assertFalse(aec.has_reference)
        self.assertEqual(aec.position, 0)

    def test_position_counts_microphone_samples_since_the_start(self):
        aec = PlaybackAec(clock=Clock())
        aec.process(np.zeros(480, dtype=np.float32))
        aec.process(np.zeros(480, dtype=np.float32))
        self.assertEqual(aec.position, 960)

    def test_reference_level_reads_the_timeline(self):
        clock = Clock()
        aec = PlaybackAec(clock=clock)
        clock.t = 100.5  # la riproduzione comincia mezzo secondo dopo il reset
        aec.push_reference(np.full(SR, 8192, dtype=np.int16), SR)
        self.assertEqual(aec.reference_level(0, 1000), 0.0)  # il vuoto iniziale
        self.assertAlmostEqual(aec.reference_level(int(0.6 * SR), 1000), 0.25, places=3)
        self.assertEqual(aec.reference_level(10 * SR, 1000), 0.0)

    def test_history_is_bounded_if_calibration_never_completes(self):
        aec = PlaybackAec(clock=Clock(), max_history_s=1.0)
        aec.push_reference(np.ones(SR // 10, dtype=np.float32) * 0.1, SR)  # riferimento troppo corto per calibrare
        for _ in range(200):
            aec.process(np.zeros(FRAME, dtype=np.float32))
        self.assertLessEqual(sum(len(c) for c in aec._history), SR + FRAME)
        self.assertFalse(aec.ready)


if __name__ == "__main__":
    unittest.main()
