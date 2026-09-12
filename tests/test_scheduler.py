"""Test unitari per core/scheduler.py::ReminderScheduler. Nessuna suite dedicata esisteva
finora (solo copertura indiretta tramite core/reminder_manager.py)."""
import time
import unittest
from unittest import mock

from core.scheduler import ReminderScheduler


class StopTimeoutTests(unittest.TestCase):
    """F1.8.5 ("aggiungere deadlock timeout e diagnosi"): buco reale, riprodotto per davvero -
    se _run() era bloccato (qui in due_reminders()) oltre i 2s di timeout di stop(), join()
    tornava comunque, silenziosamente, senza dire che il thread era ANCORA vivo. Chi chiama
    stop() (JakeCore.shutdown(), gia' reso rumoroso sui propri fallimenti in questa sessione,
    F1.8.4) non aveva modo di scoprire che lo scheduler non si era davvero fermato."""

    def test_stop_logs_a_warning_when_the_thread_does_not_stop_in_time(self):
        reminder_manager = mock.Mock()
        reminder_manager.due_reminders.side_effect = lambda: time.sleep(0.5)
        scheduler = ReminderScheduler(reminder_manager, interval_seconds=100, stop_timeout_seconds=0.05)
        scheduler._logger = mock.Mock()
        scheduler.start()
        self.addCleanup(lambda: scheduler._thread.join(timeout=5))
        time.sleep(0.05)  # lascia partire _run() e bloccarsi dentro due_reminders()

        scheduler.stop()

        self.assertTrue(scheduler._thread.is_alive(), "il thread deve essere ancora bloccato a questo punto")
        scheduler._logger.warning.assert_called_once()

    def test_stop_does_not_warn_when_the_thread_stops_normally(self):
        reminder_manager = mock.Mock(due_reminders=mock.Mock(return_value=[]))
        scheduler = ReminderScheduler(reminder_manager, interval_seconds=100)
        scheduler._logger = mock.Mock()
        scheduler.start()
        time.sleep(0.1)

        scheduler.stop()

        self.assertFalse(scheduler._thread.is_alive())
        scheduler._logger.warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
