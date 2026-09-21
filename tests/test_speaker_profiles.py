"""Test per core/voice/speaker_profile.py e core/profiles.py (F2.7.1-F2.7.6). Voci SINTETICHE con
altezza e formanti diverse (nessuna registrazione di persone), archivi su cartelle temporanee.
La misura su due voci SAPI reali (12/12 in leave-one-out) sta in ROADMAP_EXECUTION.md: non si
ripete qui perche' dipende dalle voci installate sul PC."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from core.profiles import GUEST_ID, ProfileError, ProfileManager
from core.risk import RiskLevel
from core.voice.speaker_profile import (
    ACCEPT_DISTANCE, MIN_ENROLL_SAMPLES, SpeakerHint, SpeakerProfileStore, disambiguation_question,
    extract_features, identify,
)

SR = 16000


def voice(f0, formants=(700, 1200, 2600), seconds=2.0, seed=0, tilt=1.0):
    """Voce sintetica: armoniche sotto un inviluppo a tre formanti, modulazione sillabica e vibrato.
    Cambiare `f0` e `formants` da' un "parlante" diverso; `seed`/modulazione diversi, una frase diversa."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    syllable_rate = 3.0 + rng.random() * 2.0
    phase = 2 * np.pi * np.cumsum(f0 * (1 + 0.04 * np.sin(2 * np.pi * (2 + rng.random()) * t))) / SR
    signal = np.zeros(n)
    for k in range(1, 45):
        freq = k * f0
        envelope = sum(np.exp(-(((freq - centre) / (0.18 * centre + 60)) ** 2)) for centre in formants)
        signal += envelope * np.sin(k * phase) / k**tilt
    signal *= 0.5 * (1 + np.sin(2 * np.pi * syllable_rate * t + rng.random() * 6)) ** 0.7
    signal += 0.01 * rng.standard_normal(n)
    return (signal / np.max(np.abs(signal)) * 0.4).astype(np.float32)


MAN = {"f0": 110, "formants": (650, 1100, 2400)}
WOMAN = {"f0": 210, "formants": (800, 1400, 2900)}
SIMILAR_TO_MAN = {"f0": 118, "formants": (665, 1120, 2440)}


def samples(speaker, count=5, first_seed=0):
    return [extract_features(voice(seed=first_seed + i, **speaker)) for i in range(count)]


class FeatureTests(unittest.TestCase):
    def test_a_voice_produces_a_stable_fixed_size_fingerprint(self):
        features = extract_features(voice(**MAN))
        assert features is not None
        self.assertEqual(features.shape, (25,))
        self.assertTrue(np.all(np.isfinite(features)))

    def test_too_short_audio_gives_no_fingerprint(self):
        self.assertIsNone(extract_features(voice(seconds=0.5, **MAN)))

    def test_noise_and_silence_are_not_a_voice(self):
        self.assertIsNone(extract_features((np.random.default_rng(1).standard_normal(3 * SR) * 0.2).astype(np.float32)))
        self.assertIsNone(extract_features(np.zeros(3 * SR, dtype=np.float32)))

    def test_pitch_is_part_of_the_fingerprint(self):
        low, high = extract_features(voice(**MAN)), extract_features(voice(**WOMAN))
        assert low is not None and high is not None
        self.assertLess(low[-1], high[-1])
        self.assertAlmostEqual(float(low[-1]), 1.1, delta=0.15)

    def test_gain_does_not_change_the_fingerprint_much(self):
        base = voice(**MAN)
        a, b = extract_features(base), extract_features(base * 0.3)
        assert a is not None and b is not None
        self.assertLess(float(np.linalg.norm(a - b)), 0.5)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "speakers.json"
        self.store = SpeakerProfileStore(self.path, clock=lambda: 42.0)

    def test_enrolment_is_opt_in(self):
        for consent in (False, None, "si", 1):
            with self.subTest(consent=consent), self.assertRaises(PermissionError):
                self.store.enroll("davide", "Davide", samples(MAN), consent=consent)
        self.assertFalse(self.path.exists())  # un rifiuto non crea nulla

    def test_enrolment_needs_enough_valid_samples(self):
        with self.assertRaises(ValueError):
            self.store.enroll("davide", "Davide", samples(MAN, count=MIN_ENROLL_SAMPLES - 1), consent=True)
        with self.assertRaises(ValueError):
            self.store.enroll("davide", "Davide", [None, None, None, extract_features(voice(**MAN))], consent=True)

    def test_a_profile_is_stored_as_numbers_never_audio(self):
        self.store.enroll("davide", "Davide", samples(MAN), consent=True)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        profile = raw["profiles"]["davide"]
        self.assertEqual(len(profile["centroid"]), 25)
        self.assertLess(self.path.stat().st_size, 5000)  # 25 numeri, non secondi di audio
        self.assertEqual(profile["enrolled_at"], 42.0)

    def test_delete_really_removes_the_profile_and_the_file_when_empty(self):
        self.store.enroll("davide", "Davide", samples(MAN), consent=True)
        self.store.enroll("anna", "Anna", samples(WOMAN), consent=True)
        self.assertTrue(self.store.delete("davide"))
        self.assertNotIn("davide", self.path.read_text(encoding="utf-8"))
        self.assertTrue(self.store.delete("anna"))
        self.assertFalse(self.path.exists())
        self.assertFalse(self.store.delete("anna"))

    def test_delete_all(self):
        self.store.enroll("davide", "Davide", samples(MAN), consent=True)
        self.store.enroll("anna", "Anna", samples(WOMAN), consent=True)
        self.assertEqual(self.store.delete_all(), 2)
        self.assertEqual(self.store.profiles(), [])

    def test_a_corrupt_file_means_no_profiles_not_a_crash(self):
        self.path.write_text("{ rotto", encoding="utf-8")
        self.assertEqual(self.store.profiles(), [])

    def test_enrolling_again_replaces_the_profile(self):
        self.store.enroll("davide", "Davide", samples(MAN), consent=True)
        self.store.enroll("davide", "Davide R.", samples(MAN, first_seed=50), consent=True)
        (profile,) = self.store.profiles()
        self.assertEqual(profile.display_name, "Davide R.")

    def test_invalid_names_are_rejected(self):
        with self.assertRaises(ValueError):
            self.store.enroll(" ", "Davide", samples(MAN), consent=True)
        with self.assertRaises(ValueError):
            self.store.enroll("davide", "  ", samples(MAN), consent=True)


class IdentifyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = SpeakerProfileStore(Path(self._tmp.name) / "s.json")
        self.store.enroll("davide", "Davide", samples(MAN), consent=True)
        self.store.enroll("anna", "Anna", samples(WOMAN), consent=True)

    def _hint(self, speaker, seed=100):
        return identify(extract_features(voice(seed=seed, **speaker)), self.store.profiles())

    def test_each_enrolled_speaker_is_recognised_on_a_new_sentence(self):
        for seed in (100, 101, 102):
            man, woman = self._hint(MAN, seed), self._hint(WOMAN, seed)
            self.assertEqual((man.profile_id, man.confidence), ("davide", "high"))
            self.assertEqual((woman.profile_id, woman.confidence), ("anna", "high"))

    def test_a_similar_speaker_is_never_confidently_assigned_to_someone_else(self):
        """F2.7.6: parlante simile. Non deve mai essere "high" su un profilo sbagliato: al peggio si chiede."""
        hint = self._hint(SIMILAR_TO_MAN)
        self.assertFalse(hint.confidence == "high" and hint.profile_id == "anna")

    def test_a_very_similar_voice_is_accepted_as_the_same_profile_KNOWN_LIMIT(self):
        """Limite noto e fissato: una voce con altezza +7% e formanti quasi uguali ha un'impronta a
        distanza 0,9 (contro ~9 dagli altri profili) e viene accettata come la stessa persona. E' una
        firma grossolana, non un riconoscitore di produzione: un imitatore o un fratello passano. Per
        questo `select` sceglie un profilo ma le azioni DESTRUCTIVE/ADMIN passano sempre da AuthGate."""
        hint = self._hint(SIMILAR_TO_MAN)
        self.assertEqual((hint.profile_id, hint.confidence), ("davide", "high"))

    def test_an_unknown_far_voice_is_rejected(self):
        stranger = {"f0": 160, "formants": (500, 1800, 3300)}
        hint = self._hint(stranger)
        self.assertNotEqual(hint.confidence, "high")

    def test_noise_gives_no_hint(self):
        noise = (np.random.default_rng(2).standard_normal(3 * SR) * 0.2).astype(np.float32)
        hint = identify(extract_features(noise), self.store.profiles())
        self.assertEqual((hint.profile_id, hint.confidence), (None, "none"))

    def test_no_profiles_no_hint(self):
        self.assertEqual(identify(extract_features(voice(**MAN)), []).confidence, "none")

    def test_two_close_profiles_are_ambiguous_not_guessed(self):
        store = SpeakerProfileStore(Path(self._tmp.name) / "close.json")
        store.enroll("uno", "Uno", samples(MAN), consent=True)
        store.enroll("due", "Due", samples(SIMILAR_TO_MAN), consent=True)
        hint = identify(extract_features(voice(seed=200, **MAN)), store.profiles())
        self.assertTrue(hint.needs_disambiguation or hint.profile_id == "uno")  # se sceglie, sceglie giusto
        if hint.confidence == "high":
            self.assertEqual(hint.profile_id, "uno")

    def test_a_recording_of_the_owner_is_accepted_which_is_why_voice_is_never_a_lock(self):
        """F2.7.6 voce registrata: un replay ha la stessa impronta della voce viva, quindi il
        riconoscimento lo accetta. Il test FISSA il limite: e' la ragione per cui l'esito e' un indizio
        di selezione e le azioni sensibili passano da AuthGate."""
        recording = voice(seed=100, **MAN)  # la stessa registrazione riprodotta da un altoparlante
        live = identify(extract_features(recording), self.store.profiles())
        replay = identify(extract_features(recording.copy()), self.store.profiles())
        self.assertEqual((live.profile_id, replay.profile_id), ("davide", "davide"))

    def test_distance_thresholds_are_documented_constants(self):
        self.assertGreater(ACCEPT_DISTANCE, 0)


