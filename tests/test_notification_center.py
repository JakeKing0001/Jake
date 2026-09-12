"""Test unitari per il sistema di modalita' di notifica (v4.3, core/notification_center.py)."""
import sys
import threading
import unittest

from core.notification_center import NotificationCenter, NotificationMode


class GateTests(unittest.TestCase):
    def test_normal_mode_lets_everything_through(self):
        center = NotificationCenter()
        self.assertEqual(center.gate("reminder", "promemoria"), "promemoria")
        self.assertEqual(center.gate("advisory", "avviso"), "avviso")
        self.assertEqual(center.gate("trigger", "automazione"), "automazione")
        self.assertEqual(center.pending_count(), 0)

    def test_do_not_disturb_suppresses_advisory_and_trigger_but_not_reminder(self):
        center = NotificationCenter(mode=NotificationMode.DO_NOT_DISTURB)
        self.assertEqual(center.gate("reminder", "prendi la medicina"), "prendi la medicina")
        self.assertIsNone(center.gate("advisory", "batteria scarica"))
        self.assertIsNone(center.gate("trigger", "backup fatto"))
        self.assertEqual(center.pending_count(), 2)

    def test_meeting_suppresses_everything_including_reminders(self):
        center = NotificationCenter(mode=NotificationMode.MEETING)
        self.assertIsNone(center.gate("reminder", "chiamata alle 15"))
        self.assertIsNone(center.gate("advisory", "batteria scarica"))
        self.assertIsNone(center.gate("trigger", "backup fatto"))
        self.assertEqual(center.pending_count(), 3)

    def test_gaming_lets_reminders_and_triggers_through_but_not_advisories(self):
        center = NotificationCenter(mode=NotificationMode.GAMING)
        self.assertEqual(center.gate("reminder", "pausa"), "pausa")
        self.assertEqual(center.gate("trigger", "download completato"), "download completato")
        self.assertIsNone(center.gate("advisory", "disco quasi pieno"))

    def test_empty_message_is_never_queued(self):
        center = NotificationCenter(mode=NotificationMode.MEETING)
        self.assertIsNone(center.gate("advisory", ""))
        self.assertEqual(center.pending_count(), 0)


class SetModeAndReleaseTests(unittest.TestCase):
    def test_returning_to_normal_releases_all_queued_messages_in_order(self):
        center = NotificationCenter(mode=NotificationMode.DO_NOT_DISTURB)
        center.gate("advisory", "primo avviso")
        center.gate("trigger", "prima automazione")

        released = center.set_mode(NotificationMode.NORMAL)

        self.assertEqual(released, ["primo avviso", "prima automazione"])
        self.assertEqual(center.pending_count(), 0)

    def test_switching_between_two_restrictive_modes_keeps_still_unreleased_items_queued(self):
        center = NotificationCenter(mode=NotificationMode.MEETING)
        center.gate("reminder", "promemoria durante la riunione")

        released = center.set_mode(NotificationMode.DO_NOT_DISTURB)

        # DO_NOT_DISTURB ammette i promemoria: quello in coda viene rilasciato.
        self.assertEqual(released, ["promemoria durante la riunione"])
        self.assertEqual(center.pending_count(), 0)

    def test_partial_release_leaves_the_rest_queued(self):
        center = NotificationCenter(mode=NotificationMode.MEETING)
        center.gate("reminder", "promemoria")
        center.gate("advisory", "avviso")

        released = center.set_mode(NotificationMode.DO_NOT_DISTURB)

        self.assertEqual(released, ["promemoria"])
        self.assertEqual(center.pending_count(), 1)  # l'avviso resta in coda, DND non lo ammette


class ConcurrentAccessTests(unittest.TestCase):
    """F1.8.2 (stesso principio gia' applicato a ActionLedger/ReminderManager/TodoManager/
    MemoryManager in questa sessione): buco reale, riprodotto per davvero prima del fix -
    NotificationCenter e' condivisa PER RIFERIMENTO tra JakeCore.notify() (chiamato dai thread
    separati di TriggerScheduler/ReminderScheduler/SystemAdvisor) e SetNotificationModeSkill
    (voce/companion server, altri thread). set_mode() leggeva e riscriveva self._queued in DUE
    passaggi separati senza alcun lock: un gate() concorrente che arrivava esattamente tra i due
    passaggi spariva per sempre, ne' rilasciato ne' rimasto in coda. Riprodotto con
    sys.setswitchinterval() abbassato per forzare la sovrapposizione reale: su 30 prove con 500
    gate() concorrenti a un set_mode(), oltre il 98% delle notifiche spariva senza lasciare
    traccia PRIMA di questo fix."""

    THREAD_MESSAGES = 500

    def setUp(self):
        self._original_switch_interval = sys.getswitchinterval()
        # Costringe il GIL a cedere molto piu' spesso, per massimizzare la sovrapposizione reale
        # tra i thread invece di affidarsi al caso del timing (stesso principio gia' usato per
        # riprodurre il buco durante lo sviluppo di questo fix).
        sys.setswitchinterval(0.00001)
        self.addCleanup(sys.setswitchinterval, self._original_switch_interval)

    def test_no_notification_is_lost_when_gate_races_with_a_concurrent_set_mode(self):
        for _ in range(20):
            self._assert_no_message_lost_in_one_race()

    def _assert_no_message_lost_in_one_race(self) -> None:
        center = NotificationCenter(mode=NotificationMode.STUDY)
        center.gate("advisory", "batteria scarica")  # in coda: STUDY non ammette advisory

        results: dict = {}
        delivered_directly: list = []
        delivered_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def _switch_mode():
            barrier.wait()
            results["released"] = center.set_mode(NotificationMode.NORMAL)

        def _gate_more():
            barrier.wait()
            for i in range(self.THREAD_MESSAGES):
                message = center.gate("advisory", f"avviso-{i}")
                if message is not None:
                    with delivered_lock:
                        delivered_directly.append(message)

        threads = [threading.Thread(target=_switch_mode), threading.Thread(target=_gate_more)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # Ogni messaggio deve finire in ESATTAMENTE uno dei tre posti: consegnato subito da
        # gate() (se il cambio di modalita' e' gia' avvenuto), rilasciato da set_mode(), o
        # ancora in coda - mai perso silenziosamente.
        total = len(delivered_directly) + len(results["released"]) + center.pending_count()
        self.assertEqual(total, self.THREAD_MESSAGES + 1)


if __name__ == "__main__":
    unittest.main()
