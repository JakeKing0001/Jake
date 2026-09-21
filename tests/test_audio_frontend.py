"""Test per core/voice/audio_frontend.py (F2.4.1, F2.4.2). Segnali sintetici con un "percorso d'eco"
noto: si misura di quanti dB cala l'eco (ERLE) e di quanto migliora il rapporto segnale/rumore.
Nessun audio vero, nessun dispositivo. Soglie con margine largo sui valori misurati (ERLE ~18 dB,
+4 dB di SNR): un test che sfiora il limite sarebbe instabile."""
import unittest

import numpy as np

from benchmarks.voice_corpus import _speechlike
from core.voice.audio_frontend import (
    SAMPLE_RATE, AudioFrontEnd, AutoGain, EchoCanceller, NoiseSuppressor, erle_db, estimate_delay,
)

SR = SAMPLE_RATE


def _voice(seconds, seed, f0=120, rms=0.1):
    rng = np.random.default_rng(seed)
    x = _speechlike(seconds, rng, f0=f0).astype(np.float64) / 32768
    return (x * rms / np.sqrt(np.mean(x**2))).astype(np.float32)


def _path(delay, gain, taps=(1.0, 0.4, 0.2, 0.1)):
    h = np.zeros(delay + len(taps))
    for i, t in enumerate(taps):
        h[delay + i] = t * gain
    return h


def _run(aec, mic, frame=480):
    return np.concatenate([aec.process(mic[i:i + frame]) for i in range(0, len(mic) - frame + 1, frame)])


class EstimateDelayTests(unittest.TestCase):
    def test_finds_the_delay_within_a_sample(self):
        ref = _voice(2.0, 1)
        mic = np.concatenate([np.zeros(240, dtype=np.float32), ref * 0.5])[: len(ref)]
        self.assertAlmostEqual(estimate_delay(ref, mic, 1000), 240, delta=2)

    def test_uncorrelated_signals_give_zero(self):
        self.assertEqual(estimate_delay(_voice(2.0, 1), (np.random.default_rng(9).standard_normal(32000) * 0.1).astype(np.float32), 1000), 0)

    def test_too_short_or_silent_input_gives_zero(self):
        self.assertEqual(estimate_delay(np.zeros(100), np.zeros(100), 50), 0)
        self.assertEqual(estimate_delay(np.zeros(4000), np.zeros(4000), 50), 0)

    def test_delay_beyond_the_search_range_is_not_reported(self):
        ref = _voice(2.0, 1)
        mic = np.concatenate([np.zeros(3000, dtype=np.float32), ref])[: len(ref)]
        self.assertLess(estimate_delay(ref, mic, 500), 500)


