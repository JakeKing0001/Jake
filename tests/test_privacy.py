"""Test unitari per il Privacy Engine (v5.6): modalita' privata (skills/session_control.py,
PrivateModeSkill) e politica di retention su richiesta esplicita (skills/privacy.py,
PurgeOldHistorySkill), incluso l'effetto reale della modalita' privata dentro JakeCore.answer()."""
import unittest
from unittest import mock

from core.conversation_state import ConversationStateManager
from core.event_bus import EventBus
from core.jake_core import JakeCore
from skills.privacy import PurgeOldHistorySkill
from skills.session_control import PrivateModeSkill


class FakeCore:
    def __init__(self):
        self.private_mode = False


class PrivateModeSkillTests(unittest.TestCase):
    def test_enables_private_mode(self):
        core = FakeCore()
        result = PrivateModeSkill(core).execute({"enabled": True})
        self.assertTrue(result.success)
        self.assertTrue(core.private_mode)

    def test_disables_private_mode(self):
        core = FakeCore()
        core.private_mode = True
        result = PrivateModeSkill(core).execute({"enabled": False})
        self.assertTrue(result.success)
        self.assertFalse(core.private_mode)

    def test_missing_parameter(self):
        result = PrivateModeSkill(FakeCore()).execute({})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "MISSING_PARAMETERS")


class FakeMemoryManagerForPurge:
    def __init__(self, removed=0):
        self.removed = removed
        self.calls = []

    def purge_history_older_than(self, days):
        self.calls.append(days)
        return self.removed


class PurgeOldHistorySkillTests(unittest.TestCase):
    def test_asks_for_confirmation_first(self):
        manager = FakeMemoryManagerForPurge()
        result = PurgeOldHistorySkill(manager).execute({"days": 30})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(manager.calls, [], "non deve cancellare nulla prima della conferma")

    def test_confirmed_call_purges(self):
        manager = FakeMemoryManagerForPurge(removed=12)
        result = PurgeOldHistorySkill(manager).execute({"days": 30, "confirmed": True})
        self.assertTrue(result.success)
        self.assertEqual(manager.calls, [30])
        self.assertEqual(result.data["removed"], 12)

    def test_missing_days_parameter(self):
        result = PurgeOldHistorySkill(FakeMemoryManagerForPurge()).execute({})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_zero_or_negative_days_is_rejected(self):
        result = PurgeOldHistorySkill(FakeMemoryManagerForPurge()).execute({"days": 0})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "MISSING_PARAMETERS")


class FakeNormalizer:
    def normalize(self, text):
        return text


class FakeMemoryManagerForAnswer:
    def __init__(self):
        self.logged = []
        self.summarize_calls = 0

    def log_turn(self, role, text):
        self.logged.append((role, text))

    def summarize_old_history(self, summarizer):
        self.summarize_calls += 1


class FakeLogger:
    def __init__(self):
        self.messages = []

    def info(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def exception(self, *args, **kwargs):
        pass


def _bare_core_for_answer(private_mode: bool) -> JakeCore:
    core = JakeCore.__new__(JakeCore)
    core.normalizer = FakeNormalizer()
    core.conversation_state = ConversationStateManager()
    core.memory_manager = FakeMemoryManagerForAnswer()
    core.logger = FakeLogger()
    core.context_summarizer = object()
    core.private_mode = private_mode
    core.last_response = None
    core.event_bus = EventBus()  # v4.9.1: answer() pubblica USER_MESSAGE/JAKE_MESSAGE qui
    return core


class PrivateModeSuppressesPersistenceTests(unittest.TestCase):
    def test_private_mode_writes_nothing_to_memory_or_log(self):
        core = _bare_core_for_answer(private_mode=True)
        subscriber = core.event_bus.subscribe()
        with mock.patch.object(JakeCore, "_process", return_value="risposta"):
            response = core.answer("ciao")

        self.assertEqual(response, "risposta")
        self.assertEqual(core.memory_manager.logged, [])
        self.assertEqual(core.memory_manager.summarize_calls, 0)
        self.assertTrue(any("privata" in m.lower() for m in core.logger.messages))
        self.assertTrue(subscriber.empty(), "uno scambio privato non deve essere trasmesso sul bus eventi (v4.9.1)")

    def test_private_mode_still_updates_in_memory_short_term_history(self):
        """La cronologia in RAM serve alla sessione corrente (pronomi, agente): non e' una
        persistenza su disco, resta attiva anche in modalita' privata."""
        core = _bare_core_for_answer(private_mode=True)
        with mock.patch.object(JakeCore, "_process", return_value="risposta"):
            core.answer("ciao")

        self.assertEqual(
            core.conversation_state.get_short_term_history(),
            [{"role": "user", "text": "ciao"}, {"role": "jake", "text": "risposta"}],
        )

    def test_normal_mode_persists_as_before(self):
        core = _bare_core_for_answer(private_mode=False)
        subscriber = core.event_bus.subscribe()
        with mock.patch.object(JakeCore, "_process", return_value="risposta"):
            core.answer("ciao")

        self.assertEqual(core.memory_manager.logged, [("user", "ciao"), ("jake", "risposta")])
        self.assertEqual(core.memory_manager.summarize_calls, 1)
        from core.hud_protocol import EventType
        first, second = subscriber.get_nowait(), subscriber.get_nowait()
        self.assertEqual((first.type, first.payload), (EventType.USER_MESSAGE, {"text": "ciao"}))
        self.assertEqual((second.type, second.payload), (EventType.JAKE_MESSAGE, {"text": "risposta"}))


if __name__ == "__main__":
    unittest.main()
