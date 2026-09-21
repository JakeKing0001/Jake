"""Test per core/voice/voice_consent.py (F2.5.6) e core/voice/audio_profile.py (F2.5.7). Nessun
audio: registro su file temporaneo, profili come funzioni pure."""
import json
import tempfile
import unittest
from pathlib import Path

from core.voice.audio_profile import (
    PROFILES, apply_to_provider, classify_output_device, effective_parameters, profile_for_device,
)
from core.voice.speech_text import STYLES
from core.voice.voice_consent import BASES, VoiceConsentError, VoiceConsentRegistry, main


class ConsentRegistryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "consent.json"
        self.now = [1000.0]
        self.registry = VoiceConsentRegistry(self.path, clock=lambda: self.now[0])

    def _grant(self, name="jake_the_dog", **overrides):
        args = {"subject": "personaggio animato", "basis": "fictional-character", "statement": "Personaggio di fantasia, uso personale."}
        args.update(overrides)
        return self.registry.grant(name, **args)

    def test_nothing_is_allowed_by_default(self):
        self.assertFalse(self.registry.is_allowed("jake_the_dog"))
        with self.assertRaises(VoiceConsentError) as ctx:
            self.registry.require("jake_the_dog")
        self.assertIn("voice_consent grant jake_the_dog", str(ctx.exception))  # dice come si concede

    def test_a_grant_allows_only_that_model(self):
        self._grant()
        self.assertTrue(self.registry.is_allowed("jake_the_dog"))
        self.assertFalse(self.registry.is_allowed("altra_voce"))
        self.registry.require("jake_the_dog")  # non solleva

    def test_grant_is_persisted_across_instances(self):
        self._grant()
        self.assertTrue(VoiceConsentRegistry(self.path).is_allowed("jake_the_dog"))

    def test_revocation_takes_effect_immediately_and_keeps_the_history(self):
        self._grant()
        self.now[0] = 2000.0
        self.assertTrue(self.registry.revoke("jake_the_dog"))
        self.assertFalse(self.registry.is_allowed("jake_the_dog"))
        (record,) = self.registry.records()
        self.assertEqual((record.granted_at, record.revoked_at), (1000.0, 2000.0))

    def test_revoking_twice_or_an_unknown_model_returns_false(self):
        self.assertFalse(self.registry.revoke("mai_esistita"))
        self._grant()
        self.registry.revoke("jake_the_dog")
        self.assertFalse(self.registry.revoke("jake_the_dog"))

    def test_a_new_grant_after_revocation_reactivates(self):
        self._grant()
        self.registry.revoke("jake_the_dog")
        self._grant()
        self.assertTrue(self.registry.is_allowed("jake_the_dog"))

    def test_invalid_grants_are_rejected(self):
        bad = [
            {"name": " "}, {"subject": ""}, {"basis": "mi-va-bene"}, {"statement": "ok"}, {"statement": "   "},
        ]
        for overrides in bad:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self._grant(**overrides)
        self.assertFalse(self.path.exists())  # un rifiuto non lascia tracce

    def test_every_basis_is_explained(self):
        self.assertTrue(all(BASES.values()))
        self.assertIn("consenting-person", BASES)

    def test_a_corrupt_file_denies_everything_and_is_never_silently_overwritten(self):
        self.path.write_text("{ non json", encoding="utf-8")
        self.assertFalse(self.registry.is_allowed("jake_the_dog"))
        self._grant("nuova")  # una nuova concessione esplicita e' possibile...
        self.assertTrue(self.registry.is_allowed("nuova"))
        # ...ma il file rotto e' stato conservato, non buttato via
        self.assertEqual(self.path.with_suffix(".corrupt").read_text(encoding="utf-8"), "{ non json")

    def test_a_structurally_wrong_file_denies(self):
        self.path.write_text(json.dumps({"records": {"x": {"model_name": "x"}}}), encoding="utf-8")
        self.assertFalse(self.registry.is_allowed("x"))

    def test_no_temporary_file_is_left_behind(self):
        self._grant()
        self.assertEqual([p.name for p in Path(self._tmp.name).iterdir()], ["consent.json"])


