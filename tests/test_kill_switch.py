"""Test unitari per il kill switch globale (F1, Trustworthy Agent Core 3.0 - vedi
core/kill_switch.py, skills/kill_switch.py). L'effetto sull'agente a passi e su PlanExecutor e'
coperto anche da tests/test_agent.py/tests/test_plan_executor.py con scenari piu' completi
(rollback dei passi gia' fatti); qui si copre il meccanismo in isolamento e le due skill."""
import unittest
from unittest import mock

from core.kill_switch import KillSwitch
from skills.kill_switch import KillSwitchSkill, ResetKillSwitchSkill


class KillSwitchTests(unittest.TestCase):
    def test_starts_inactive(self):
        self.assertFalse(KillSwitch().is_active())

    def test_activate_sets_it_active(self):
        switch = KillSwitch()
        switch.activate()
        self.assertTrue(switch.is_active())

    def test_reset_clears_it(self):
        switch = KillSwitch()
        switch.activate()
        switch.reset()
        self.assertFalse(switch.is_active())


class FakeCore:
    def __init__(self):
        self.activate_calls = 0
        self.reset_calls = 0

    def activate_kill_switch(self):
        self.activate_calls += 1

    def reset_kill_switch(self):
        self.reset_calls += 1


class KillSwitchSkillTests(unittest.TestCase):
    def test_execute_calls_activate_kill_switch_on_core(self):
        core = FakeCore()
        result = KillSwitchSkill(core).execute()

        self.assertTrue(result.success)
        self.assertEqual(core.activate_calls, 1)
        self.assertIn("fermato", result.data["text"].lower())


class ResetKillSwitchSkillTests(unittest.TestCase):
    def test_execute_calls_reset_kill_switch_on_core(self):
        core = FakeCore()
        result = ResetKillSwitchSkill(core).execute()

        self.assertTrue(result.success)
        self.assertEqual(core.reset_calls, 1)


class JakeCoreActivateResetTests(unittest.TestCase):
    """JakeCore.activate_kill_switch()/reset_kill_switch(): il flag e gli scheduler insieme,
    non solo il flag - vedi core/jake_core.py."""

    def _bare_core(self):
        from core.jake_core import JakeCore

        core = JakeCore.__new__(JakeCore)
        core.kill_switch = KillSwitch()
        core.scheduler = mock.Mock()
        core.trigger_scheduler = mock.Mock()
        core.logger = mock.Mock()
        return core

    def test_activate_sets_the_flag_and_stops_both_schedulers(self):
        core = self._bare_core()

        core.activate_kill_switch()

        self.assertTrue(core.kill_switch.is_active())
        core.scheduler.stop.assert_called_once()
        core.trigger_scheduler.stop.assert_called_once()

    def test_reset_clears_the_flag_and_starts_both_schedulers(self):
        core = self._bare_core()
        core.kill_switch.activate()

        core.reset_kill_switch()

        self.assertFalse(core.kill_switch.is_active())
        core.scheduler.start.assert_called_once()
        core.trigger_scheduler.start.assert_called_once()

    def test_activate_does_not_crash_if_a_scheduler_stop_raises(self):
        core = self._bare_core()
        core.scheduler.stop.side_effect = RuntimeError("boom")

        core.activate_kill_switch()  # non deve sollevare

        self.assertTrue(core.kill_switch.is_active())
        core.trigger_scheduler.stop.assert_called_once()  # l'altro scheduler va comunque fermato


if __name__ == "__main__":
    unittest.main()