class EchoCancellerTests(unittest.TestCase):
    def _scenario(self, delay, gain, seconds=6):
        ref = np.concatenate([_voice(1.0, s, f0) for s, f0 in zip(range(seconds), (120, 180, 140, 200, 110, 160), strict=False)])
        echo = np.convolve(ref, _path(delay, gain))[: len(ref)].astype(np.float32)
        return ref, echo

    def test_erle_above_12_db_for_a_laptop_speaker(self):
        ref, echo = self._scenario(240, 0.5)
        aec = EchoCanceller()
        aec.calibrate(ref[:SR], echo[:SR])
        aec.push_reference(ref)
        out = _run(aec, echo)
        n = len(out)
        self.assertGreater(erle_db(echo[n - 3 * SR:n], out[n - 3 * SR:n]), 12.0)

    def test_erle_above_12_db_even_with_a_long_bluetooth_delay(self):
        ref, echo = self._scenario(2900, 0.4)  # 181 ms: molto oltre la lunghezza del filtro
        aec = EchoCanceller()
        self.assertAlmostEqual(aec.calibrate(ref[:SR], echo[:SR]), 2900, delta=3)
        aec.push_reference(ref)
        out = _run(aec, echo)
        n = len(out)
        self.assertGreater(erle_db(echo[n - 3 * SR:n], out[n - 3 * SR:n]), 12.0)

    def test_without_the_delay_estimate_a_long_delay_is_not_cancelled(self):
        """Il filtro copre 32 ms: senza `delay_samples` un eco a 181 ms passa quasi intatto."""
        ref, echo = self._scenario(2900, 0.4)
        aec = EchoCanceller()
        aec.push_reference(ref)
        out = _run(aec, echo)
        n = len(out)
        self.assertLess(erle_db(echo[n - 3 * SR:n], out[n - 3 * SR:n]), 3.0)

    def test_the_users_voice_survives_while_the_echo_is_removed(self):
        ref, echo = self._scenario(240, 0.5)
        user = _voice(1.5, 77, f0=105, rms=0.12)
        mic = echo.copy()
        start = 4 * SR
        mic[start:start + len(user)] += user
        aec = EchoCanceller()
        aec.calibrate(ref[:SR], echo[:SR])
        aec.push_reference(ref)
        out = _run(aec, mic)
        window = slice(start + 2000, start + len(user) - 2000)
        # dopo la cancellazione, l'energia nella finestra della voce e' dominata dall'utente
        self.assertGreater(np.mean(out[window] ** 2), 0.4 * np.mean(user[2000:-2000] ** 2))
        # ...e nella finestra senza utente subito prima e' molto inferiore
        self.assertLess(np.mean(out[start - 24000:start - 4000] ** 2), 0.2 * np.mean(echo[start - 24000:start - 4000] ** 2))

    def test_without_any_reference_the_microphone_passes_through_untouched(self):
        mic = _voice(1.0, 3)
        out = EchoCanceller().process(mic)
        np.testing.assert_allclose(out, mic, atol=1e-6)

    def test_reset_forgets_reference_and_filter(self):
        ref, echo = self._scenario(240, 0.5, seconds=3)
        aec = EchoCanceller()
        aec.push_reference(ref)
        _run(aec, echo)
        self.assertGreater(float(np.abs(aec._weights).max()), 0.0)
        aec.reset()
        self.assertEqual(float(np.abs(aec._weights).max()), 0.0)
        np.testing.assert_allclose(aec.process(echo[:480]), echo[:480], atol=1e-6)

    def test_reference_buffer_is_bounded(self):
        aec = EchoCanceller(max_reference_s=2.0)
        for _ in range(5):
            aec.push_reference(np.zeros(SR, dtype=np.float32))
        self.assertEqual(len(aec._reference), 2 * SR)

    def test_runs_faster_than_real_time(self):
        import time

        ref, echo = self._scenario(240, 0.5, seconds=2)
        aec = EchoCanceller()
        aec.push_reference(ref)
        started = time.perf_counter()
        _run(aec, echo)
        self.assertLess(time.perf_counter() - started, 2.0 * 0.6)  # ampio margine: misurato ~0,08x

    def test_empty_frame_is_harmless(self):
        aec = EchoCanceller()
        aec.push_reference(np.ones(100, dtype=np.float32))
        self.assertEqual(aec.process(np.zeros(0, dtype=np.float32)).size, 0)


class NoiseSuppressorTests(unittest.TestCase):
    def _noisy(self, noise_std, seed=2):
        rng = np.random.default_rng(seed)
        speech = _voice(1.0, seed, rms=0.08)
        clean = np.concatenate([np.zeros(SR), speech, np.zeros(SR), speech, np.zeros(SR)]).astype(np.float32)
        return clean, clean + (rng.standard_normal(len(clean)) * noise_std).astype(np.float32)

    def _process(self, signal, chunk=480):
        ns = NoiseSuppressor()
        out = np.concatenate([ns.process(signal[i:i + chunk]) for i in range(0, len(signal) - chunk + 1, chunk)])
        return out, ns.latency_samples

    @staticmethod
    def _snr(estimate, reference):
        return 10 * np.log10(np.sum(reference**2) / np.sum((estimate - reference) ** 2))

    def test_snr_improves_in_a_noisy_room(self):
        clean, noisy = self._noisy(0.03)
        out, lag = self._process(noisy)
        before = self._snr(noisy[SR:2 * SR], clean[SR:2 * SR])
        after = self._snr(out[SR + lag:2 * SR + lag], clean[SR:2 * SR])
        self.assertGreater(after, before + 2.0)

    def test_noise_only_stretches_get_quieter(self):
        _, noisy = self._noisy(0.03)
        out, lag = self._process(noisy)
        before = np.mean(noisy[4 * SR - 2000:5 * SR - 2000] ** 2)
        after = np.mean(out[4 * SR - 2000 + lag:5 * SR - 2000 + lag] ** 2)
        self.assertLess(after, before * 0.4)  # almeno -4 dB

    def test_with_no_reduction_the_signal_is_reconstructed_exactly(self):
        """Prova della sovrapposizione radice-di-Hann: dove il guadagno vale 1 l'uscita e' l'ingresso."""
        x = (np.random.default_rng(0).standard_normal(16000) * 0.1).astype(np.float32)
        ns = NoiseSuppressor(reduction_db=0.0)
        out = np.concatenate([ns.process(x[i:i + 480]) for i in range(0, len(x) - 479, 480)])
        lag = ns.latency_samples
        np.testing.assert_allclose(out[lag + 600:lag + 8000], x[600:8000], atol=1e-5)

    def test_output_length_always_matches_input_length(self):
        ns = NoiseSuppressor()
        for size in (1, 100, 480, 700, 1024):
            self.assertEqual(ns.process(np.zeros(size, dtype=np.float32)).size, size)

    def test_latency_is_reported_and_bounded(self):
        ns = NoiseSuppressor()
        for _ in range(10):
            ns.process(np.zeros(480, dtype=np.float32))
        self.assertLessEqual(ns.latency_samples, 1024)

    def test_reset_clears_the_noise_estimate(self):
        ns = NoiseSuppressor()
        ns.process(np.ones(2000, dtype=np.float32) * 0.1)
        ns.reset()
        self.assertIsNone(ns._noise)


