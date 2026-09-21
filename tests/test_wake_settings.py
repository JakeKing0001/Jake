"""Test per core/voice/wake_settings.py (F2.3.1, F2.3.2): impostazioni per dispositivo validate e
calibrazione del rumore che vede solo livelli, mai audio."""
import unittest

import numpy as np

from core.voice.wake_settings import (
    SETTINGS_KEY, NoiseCalibrator, SettingsError, WakeSettings, apply_calibration, load_wake_settings,
    settings_from_dict,
)


class FakeConfig:
    def __init__(self, table=None):
        self._table = table

    def get(self, key, default=None):
        return self._table if key == SETTINGS_KEY and self._table is not None else default


class WakeSettingsValidationTests(unittest.TestCase):
    def test_defaults_are_valid_and_match_the_historical_behaviour(self):
        s = WakeSettings()
        self.assertIn("jake", s.wake_words)
        self.assertEqual((s.wake_max_distance, s.vad_aggressiveness, s.silence_ms, s.follow_up_seconds), (1, 2, 700, 6.0))

    def test_out_of_range_values_are_rejected(self):
        bad = [
            {"wake_max_distance": 3}, {"vad_aggressiveness": 4}, {"silence_ms": 100}, {"silence_ms": 9000},
            {"follow_up_seconds": -1}, {"follow_up_seconds": 99}, {"energy_gate": 0.9},
        ]
        for overrides in bad:
            with self.subTest(overrides=overrides), self.assertRaises(SettingsError):
                settings_from_dict(overrides)

    def test_wake_words_must_be_letters_and_not_everyday_words(self):
        for words in (["ok"], ["apri"], ["j4ke"], ["a"], ["x" * 21], [""], []):
            with self.subTest(words=words), self.assertRaises(SettingsError):
                settings_from_dict({"wake_words": words})
        self.assertEqual(settings_from_dict({"wake_words": ["Computer", " Ada "]}).wake_words, ("computer", "ada"))

    def test_a_typo_in_a_key_is_an_error_not_silently_ignored(self):
        with self.assertRaises(SettingsError):
            settings_from_dict({"vad_agressiveness": 3})

    def test_wrong_types_are_rejected(self):
        for overrides in ({"silence_ms": "700"}, {"vad_aggressiveness": True}, {"wake_words": "jake"}, {"follow_up_seconds": None}):
            with self.subTest(overrides=overrides), self.assertRaises(SettingsError):
                settings_from_dict(overrides)


class SensitivityTests(unittest.TestCase):
    def test_presets_set_aggressiveness_and_distance_together(self):
        self.assertEqual(
            (settings_from_dict({"sensitivity": "high"}).vad_aggressiveness, settings_from_dict({"sensitivity": "high"}).wake_max_distance),
            (1, 2),
        )
        low = settings_from_dict({"sensitivity": "low"})
        self.assertEqual((low.vad_aggressiveness, low.wake_max_distance), (3, 0))

    def test_explicit_values_win_over_the_preset(self):
        s = settings_from_dict({"sensitivity": "low", "vad_aggressiveness": 1})
        self.assertEqual((s.vad_aggressiveness, s.wake_max_distance), (1, 0))

    def test_unknown_preset_is_rejected(self):
        with self.assertRaises(SettingsError):
            settings_from_dict({"sensitivity": "extreme"})


class PerDeviceLoadingTests(unittest.TestCase):
    TABLE = {
        "default": {"silence_ms": 900},
        "Cuffie USB": {"sensitivity": "low", "follow_up_seconds": 10},
        "Microfono portatile": {"vad_aggressiveness": 3, "energy_gate": 0.02},
    }

    def test_a_device_inherits_default_then_applies_its_own(self):
        s = load_wake_settings(FakeConfig(self.TABLE), "Cuffie USB")
        self.assertEqual((s.silence_ms, s.follow_up_seconds, s.vad_aggressiveness), (900, 10, 3))

    def test_devices_do_not_leak_into_each_other(self):
        self.assertEqual(load_wake_settings(FakeConfig(self.TABLE), "Microfono portatile").follow_up_seconds, 6.0)
        self.assertEqual(load_wake_settings(FakeConfig(self.TABLE), "Cuffie USB").energy_gate, 0.0)

    def test_an_unknown_device_gets_only_the_default_section(self):
        s = load_wake_settings(FakeConfig(self.TABLE), "Satellite cucina")
        self.assertEqual((s.silence_ms, s.vad_aggressiveness), (900, 2))

    def test_no_configuration_gives_the_defaults(self):
        self.assertEqual(load_wake_settings(FakeConfig(None), "x"), WakeSettings())
        self.assertEqual(load_wake_settings(None, "x"), WakeSettings())

    def test_a_bad_table_or_device_entry_raises_a_clear_error(self):
        with self.assertRaises(SettingsError):
            load_wake_settings(FakeConfig(["non", "un", "oggetto"]), "x")
        with self.assertRaises(SettingsError):
            load_wake_settings(FakeConfig({"Cuffie": {"silence_ms": 5}}), "Cuffie")


