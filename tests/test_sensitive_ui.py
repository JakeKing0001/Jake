"""F3.4.3 nelle skill di input: un click/tasto dichiarato sensibile (effect: send/submit/upload/delete/
purchase) passa SEMPRE dalla policy e dalla conferma dell'utente PRIMA di muovere mouse o tastiera.
Nessuna deduzione dal testo del bottone: senza `effect` il comportamento resta quello di sempre."""
import unittest
from unittest import mock

from core.command import Command
from core.computer_agent import ComputerActionResult
from core.computer_use.sensitive_ui import SENSITIVE_UI_EFFECTS, gate_sensitive_ui_action
from core.policy_engine import PolicyEngine
from core.risk import RiskLevel, risk_of
from skills.keyboard_control import PressKeySkill, TypeTextSkill
from skills.mouse_control import ClickMouseSkill
from skills.screen_click import ClickElementSkill, ClickTextSkill
from tests.test_jake_core_pipeline import FakeRegistry, _JakeCoreTestCase


class _Agent:
    def __init__(self):
        self.clicks = []

    def observe(self):
        return [{"text": "Invia", "line": 0, "x": 10, "y": 10, "w": 40, "h": 20}]

    def locate_text(self, text, words=None):
        return {"x": 30, "y": 20, "matched": "Invia", "score": 1.0}

    def click_point(self, x, y, button="left", matched=None):
        self.clicks.append((x, y))
        return ComputerActionResult(success=True, x=x, y=y, matched=matched, verified=True)


def _skills():
    agent = _Agent()
    return {
        "CLICK_TEXT": (ClickTextSkill(computer_agent=agent), {"text": "Invia"}, agent),
        "CLICK_ELEMENT": (ClickElementSkill(vision_provider=None, computer_agent=agent), {"description": "Invia"}, agent),
        "CLICK_MOUSE": (ClickMouseSkill(), {"x": 5, "y": 5}, None),
        "TYPE_TEXT": (TypeTextSkill(), {"text": "ciao"}, None),
        "PRESS_KEY": (PressKeySkill(), {"keys": "enter"}, None),
    }


class GateTests(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch("pyautogui.click"), mock.patch("pyautogui.doubleClick"), mock.patch("pyautogui.press"),
                   mock.patch("pyautogui.hotkey"), mock.patch("keyboard.write")]
        self.mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    def _no_input(self, agent):
        for m in self.mocks:
            m.assert_not_called()
        if agent is not None:
            self.assertEqual(agent.clicks, [])

    def test_a_declared_sensitive_action_asks_confirmation_before_any_input(self):
        for intent, (skill, params, agent) in _skills().items():
            for effect in SENSITIVE_UI_EFFECTS:
                with self.subTest(intent=intent, effect=effect):
                    skill.policy_engine = PolicyEngine()
                    result = skill.execute({**params, "effect": effect})
                    self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
                    self.assertTrue(result.data["confirm_parameters"]["confirmed"])
                    self.assertIn("Confermi", result.data["message"])
                    self._no_input(agent)

    def test_after_confirmation_the_action_really_runs(self):
        for intent, (skill, params, _agent) in _skills().items():
            with self.subTest(intent=intent):
                skill.policy_engine = PolicyEngine()
                result = skill.execute({**params, "effect": "send", "confirmed": True})
                self.assertTrue(result.success, result)
        self.assertTrue(any(m.called for m in self.mocks))

    def test_blocked_by_policy_even_when_confirmed(self):
        for intent, (skill, params, agent) in _skills().items():
            with self.subTest(intent=intent):
                skill.policy_engine = PolicyEngine(blocked_intents={"UI_DELETE"})
                result = skill.execute({**params, "effect": "delete", "confirmed": True})
                self.assertEqual(result.error, "POLICY_BLOCKED")
                self._no_input(agent)

    def test_without_a_policy_engine_a_sensitive_action_is_blocked(self):
        for intent, (skill, params, agent) in _skills().items():
            with self.subTest(intent=intent):
                result = skill.execute({**params, "effect": "purchase", "confirmed": True})
                self.assertEqual(result.error, "POLICY_BLOCKED")
                self._no_input(agent)

    def test_an_unknown_effect_is_rejected_never_guessed(self):
        result = gate_sensitive_ui_action({"effect": "pay"}, PolicyEngine(), "Paga")
        self.assertEqual(result.error, "INVALID_PARAMETERS")

    def test_ordinary_actions_are_unchanged(self):
        self.assertIsNone(gate_sensitive_ui_action({"text": "Aggiungi"}, None, "Aggiungi"))

    def test_declared_effects_have_their_own_risk_levels(self):
        self.assertEqual(risk_of("UI_DELETE"), RiskLevel.DESTRUCTIVE)
        self.assertEqual(risk_of("UI_PURCHASE"), RiskLevel.DESTRUCTIVE)
        self.assertEqual(risk_of("UI_SEND"), RiskLevel.EXTERNAL_ACTION)
        self.assertEqual(risk_of("UI_UPLOAD"), RiskLevel.EXTERNAL_ACTION)
        self.assertEqual(risk_of("UI_SUBMIT"), RiskLevel.EXTERNAL_ACTION)


class JakeCoreRoundTripTests(_JakeCoreTestCase):
    def test_click_send_asks_then_runs_only_after_yes(self):
        agent = _Agent()
        skill = ClickTextSkill(computer_agent=agent)
        core = self._core(skill_registry=FakeRegistry({"CLICK_TEXT": skill}))
        skill.policy_engine = core.policy_engine

        response = core._execute_command("clicca invia", Command("CLICK_TEXT", {"text": "Invia", "effect": "send"}))
        self.assertIn("Confermi", response)
        self.assertEqual(agent.clicks, [])
        self.assertTrue(core.conversation_state.has_pending_action())

        core.answer("sì")
        self.assertEqual(agent.clicks, [(30, 20)])

    def test_a_no_leaves_the_app_untouched(self):
        agent = _Agent()
        skill = ClickTextSkill(computer_agent=agent)
        core = self._core(skill_registry=FakeRegistry({"CLICK_TEXT": skill}))
        skill.policy_engine = core.policy_engine
        core._execute_command("clicca elimina", Command("CLICK_TEXT", {"text": "Elimina", "effect": "delete"}))
        core.answer("no")
        self.assertEqual(agent.clicks, [])
        self.assertFalse(core.conversation_state.has_pending_action())


if __name__ == "__main__":
    unittest.main()
