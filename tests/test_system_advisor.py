"""Test per gli avvisi proattivi (vedi core/system_advisor.py): un avviso scatta una sola
volta per 'episodio' sotto soglia, non ad ogni controllo, e si riarma quando si torna sopra
soglia."""
import unittest
from types import SimpleNamespace
from unittest import mock

from core.system_advisor import SystemAdvisor


class SystemAdvisorBatteryTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.advisor = SystemAdvisor(on_advisory=self.messages.append, enabled=False)

    def _battery(self, percent, plugged):
        return SimpleNamespace(percent=percent, power_plugged=plugged)

    def test_warns_once_when_low_and_unplugged(self):
        with mock.patch("psutil.sensors_battery", return_value=self._battery(10, False)):
            self.advisor._check_battery()
            self.advisor._check_battery()
        self.assertEqual(len(self.messages), 1)
        self.assertIn("10%", self.messages[0])

    def test_does_not_warn_when_plugged_in(self):
        with mock.patch("psutil.sensors_battery", return_value=self._battery(10, True)):
            self.advisor._check_battery()
        self.assertEqual(self.messages, [])

    def test_does_not_warn_above_threshold(self):
        with mock.patch("psutil.sensors_battery", return_value=self._battery(80, False)):
            self.advisor._check_battery()
        self.assertEqual(self.messages, [])

    def test_rearms_after_recovering_above_threshold(self):
        with mock.patch("psutil.sensors_battery", return_value=self._battery(10, False)):
            self.advisor._check_battery()
        with mock.patch("psutil.sensors_battery", return_value=self._battery(50, False)):
            self.advisor._check_battery()
        with mock.patch("psutil.sensors_battery", return_value=self._battery(10, False)):
            self.advisor._check_battery()
        self.assertEqual(len(self.messages), 2)

    def test_no_battery_sensor_is_silent(self):
        with mock.patch("psutil.sensors_battery", return_value=None):
            self.advisor._check_battery()
        self.assertEqual(self.messages, [])


class SystemAdvisorDiskTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.advisor = SystemAdvisor(on_advisory=self.messages.append, enabled=False)

    def _usage(self, free_gb):
        return SimpleNamespace(free=free_gb * (1024 ** 3), total=100 * (1024 ** 3), used=0, percent=0)

    def test_warns_once_when_disk_almost_full(self):
        with mock.patch("psutil.disk_usage", return_value=self._usage(1.0)):
            self.advisor._check_disk()
            self.advisor._check_disk()
        self.assertEqual(len(self.messages), 1)

    def test_does_not_warn_with_plenty_of_space(self):
        with mock.patch("psutil.disk_usage", return_value=self._usage(50.0)):
            self.advisor._check_disk()
        self.assertEqual(self.messages, [])


class FakeTodoManager:
    def __init__(self, stale: list = None):
        self._stale = stale or []

    def list_stale_pending(self, days=3):
        return list(self._stale)


class SystemAdvisorStaleTodoTests(unittest.TestCase):
    def setUp(self):
        self.messages = []

    def _advisor(self, stale):
        return SystemAdvisor(on_advisory=self.messages.append, enabled=False, todo_manager=FakeTodoManager(stale))

    def test_no_todo_manager_is_silent(self):
        advisor = SystemAdvisor(on_advisory=self.messages.append, enabled=False)
        advisor._check_stale_todos()
        self.assertEqual(self.messages, [])

    def test_single_stale_todo_is_announced_once(self):
        advisor = self._advisor([{"id": 1, "text": "pagare la bolletta", "created_at": "2020-01-01"}])
        advisor._check_stale_todos()
        advisor._check_stale_todos()
        self.assertEqual(len(self.messages), 1)
        self.assertIn("pagare la bolletta", self.messages[0])

    def test_no_stale_todos_is_silent(self):
        advisor = self._advisor([])
        advisor._check_stale_todos()
        self.assertEqual(self.messages, [])

    def test_multiple_stale_todos_are_summarized_in_one_message(self):
        advisor = self._advisor([
            {"id": 1, "text": "vecchia", "created_at": "2020-01-01"},
            {"id": 2, "text": "meno vecchia", "created_at": "2020-06-01"},
        ])
        advisor._check_stale_todos()
        self.assertEqual(len(self.messages), 1)
        self.assertIn("2 attivita", self.messages[0])
        self.assertIn("vecchia", self.messages[0])

    def test_new_stale_todo_is_announced_even_after_an_earlier_one_was_already_warned(self):
        advisor = self._advisor([{"id": 1, "text": "prima", "created_at": "2020-01-01"}])
        advisor._check_stale_todos()
        advisor.todo_manager = FakeTodoManager([
            {"id": 1, "text": "prima", "created_at": "2020-01-01"},
            {"id": 2, "text": "seconda", "created_at": "2020-02-01"},
        ])
        advisor._check_stale_todos()
        self.assertEqual(len(self.messages), 2)
        self.assertIn("seconda", self.messages[1])

    def test_todo_no_longer_stale_can_be_warned_about_again_later(self):
        """Se una todo esce dalla lista (completata) e un'altra, diversa, diventa stale in
        seguito, non deve restare "silenziata" per sempre solo perche' condivide un vecchio id
        gia' visto in passato con un contesto diverso."""
        advisor = self._advisor([{"id": 1, "text": "prima", "created_at": "2020-01-01"}])
        advisor._check_stale_todos()
        advisor.todo_manager = FakeTodoManager([])  # completata
        advisor._check_stale_todos()
        advisor.todo_manager = FakeTodoManager([{"id": 1, "text": "prima", "created_at": "2020-01-01"}])
        advisor._check_stale_todos()
        self.assertEqual(len(self.messages), 2)


if __name__ == "__main__":
    unittest.main()
