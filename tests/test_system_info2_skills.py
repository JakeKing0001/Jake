"""Test unitari per skills/system_info2.py: nessuna suite esisteva finora, nessun bug trovato.
subprocess.run/psutil/win32api sono sempre mockati."""
import unittest
from unittest import mock

from skills.system_info2 import (
    GetDnsServersSkill,
    GetEnvironmentVariableSkill,
    GetGpuInfoSkill,
    GetMacAddressSkill,
    GetScreenResolutionSkill,
    ListDrivesSkill,
)


class GetScreenResolutionTests(unittest.TestCase):
    def test_returns_width_and_height(self):
        fake_win32api = mock.MagicMock()
        fake_win32api.GetSystemMetrics.side_effect = [1920, 1080]
        with mock.patch.dict("sys.modules", {"win32api": fake_win32api}):
            result = GetScreenResolutionSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data, {"width": 1920, "height": 1080})


class GetGpuInfoTests(unittest.TestCase):
    def test_a_successful_query_lists_gpus(self):
        completed = mock.MagicMock(stdout="NVIDIA GeForce RTX 4070\n")
        with mock.patch("subprocess.run", return_value=completed):
            result = GetGpuInfoSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["gpus"], ["NVIDIA GeForce RTX 4070"])

    def test_a_failure_reports_operation_failed(self):
        with mock.patch("subprocess.run", side_effect=OSError("no powershell")):
            result = GetGpuInfoSkill().execute({})
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_empty_output_reports_not_found(self):
        completed = mock.MagicMock(stdout="\n\n")
        with mock.patch("subprocess.run", return_value=completed):
            result = GetGpuInfoSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")


class GetDnsServersTests(unittest.TestCase):
    def test_extracts_dns_servers_from_ipconfig_output(self):
        completed = mock.MagicMock(stdout="   DNS Servers . . . . . . . . . : 8.8.8.8\n                                    8.8.4.4\n")
        with mock.patch("subprocess.run", return_value=completed):
            result = GetDnsServersSkill().execute({})
        self.assertTrue(result.success)
        self.assertIn("8.8.8.8", result.data["servers"])

    def test_no_dns_servers_found_reports_not_found(self):
        completed = mock.MagicMock(stdout="Nessuna informazione utile\n")
        with mock.patch("subprocess.run", return_value=completed):
            result = GetDnsServersSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_a_failure_reports_operation_failed(self):
        with mock.patch("subprocess.run", side_effect=OSError("no ipconfig")):
            result = GetDnsServersSkill().execute({})
        self.assertEqual(result.error, "OPERATION_FAILED")


class GetEnvironmentVariableTests(unittest.TestCase):
    def test_missing_name_fails(self):
        result = GetEnvironmentVariableSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_an_unset_variable_reports_not_found(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            result = GetEnvironmentVariableSkill().execute({"name": "VARIABILE_INESISTENTE_JAKE"})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_a_set_variable_returns_its_value(self):
        with mock.patch.dict("os.environ", {"JAKE_TEST_VAR": "valore"}):
            result = GetEnvironmentVariableSkill().execute({"name": "JAKE_TEST_VAR"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["value"], "valore")


class GetMacAddressTests(unittest.TestCase):
    def test_returns_a_colon_separated_mac_address(self):
        with mock.patch("uuid.getnode", return_value=0x001122334455):
            result = GetMacAddressSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["mac_address"], "00:11:22:33:44:55")


class ListDrivesTests(unittest.TestCase):
    def test_lists_accessible_drives_and_skips_unreadable_ones(self):
        partitions = [mock.MagicMock(device="C:\\", mountpoint="C:\\"), mock.MagicMock(device="D:\\", mountpoint="D:\\")]
        usage = mock.MagicMock(free=100 * 1024**3, total=500 * 1024**3)
        with mock.patch("psutil.disk_partitions", return_value=partitions, create=True):
            with mock.patch("psutil.disk_usage", side_effect=[usage, OSError("no disk")], create=True):
                result = ListDrivesSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(len(result.data["drives"]), 1)
        self.assertEqual(result.data["drives"][0]["drive"], "C:\\")

    def test_no_accessible_drives_reports_not_found(self):
        partitions = [mock.MagicMock(device="C:\\", mountpoint="C:\\")]
        with mock.patch("psutil.disk_partitions", return_value=partitions, create=True):
            with mock.patch("psutil.disk_usage", side_effect=OSError("no disk"), create=True):
                result = ListDrivesSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
