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


if __name__ == "__main__":
    unittest.main()
