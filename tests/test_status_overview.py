"""F6.7/F4.5.6: "a cosa stai lavorando?" legge davvero il TaskMonitorRegistry (finora mai letto), le sorveglianze, la
conferma in sospeso e le notifiche trattenute. Componenti reali in memoria."""
import unittest
from types import SimpleNamespace

from core.conversation_state import ConversationStateManager
from core.notification_center import NotificationCenter, NotificationMode
from core.response_formatter import format_skill_result
from core.task_monitor import TaskMonitorRegistry
from skills.status_overview import StatusOverviewSkill
from skills.watch_process import WatchProcessSkill


class StatusOverviewTests(unittest.TestCase):
    def test_everything_in_progress_in_one_answer(self):
        now = [1000.0]
        monitor = TaskMonitorRegistry(clock=lambda: now[0])
        monitor.start("t1", "riassumi i PDF della tesi")
        monitor.start("t2", "compito finito")
        monitor.complete("t2")
        center = NotificationCenter(NotificationMode.DO_NOT_DISTURB)
        center.gate("advisory", "Batteria al 20%")
        state = ConversationStateManager()
        state.set_pending_action({"intent": "DELETE_PATH", "parameters": {}, "reason": "confirmation_required"})
        core = SimpleNamespace(task_monitor=monitor, notification_center=center, conversation_state=state)
        watcher = WatchProcessSkill(core, process_iter=lambda: [])
        watcher.watching[42] = "npm.exe"

        now[0] += 300
        reply = format_skill_result("STATUS_OVERVIEW", StatusOverviewSkill(core, watcher, clock=lambda: now[0]).execute())
        self.assertEqual(reply, 'Sto lavorando a "riassumi i PDF della tesi" (in corso, da 5 min); aspetto che finiscano '
                                "npm.exe; aspetto una tua conferma; 1 notifica è in attesa.")

    def test_nothing_in_progress(self):
        core = SimpleNamespace(task_monitor=TaskMonitorRegistry(), notification_center=NotificationCenter(),
                               conversation_state=ConversationStateManager())
        self.assertEqual(format_skill_result("STATUS_OVERVIEW", StatusOverviewSkill(core).execute()),
                         "Niente in corso: sono libero.")


if __name__ == "__main__":
    unittest.main()