class ConsentCliTests(unittest.TestCase):
    def test_cli_grant_list_revoke_roundtrip(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            with mock.patch("core.voice.voice_consent.DEFAULT_PATH", path), \
                    mock.patch("core.voice.voice_consent.VoiceConsentRegistry", lambda: VoiceConsentRegistry(path)):
                main(["grant", "voce_x", "--subject", "me stesso", "--basis", "self", "--statement", "E' la mia voce, la uso io."])
                self.assertTrue(VoiceConsentRegistry(path).is_allowed("voce_x"))
                main(["revoke", "voce_x"])
                self.assertFalse(VoiceConsentRegistry(path).is_allowed("voce_x"))

    def test_the_assistant_has_no_code_path_that_grants_consent(self):
        """Il consenso lo da' un umano da terminale: nessun modulo dell'assistente (core/skills)
        deve chiamare `.grant(` del registro."""
        root = Path(__file__).resolve().parent.parent
        offenders = []
        for folder in ("core", "skills", "plugins", "main.py"):
            target = root / folder
            files = [target] if target.is_file() else list(target.rglob("*.py"))
            for file in files:
                if file.name == "voice_consent.py":
                    continue
                text = file.read_text(encoding="utf-8", errors="ignore")
                if "VoiceConsentRegistry" in text and ".grant(" in text:
                    offenders.append(str(file.relative_to(root)))
        self.assertEqual(offenders, [])


class OutputProfileTests(unittest.TestCase):
    def test_device_names_are_classified(self):
        cases = {
            "Headphones (Realtek(R) Audio)": "headphones",
            "Cuffie (WH-1000XM4)": "headphones",
            "Headset Earphone (Bluetooth Hands-Free)": "headphones",
            "Bluetooth Speaker": "bluetooth",
            "Speakers (Realtek High Definition Audio)": "laptop-speaker",
            "Altoparlanti (Realtek)": "laptop-speaker",
            "LG TV (HDMI)": "tv",
            "Dispositivo sconosciuto": "unknown",
            "": "unknown",
            None: "unknown",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(classify_output_device(name), expected)

    def test_every_profile_is_sane(self):
        for profile in PROFILES.values():
            self.assertTrue(0 < profile.volume_scale <= 1.0)
            self.assertLessEqual(abs(profile.rate_delta_percent), 20)

    def test_headphones_are_quieter_than_speakers(self):
        self.assertLess(PROFILES["headphones"].volume_scale, PROFILES["laptop-speaker"].volume_scale)

    def test_tv_is_slower(self):
        self.assertLess(PROFILES["tv"].rate_delta_percent, 0)

    def test_effective_parameters_combine_style_and_device(self):
        volume, rate = effective_parameters(STYLES["whisper"], PROFILES["headphones"])
        self.assertAlmostEqual(volume, 0.35 * 0.6, places=2)
        self.assertEqual(rate, -10)

    def test_volume_is_clamped_between_a_floor_and_one(self):
        from core.voice.speech_text import SpeechStyle

        self.assertEqual(effective_parameters(SpeechStyle("x", None, 0, 0.0), PROFILES["headphones"])[0], 0.05)
        self.assertEqual(effective_parameters(SpeechStyle("x", None, 0, 5.0), PROFILES["unknown"])[0], 1.0)

    def test_rate_is_clamped(self):
        from core.voice.speech_text import SpeechStyle

        self.assertEqual(effective_parameters(SpeechStyle("x", None, -200, 1.0), PROFILES["tv"])[1], -50)

    def test_apply_to_provider_passes_the_effective_values(self):
        class P:
            def set_speech_params(self, volume, rate):
                self.got = (volume, rate)
                return True

        provider = P()
        self.assertTrue(apply_to_provider(provider, STYLES["normal"], "Cuffie USB"))
        self.assertEqual(provider.got, (0.6, 0))

    def test_a_provider_without_support_is_reported_not_crashed(self):
        self.assertFalse(apply_to_provider(object(), STYLES["normal"], "Speakers"))

    def test_the_only_inputs_are_a_device_name_and_a_chosen_style(self):
        """F2.5.7: nessun ingresso che descriva chi parla o il suo stato d'animo."""
        import inspect

        self.assertEqual(list(inspect.signature(profile_for_device).parameters), ["device_name"])
        self.assertEqual(list(inspect.signature(apply_to_provider).parameters), ["provider", "style", "device_name"])


if __name__ == "__main__":
    unittest.main()
