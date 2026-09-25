"""Arruolamento vocale da terminale (F2.7.1): consenso esplicito, id validato prima di registrare,
almeno 3 campioni validi, tentativi limitati, nessun audio su disco."""
import tempfile
import unittest
from pathlib import Path

import numpy as np

from core.profiles import ProfileManager
from core.voice.speaker_enroll import EnrollmentAborted, enroll_speaker
from core.voice.speaker_profile import SpeakerProfileStore
from tests.test_speaker_profiles import MAN, voice


class EnrollSpeakerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = SpeakerProfileStore(path=self.root / "speaker_profiles.json")
        self.manager = ProfileManager(base_dir=self.root / "profiles")
        self.recorded = []

    def _recorder(self, audio_for):
        def record(phrase):
            audio = audio_for(len(self.recorded))
            self.recorded.append(phrase)
            return audio
        return record

    def _enroll(self, profile_id="davide", answers=("CONSENTO",), audio_for=None, **kwargs):
        answers = list(answers)
        return enroll_speaker(
            profile_id, "Davide", prompt=lambda _text: answers.pop(0) if answers else "",
            record=self._recorder(audio_for or (lambda i: voice(seed=i, **MAN))),
            store=self.store, manager=self.manager, **kwargs,
        )

    def test_consent_creates_a_fingerprint_only_profile_and_its_memory_namespace(self):
        profile = self._enroll()
        self.assertEqual(profile.samples, 5)
        self.assertIn("davide", self.manager.profile_ids())
        files = sorted(p.name for p in self.root.rglob("*") if p.is_file())
        self.assertFalse([name for name in files if name.lower().endswith((".wav", ".npy", ".raw", ".mp3"))])
        self.assertLess((self.root / "speaker_profiles.json").stat().st_size, 4096, "solo numeri, mai audio")

    def test_without_the_exact_consent_word_nothing_is_recorded_or_saved(self):
        with self.assertRaises(EnrollmentAborted):
            self._enroll(answers=("si",))
        self.assertEqual(self.recorded, [])
        self.assertEqual(self.store.profiles(), [])
        self.assertEqual(self.manager.profile_ids(), [])

    def test_an_invalid_id_is_rejected_before_any_recording(self):
        for bad in ("Davide", "../fuori", "guest", ""):
            with self.subTest(bad=bad), self.assertRaises(EnrollmentAborted):
                self._enroll(profile_id=bad)
        self.assertEqual(self.recorded, [])
        self.assertEqual(self.store.profiles(), [])

    def test_a_silent_microphone_aborts_after_limited_attempts_without_saving(self):
        with self.assertRaises(EnrollmentAborted):
            self._enroll(audio_for=lambda i: np.zeros(16000, dtype=np.float32), max_attempts=2)
        self.assertEqual(len(self.recorded), 10, "5 frasi x 2 tentativi, poi basta")
        self.assertEqual(self.store.profiles(), [])
        self.assertEqual(self.manager.profile_ids(), [])

    def test_an_invalid_sample_is_retried_and_three_valid_phrases_are_enough(self):
        silent = np.zeros(16000, dtype=np.float32)
        # frasi 1-2 sempre mute (2 tentativi ciascuna), frasi 3-5 valide al primo colpo
        pattern = [silent, silent, silent, silent]
        profile = self._enroll(
            audio_for=lambda i: pattern[i] if i < len(pattern) else voice(seed=i, **MAN), max_attempts=2,
        )
        self.assertEqual(profile.samples, 3)

    def test_recognition_never_grants_authentication(self):
        from core.risk import RiskLevel

        self._enroll()
        namespace = self.manager.namespace("davide")
        self.assertTrue(namespace.needs_authentication(RiskLevel.DESTRUCTIVE))
        self.assertTrue(namespace.needs_authentication(RiskLevel.ADMIN))


if __name__ == "__main__":
    unittest.main()