class CalibrationTests(unittest.TestCase):
    def _fill(self, level, n=60, jitter=0.0):
        calibrator = NoiseCalibrator()
        rng = np.random.default_rng(3)
        for _ in range(n):
            calibrator.add_level(max(0.0, level + float(rng.normal(0, jitter))))
        return calibrator

    def test_not_enough_frames_gives_no_result(self):
        self.assertIsNone(self._fill(0.001, n=10).result())

    def test_a_quiet_room(self):
        result = self._fill(0.001, jitter=0.0002).result()
        assert result is not None
        self.assertEqual((result.quality, result.recommended_aggressiveness, result.recommended_energy_gate), ("quiet", 2, 0.0))

    def test_a_moderate_room_raises_the_gate(self):
        result = self._fill(0.01, jitter=0.002).result()
        assert result is not None
        self.assertEqual(result.quality, "moderate")
        self.assertGreater(result.recommended_energy_gate, 0.01)

    def test_a_noisy_room_asks_for_the_strictest_vad(self):
        result = self._fill(0.04, jitter=0.005).result()
        assert result is not None
        self.assertEqual((result.quality, result.recommended_aggressiveness), ("noisy", 3))

    def test_speech_during_calibration_invalidates_it(self):
        calibrator = self._fill(0.002, n=55, jitter=0.0003)
        for _ in range(10):
            calibrator.add_level(0.3)  # qualcuno ha parlato
        self.assertIsNone(calibrator.result())

    def test_only_numbers_are_ever_stored_never_audio(self):
        calibrator = NoiseCalibrator(min_frames=1)
        frame = (np.random.default_rng(1).standard_normal(480) * 500).astype(np.int16)
        calibrator.add_frame(frame)
        self.assertEqual(len(calibrator.levels), 1)
        self.assertTrue(all(isinstance(level, float) for level in calibrator.levels))
        self.assertFalse(any(isinstance(v, np.ndarray) for v in vars(calibrator).values()))

    def test_add_frame_computes_rms(self):
        calibrator = NoiseCalibrator(min_frames=1)
        calibrator.add_frame(np.full(480, 3276, dtype=np.int16))
        self.assertAlmostEqual(calibrator.levels[0], 3276 / 32768, places=3)

    def test_invalid_levels_are_rejected(self):
        calibrator = NoiseCalibrator()
        for bad in (-0.1, float("nan"), "0.1", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                calibrator.add_level(bad)

    def test_levels_above_one_are_clamped(self):
        calibrator = NoiseCalibrator()
        calibrator.add_level(5.0)
        self.assertEqual(calibrator.levels, [1.0])


class ApplyCalibrationTests(unittest.TestCase):
    def _calibration(self, level, jitter):
        calibrator = NoiseCalibrator()
        rng = np.random.default_rng(9)
        for _ in range(60):
            calibrator.add_level(max(0.0, level + float(rng.normal(0, jitter))))
        result = calibrator.result()
        assert result is not None
        return result

    def test_a_noisy_room_makes_listening_stricter(self):
        s = apply_calibration(WakeSettings(), self._calibration(0.04, 0.005))
        self.assertEqual(s.vad_aggressiveness, 3)
        self.assertGreater(s.energy_gate, 0.04)

    def test_calibration_never_makes_listening_more_permissive_than_the_user_chose(self):
        strict = WakeSettings(vad_aggressiveness=3, energy_gate=0.05)
        s = apply_calibration(strict, self._calibration(0.001, 0.0002))
        self.assertEqual((s.vad_aggressiveness, s.energy_gate), (3, 0.05))

    def test_other_fields_are_untouched(self):
        base = WakeSettings(silence_ms=1200, wake_words=("computer",))
        s = apply_calibration(base, self._calibration(0.04, 0.005))
        self.assertEqual((s.silence_ms, s.wake_words), (1200, ("computer",)))


if __name__ == "__main__":
    unittest.main()