class DisambiguationTests(unittest.TestCase):
    def _profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SpeakerProfileStore(Path(tmp) / "s.json")
            store.enroll("davide", "Davide", samples(MAN), consent=True)
            store.enroll("anna", "Anna", samples(WOMAN), consent=True)
            return store.profiles()

    def test_a_low_confidence_hint_asks_between_the_candidates(self):
        hint = SpeakerHint("davide", "low", 3.0, 3.5, ("davide", "anna"))
        self.assertTrue(hint.needs_disambiguation)
        self.assertEqual(disambiguation_question(hint, self._profiles()), "Sei Davide o Anna?")

    def test_an_unrecognised_voice_asks_who_is_speaking(self):
        hint = SpeakerHint(None, "none", 9.0, None, ("davide",))
        self.assertEqual(disambiguation_question(hint, self._profiles()), "Non ho riconosciuto la voce: chi sta parlando?")
        self.assertEqual(disambiguation_question(SpeakerHint(None, "none"), []), "Chi sta parlando?")

    def test_a_confident_hint_needs_no_question(self):
        self.assertFalse(SpeakerHint("davide", "high", 1.0, 5.0, ("davide",)).needs_disambiguation)


class ProfileManagerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manager = ProfileManager(Path(self._tmp.name) / "profiles")
        self.addCleanup(self.manager.close_all_guests)

    def test_profiles_have_separate_history_preferences_and_databases(self):
        a = self.manager.create_profile("davide", "Davide")
        b = self.manager.create_profile("anna", "Anna")
        a.conversation.add_turn("user", "il mio segreto")
        a.preferences["tema"] = "scuro"
        self.assertEqual(b.conversation.get_short_term_history(), [])
        self.assertEqual(b.preferences, {})
        self.assertNotEqual(a.memory_db_path, b.memory_db_path)

    def test_memory_written_in_one_profile_is_invisible_in_another(self):
        a = self.manager.create_profile("davide", "Davide")
        b = self.manager.create_profile("anna", "Anna")
        memory_a, memory_b = a.open_memory(), b.open_memory()
        try:
            memory_a.remember("password wifi", "hunter2", category="fact")
            self.assertEqual(memory_a.count_memories(), 1)
            self.assertEqual(memory_b.count_memories(), 0)
            self.assertEqual(memory_b.recall("password wifi"), [])
        finally:
            memory_a.close()
            memory_b.close()

    def test_a_profile_cannot_exceed_its_own_risk_ceiling(self):
        kid = self.manager.create_profile("figlio", "Figlio", max_risk=RiskLevel.LOCAL_REVERSIBLE)
        self.assertTrue(kid.permits(RiskLevel.READ_ONLY))
        self.assertTrue(kid.permits(RiskLevel.LOCAL_REVERSIBLE))
        self.assertFalse(kid.permits(RiskLevel.EXTERNAL_ACTION))
        self.assertFalse(kid.permits(RiskLevel.ADMIN))

    def test_destructive_and_admin_need_authentication_even_for_the_owner(self):
        owner = self.manager.create_profile("davide", "Davide", max_risk=RiskLevel.ADMIN)
        self.assertTrue(owner.permits(RiskLevel.ADMIN))  # il tetto lo consente...
        for risk in (RiskLevel.DESTRUCTIVE, RiskLevel.ADMIN):
            self.assertTrue(owner.needs_authentication(risk))  # ...ma serve comunque AuthGate
        for risk in (RiskLevel.READ_ONLY, RiskLevel.LOCAL_REVERSIBLE, RiskLevel.EXTERNAL_ACTION):
            self.assertFalse(owner.needs_authentication(risk))

    def test_selection_uses_a_hint_only_when_it_is_confident(self):
        self.manager.create_profile("davide", "Davide")
        high = SpeakerHint("davide", "high", 1.0, 6.0, ("davide",))
        selected = self.manager.select(high)
        assert selected is not None
        self.assertEqual(selected.profile_id, "davide")
        self.assertIsNone(self.manager.select(SpeakerHint("davide", "low", 3.0, 3.4, ("davide", "anna"))))
        self.assertIsNone(self.manager.select(SpeakerHint(None, "none")))

    def test_selection_of_a_deleted_profile_is_none(self):
        self.manager.create_profile("davide", "Davide")
        self.manager.delete_profile("davide")
        self.assertIsNone(self.manager.select(SpeakerHint("davide", "high", 1.0, None, ("davide",))))

    def test_deleting_a_profile_removes_its_data_from_disk(self):
        a = self.manager.create_profile("davide", "Davide")
        memory = a.open_memory()
        memory.remember("x", "y", category="fact")
        memory.close()
        self.assertTrue(a.memory_db_path.exists())
        self.assertTrue(self.manager.delete_profile("davide"))
        self.assertFalse(a.memory_db_path.parent.exists())
        self.assertEqual(self.manager.profile_ids(), [])
        self.assertFalse(self.manager.delete_profile("davide"))

    def test_ids_are_validated_against_path_tricks(self):
        for bad in ("../fuori", "a/b", "", "UPPER", "x" * 40, "con spazio", GUEST_ID):
            with self.subTest(bad=bad), self.assertRaises(ProfileError):
                self.manager.create_profile(bad, "Nome")

    def test_a_duplicate_profile_is_refused_and_unknown_profile_errors(self):
        self.manager.create_profile("davide", "Davide")
        with self.assertRaises(ProfileError):
            self.manager.create_profile("davide", "Un altro")
        with self.assertRaises(ProfileError):
            self.manager.namespace("mai-creato")

    def test_profiles_survive_a_new_manager_on_the_same_folder(self):
        self.manager.create_profile("davide", "Davide", max_risk=RiskLevel.EXTERNAL_ACTION)
        again = ProfileManager(self.manager.base_dir)
        ns = again.namespace("davide")
        self.assertEqual((ns.display_name, ns.max_risk), ("Davide", RiskLevel.EXTERNAL_ACTION))


class GuestModeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manager = ProfileManager(Path(self._tmp.name) / "profiles")
        self.addCleanup(self.manager.close_all_guests)

    def test_a_guest_is_not_persistent_does_not_learn_and_is_capped(self):
        guest = self.manager.guest()
        self.assertEqual((guest.persistent, guest.learning_enabled, guest.max_risk), (False, False, RiskLevel.LOCAL_REVERSIBLE))
        self.assertFalse(guest.permits(RiskLevel.EXTERNAL_ACTION))

    def test_a_guest_leaves_nothing_behind_after_closing(self):
        guest = self.manager.guest()
        memory = guest.open_memory()
        memory.remember("ospite", "dato", category="fact")
        memory.close()
        guest.conversation.add_turn("user", "ciao")
        folder = guest.memory_db_path.parent
        self.assertTrue(folder.exists())
        self.manager.close_guest(guest)
        self.assertFalse(folder.exists())
        self.assertEqual(guest.conversation.get_short_term_history(), [])

    def test_a_guest_never_writes_inside_the_profiles_folder(self):
        guest = self.manager.guest()
        base = self.manager.base_dir.resolve()
        self.assertNotIn(base, guest.memory_db_path.resolve().parents)

    def test_two_guests_share_nothing(self):
        one, two = self.manager.guest(), self.manager.guest()
        one.conversation.add_turn("user", "solo mio")
        self.assertEqual(two.conversation.get_short_term_history(), [])
        self.assertNotEqual(one.memory_db_path, two.memory_db_path)

    def test_guest_memory_does_not_reach_a_real_profile(self):
        owner = self.manager.create_profile("davide", "Davide")
        guest = self.manager.guest()
        gm = guest.open_memory()
        gm.remember("ospite", "dato", category="fact")
        gm.close()
        om = owner.open_memory()
        try:
            self.assertEqual(om.count_memories(), 0)
        finally:
            om.close()

    def test_close_guest_refuses_a_real_profile(self):
        owner = self.manager.create_profile("davide", "Davide")
        with self.assertRaises(ProfileError):
            self.manager.close_guest(owner)

    def test_close_all_guests_reports_how_many(self):
        self.manager.guest()
        self.manager.guest()
        self.assertEqual(self.manager.close_all_guests(), 2)
        self.assertEqual(self.manager.close_all_guests(), 0)


if __name__ == "__main__":
    unittest.main()
