"""Test unitari per skills/system_info.py: nessuna suite esisteva finora. psutil e socket sono
mockati dove serve; urllib.request.urlopen e' sempre mockato per GetPublicIpSkill, is_online
forzato a True.

F1: buco reale corretto in questa sessione (stesso pattern sistemico gia' corretto per altri
consumatori diretti di API esterne) - GetPublicIpSkill non validava la forma della risposta prima
di chiamare payload.get("ip"): un corpo JSON valido ma non un dizionario faceva sollevare
AttributeError, mai catturato, invece di degradare a NETWORK_UNAVAILABLE."""
import json
import unittest
from unittest import mock
from urllib import error

from skills.system_info import (
    GetBatteryStatusSkill,
    GetDiskUsageSkill,
    GetLocalIpSkill,
    GetPublicIpSkill,
    GetSystemInfoSkill,
)


def _fake_response(body_bytes: bytes) -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = body_bytes
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _fake_json_response(payload) -> mock.MagicMock:
    return _fake_response(json.dumps(payload).encode("utf-8"))


class GetPublicIpTests(unittest.TestCase):
    def test_offline_reports_network_unavailable(self):
        with mock.patch("skills.system_info.is_online", return_value=False):
            result = GetPublicIpSkill().execute({})
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")

    def test_a_successful_response_returns_the_ip(self):
        with mock.patch("skills.system_info.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", return_value=_fake_json_response({"ip": "1.2.3.4"})):
                result = GetPublicIpSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["ip"], "1.2.3.4")

    def test_connection_failure_reports_network_unavailable(self):
        with mock.patch("skills.system_info.is_online", return_value=True):
            with mock.patch("urllib.request.urlopen", side_effect=error.URLError("rifiutata")):
                result = GetPublicIpSkill().execute({})
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")

    def test_malformed_payload_does_not_crash(self):
        """Il buco reale trovato e corretto in questa sessione."""
        for body in (b"null", b"[]", b"42"):
            with mock.patch("skills.system_info.is_online", return_value=True):
                with mock.patch("urllib.request.urlopen", return_value=_fake_response(body)):
                    result = GetPublicIpSkill().execute({})
            self.assertEqual(result.error, "NETWORK_UNAVAILABLE", msg=body)


class GetLocalIpTests(unittest.TestCase):
    def test_a_socket_failure_reports_network_unavailable(self):
        with mock.patch("socket.socket", side_effect=OSError("no route")):
            result = GetLocalIpSkill().execute({})
        self.assertEqual(result.error, "NETWORK_UNAVAILABLE")


class GetSystemInfoTests(unittest.TestCase):
    def test_returns_os_hostname_and_architecture(self):
        result = GetSystemInfoSkill().execute({})
        self.assertTrue(result.success)
        self.assertIn("os", result.data)
        self.assertIn("hostname", result.data)
        self.assertIn("architecture", result.data)


class GetBatteryStatusTests(unittest.TestCase):
    def test_no_battery_reports_not_found(self):
        with mock.patch("psutil.sensors_battery", return_value=None, create=True):
            result = GetBatteryStatusSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_a_battery_present_returns_percent_and_plugged(self):
        battery = mock.MagicMock(percent=87.6, power_plugged=True)
        with mock.patch("psutil.sensors_battery", return_value=battery, create=True):
            result = GetBatteryStatusSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["percent"], 88)
        self.assertTrue(result.data["plugged"])


class GetDiskUsageTests(unittest.TestCase):
    def test_a_nonexistent_drive_reports_path_not_found(self):
        with mock.patch("psutil.disk_usage", side_effect=OSError("no disk"), create=True):
            result = GetDiskUsageSkill().execute({"drive": "Z:"})
        self.assertEqual(result.error, "PATH_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