class AutoGainTests(unittest.TestCase):
    def test_a_quiet_voice_is_brought_toward_the_target(self):
        agc = AutoGain(target_rms=0.08)
        quiet = _voice(2.0, 1, rms=0.02)
        out = np.concatenate([agc.process(quiet[i:i + 480]) for i in range(0, len(quiet) - 479, 480)])
        self.assertGreater(np.sqrt(np.mean(out[-8000:] ** 2)), 0.05)

    def test_gain_is_capped(self):
        agc = AutoGain(target_rms=0.5, max_gain_db=6.0)
        very_quiet = np.full(480, 0.01, dtype=np.float32)
        for _ in range(300):
            agc.process(very_quiet)
        self.assertLessEqual(agc.gain, 10 ** (6 / 20) + 1e-6)

    def test_frames_below_the_gate_are_never_amplified(self):
        agc = AutoGain(gate_rms=0.01)
        hiss = np.full(480, 0.002, dtype=np.float32)
        for _ in range(100):
            out = agc.process(hiss)
        self.assertEqual(agc.gain, 1.0)
        np.testing.assert_allclose(out, hiss)

    def test_a_sudden_loud_sound_lowers_the_gain_and_never_clips_beyond_one(self):
        agc = AutoGain(target_rms=0.08)
        for _ in range(60):
            agc.process(np.full(480, 0.02, dtype=np.float32))
        boosted = agc.gain
        out = agc.process(np.full(480, 0.9, dtype=np.float32))
        self.assertLess(agc.gain, boosted)
        self.assertLessEqual(float(np.max(np.abs(out))), 1.0)

    def test_attack_is_faster_than_release(self):
        down = AutoGain()
        down.gain = 5.0
        down.process(np.full(480, 0.5, dtype=np.float32))
        up = AutoGain()
        up.gain = 1.0
        up.process(np.full(480, 0.02, dtype=np.float32))
        self.assertGreater(5.0 - down.gain, up.gain - 1.0)


class FrontEndTests(unittest.TestCase):
    def test_everything_bypassed_returns_the_same_array(self):
        front = AudioFrontEnd(aec=False, noise_suppression=False, agc=False)
        frame = _voice(0.03, 1)
        self.assertIs(front.process(frame), frame)

    def test_each_block_can_be_bypassed_independently(self):
        self.assertIsNone(AudioFrontEnd(aec=False).echo_canceller)
        self.assertIsNone(AudioFrontEnd(noise_suppression=False).noise_suppressor)
        self.assertIsNone(AudioFrontEnd(agc=False).auto_gain)
        full = AudioFrontEnd()
        self.assertIsNotNone(full.echo_canceller and full.noise_suppressor and full.auto_gain)

    def test_the_chain_runs_and_preserves_length_and_dtype(self):
        front = AudioFrontEnd()
        out = front.process(_voice(0.03, 1))
        self.assertEqual((out.dtype, out.size), (np.float32, 480))

    def test_reference_reaches_the_echo_canceller(self):
        front = AudioFrontEnd(noise_suppression=False, agc=False)
        front.push_reference(np.ones(100, dtype=np.float32))
        assert front.echo_canceller is not None
        self.assertEqual(len(front.echo_canceller._reference), 100)

    def test_push_reference_without_aec_is_a_no_op(self):
        AudioFrontEnd(aec=False).push_reference(np.ones(100, dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
