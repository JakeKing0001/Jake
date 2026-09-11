"""Test unitari per skills/wifi_control.py: nessuna suite esisteva finora, nessun bug trovato.
subprocess.run e' sempre mockato (mai una vera chiamata a netsh)."""
import unittest
from unittest import mock

from skills.wifi_control import ListWifiNetworksSkill

SAMPLE_OUTPUT = """Interfaccia riportata: Wi-Fi

Numero di reti rilevate: 2

SSID 1 : CasaMia
    Tipo di rete           : Infrastruttura
SSID 2 : VicinoWifi
    Tipo di rete           : Infrastruttura
"""


class ListWifiNetworksTests(unittest.TestCase):
    def test_a_successful_scan_lists_ssids(self):
        completed = mock.MagicMock(stdout=SAMPLE_OUTPUT)
        with mock.patch("subprocess.run", return_value=completed):
            result = ListWifiNetworksSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["networks"], ["CasaMia", "VicinoWifi"])

    def test_no_networks_found_reports_not_found(self):
        completed = mock.MagicMock(stdout="Numero di reti rilevate: 0\n")
        with mock.patch("subprocess.run", return_value=completed):
            result = ListWifiNetworksSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_a_subprocess_failure_reports_operation_failed(self):
        with mock.patch("subprocess.run", side_effect=OSError("no netsh")):
            result = ListWifiNetworksSkill().execute({})
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_hidden_ssids_are_skipped(self):
        completed = mock.MagicMock(stdout="SSID 1 : \nSSID 2 : RedeVisibile\n")
        with mock.patch("subprocess.run", return_value=completed):
            result = ListWifiNetworksSkill().execute({})
        self.assertEqual(result.data["networks"], ["RedeVisibile"])


if __name__ == "__main__":
    unittest.main()
