"""Presenza ambientale dell'HUD (expanded | mini | hidden): frasi esatte, skill sul protocollo esistente e ripristino
alla wake word reale. La logica QML (auto-mini, geometria, notifiche) e' in hud/native/tests/presence_tests.cpp."""
import unittest
from unittest import mock

from core.event_bus import EventBus
from core.hud_protocol import EventType
from core.nlu.examples import ExampleStore
from core.nlu.normalizer import TranscriptNormalizer
from skills.session_control import HudPresentationSkill
from tests.test_wake_word_session import _mock_session
from tests.voice_session_support import VoiceSessionTestCase


class _Core:
    """Quanto basta di JakeCore: bus vero, corsia esatta vera, CompanionMixin.hud_presentation_command vero."""

    def __init__(self):
        from core.companion_manager import CompanionMixin

        self.event_bus = EventBus()
        self.example_store = ExampleStore()
        self.normalizer = TranscriptNormalizer()
        self.hud_presentation_command = CompanionMixin.hud_presentation_command.__get__(self)


class PresentationPhrasesTests(unittest.TestCase):
    def test_spoken_phrases_take_the_exact_lane_to_the_right_mode(self):
        core = _Core()
        expected = {
            "Rimpicciolisciti.": "mini", "fatti piccolo": "mini", "modalità mini": "mini",
            "mettiti nell'angolo": "mini", "lascia solo l'orb": "mini",
            "ingrandisciti": "expanded", "torna grande": "expanded", "espanditi": "expanded",
            "mostra tutto": "expanded", "mostrati": "expanded", "riappari": "expanded",
            "riduciti a icona": "hidden", "nasconditi": "hidden", "sparisci dallo schermo": "hidden",
            "nascondi l'HUD": "hidden",
        }
        for text, mode in expected.items():
            with self.subTest(text=text):
                self.assertEqual(core.hud_presentation_command(text), mode)

    def test_garbled_or_unrelated_speech_never_becomes_a_presentation_command(self):
        core = _Core()
        for text in ("rimpicciolisci citi", "riduci a icona la finestra di opera", "ingrandisci il testo",
                     "mostra tutto il file", "che ore sono", ""):
            with self.subTest(text=text):
                self.assertIsNone(core.hud_presentation_command(text))


class HudPresentationSkillTests(unittest.TestCase):
    def _run(self, mode):
        core = _Core()
        queue = core.event_bus.subscribe()
        result = HudPresentationSkill(core).execute({"mode": mode})
        return result, (queue.get_nowait() if not queue.empty() else None)

    def test_mini_and_expanded_reuse_hud_show_and_hidden_reuses_hud_hide(self):
        result, event = self._run("mini")
        self.assertTrue(result.success)
        self.assertEqual((event.type, event.payload), (EventType.HUD_SHOW, {"presentation": "mini", "reason": "manual"}))
        _, event = self._run("expanded")
        self.assertEqual(event.payload["presentation"], "expanded")
        # nascosto = solo l'evento verso l'HUD: la skill non tocca voce, microfono, processo dell'HUD o server
        _, event = self._run("hidden")
        self.assertEqual((event.type, event.payload), (EventType.HUD_HIDE, {"reason": "manual"}))

    def test_an_unknown_mode_publishes_nothing(self):
        result, event = self._run("gigante")
        self.assertEqual((result.success, result.error, event), (False, "INVALID_VALUE", None))


class WakeRestoresHudTests(VoiceSessionTestCase):
    def _session(self, heard, presentation_command=None):
        session = _mock_session()
        session.jake_core.hud_presentation_command.return_value = presentation_command
        session.stt_provider.transcribe.return_value = heard
        with mock.patch.object(session, "_process_command"):
            session._handle_utterance(object())
        return [c.args[0] for c in session.jake_core.event_bus.publish.call_args_list
                if c.args[0].type in (EventType.HUD_SHOW, EventType.HUD_HIDE)]

    def test_the_wake_word_alone_brings_the_hud_back_expanded_and_listening(self):
        events = self._session("Jake")
        self.assertEqual([(e.type, e.payload) for e in events],
                         [(EventType.HUD_SHOW, {"presentation": "expanded", "reason": "wake"})])

    def test_wake_with_a_normal_command_also_restores(self):
        self.assertEqual(len(self._session("Jake, che ore sono")), 1)

    def test_wake_with_a_presentation_command_does_not_flash_big(self):
        self.assertEqual(self._session("Jake, rimpicciolisciti", presentation_command="mini"), [])

    def test_no_restore_without_a_real_wake(self):
        # "Jake" a meta' di una frase non rivolta a lui, o nessun "Jake": l'HUD resta com'e'
        self.assertEqual(self._session("ho parlato con Jake ieri"), [])
        self.assertEqual(self._session("che ore sono"), [])


if __name__ == "__main__":
    unittest.main()
