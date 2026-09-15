"""Test unitari per skills/system_maintenance.py: nessuna suite esisteva finora. subprocess.run
e' sempre mockato; winreg e' importato a livello di MODULO in skills/system_maintenance.py
(`import winreg`, non dentro le funzioni), quindi va mockato come `skills.system_maintenance.
winreg` - patchare sys.modules["winreg"] non basta, il nome e' gia' legato nel modulo. Un test
che chiamasse per davvero taskkill/powercfg/il registro modificherebbe lo stato reale della
macchina che esegue la suite."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skills.system_maintenance import (
    ClearTempFilesSkill, FlushDnsSkill, GetWifiStatusSkill, ListInstalledAppsSkill,
    ListStartupAppsSkill, RestartExplorerSkill, SetPowerPlanSkill, ToggleDarkModeSkill,
)


class ClearTempFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_clear_temp_test_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_without_confirmation_asks_for_it_and_deletes_nothing(self):
        (self.tmp_dir / "file.tmp").write_text("x", encoding="utf-8")
        with mock.patch("tempfile.gettempdir", return_value=str(self.tmp_dir)):
            result = ClearTempFilesSkill().execute({})
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertTrue((self.tmp_dir / "file.tmp").exists())

    def test_confirmed_clearing_actually_deletes_real_files_and_folders(self):
        (self.tmp_dir / "file.tmp").write_text("x", encoding="utf-8")
        subfolder = self.tmp_dir / "sottocartella"
        subfolder.mkdir()
        (subfolder / "altro.tmp").write_text("y", encoding="utf-8")

        with mock.patch("tempfile.gettempdir", return_value=str(self.tmp_dir)):
            result = ClearTempFilesSkill().execute({"confirmed": True})

        self.assertTrue(result.success)
        self.assertEqual(result.data["deleted"], 2)
        self.assertEqual(list(self.tmp_dir.iterdir()), [])

    def test_a_locked_file_is_skipped_not_a_crash(self):
        (self.tmp_dir / "buono.tmp").write_text("x", encoding="utf-8")
        (self.tmp_dir / "bloccato.tmp").write_text("y", encoding="utf-8")
        real_unlink = Path.unlink

        def flaky_unlink(self, *args, **kwargs):
            if self.name == "bloccato.tmp":
                raise OSError("in uso da un altro processo")
            return real_unlink(self, *args, **kwargs)

        with mock.patch("tempfile.gettempdir", return_value=str(self.tmp_dir)):
            with mock.patch.object(Path, "unlink", flaky_unlink):
                result = ClearTempFilesSkill().execute({"confirmed": True})

        self.assertTrue(result.success)
        self.assertEqual(result.data["deleted"], 1)
        self.assertTrue((self.tmp_dir / "bloccato.tmp").exists())


class _FakeProcess:
    def __init__(self, name):
        self.info = {"name": name}


class RestartExplorerTests(unittest.TestCase):
    """F1.3.2 (stesso pattern trovato una quarta volta in questa sessione, dopo processi/
    finestre/casa): buco reale - subprocess.Popen("explorer.exe") e' fire-and-forget, success=True
    veniva dichiarato subito dopo senza aspettare che Explorer fosse DAVVERO ripartito."""

    def setUp(self):
        # EXPLORER_RESTART_WAIT_SECONDS reale sarebbe 10s: qui ridotto perche' il test "mai
        # ripartito" deve aspettare per davvero il timeout completo prima di riportare il
        # fallimento, stesso principio gia' usato per CLOSE_WAIT_SECONDS in CloseWindowSkill.
        patcher = mock.patch.object(RestartExplorerSkill, "EXPLORER_RESTART_WAIT_SECONDS", 0.05)
        patcher.start()
        self.addCleanup(patcher.stop)
        interval_patcher = mock.patch.object(RestartExplorerSkill, "_POLL_INTERVAL_SECONDS", 0.01)
        interval_patcher.start()
        self.addCleanup(interval_patcher.stop)

    def test_success_restarts_explorer(self):
        with mock.patch("subprocess.run") as run, mock.patch("subprocess.Popen") as popen:
            with mock.patch("psutil.process_iter", return_value=[_FakeProcess("explorer.exe")]):
                result = RestartExplorerSkill().execute({})
        self.assertTrue(result.success)
        run.assert_called_once_with(["taskkill", "/f", "/im", "explorer.exe"], check=True, capture_output=True)
        popen.assert_called_once_with("explorer.exe")

    def test_a_failure_is_reported_not_raised(self):
        with mock.patch("subprocess.run", side_effect=OSError("boom")):
            result = RestartExplorerSkill().execute({})
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_explorer_never_reappearing_reports_operation_failed_not_success(self):
        """Il caso che prima di questa correzione veniva riportato come successo: taskkill e
        Popen non sollevano eccezioni, ma Explorer non e' davvero ripartito (es. crash immediato
        dopo l'avvio)."""
        with mock.patch("subprocess.run"), mock.patch("subprocess.Popen"):
            with mock.patch("psutil.process_iter", return_value=[_FakeProcess("notepad.exe")]):
                result = RestartExplorerSkill().execute({})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")


class FlushDnsTests(unittest.TestCase):
    def test_success(self):
        with mock.patch("subprocess.run") as run:
            result = FlushDnsSkill().execute({})
        self.assertTrue(result.success)
        run.assert_called_once()

    def test_a_failure_is_reported_not_raised(self):
        with mock.patch("subprocess.run", side_effect=OSError("boom")):
            result = FlushDnsSkill().execute({})
        self.assertEqual(result.error, "OPERATION_FAILED")


class ListStartupAppsTests(unittest.TestCase):
    def test_lists_registry_values(self):
        fake_winreg = mock.MagicMock()
        fake_winreg.OpenKey.return_value.__enter__.return_value = mock.sentinel.key
        fake_winreg.EnumValue.side_effect = [("AppUno", "", 1), ("AppDue", "", 1), OSError("fine")]
        with mock.patch("skills.system_maintenance.winreg", fake_winreg):
            result = ListStartupAppsSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["apps"], ["AppUno", "AppDue"])

    def test_no_entries_reports_not_found(self):
        fake_winreg = mock.MagicMock()
        fake_winreg.OpenKey.return_value.__enter__.return_value = mock.sentinel.key
        fake_winreg.EnumValue.side_effect = OSError("fine")
        with mock.patch("skills.system_maintenance.winreg", fake_winreg):
            result = ListStartupAppsSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_registry_key_missing_is_reported_not_raised(self):
        fake_winreg = mock.MagicMock()
        fake_winreg.OpenKey.side_effect = OSError("chiave non trovata")
        with mock.patch("skills.system_maintenance.winreg", fake_winreg):
            result = ListStartupAppsSkill().execute({})
        self.assertEqual(result.error, "OPERATION_FAILED")


class SetPowerPlanTests(unittest.TestCase):
    def test_unknown_plan_fails(self):
        result = SetPowerPlanSkill().execute({"plan": "un piano inventato"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_known_plan_calls_powercfg_with_the_right_guid(self):
        with mock.patch("subprocess.run") as run:
            result = SetPowerPlanSkill().execute({"plan": "balanced"})
        self.assertTrue(result.success)
        run.assert_called_once_with(
            ["powercfg", "/setactive", SetPowerPlanSkill.PLAN_GUIDS["balanced"]],
            check=True, capture_output=True, timeout=10,
        )

    def test_a_failure_is_reported_not_raised(self):
        with mock.patch("subprocess.run", side_effect=OSError("boom")):
            result = SetPowerPlanSkill().execute({"plan": "balanced"})
        self.assertEqual(result.error, "OPERATION_FAILED")


class ToggleDarkModeTests(unittest.TestCase):
    def test_missing_enabled_fails(self):
        result = ToggleDarkModeSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_non_boolean_enabled_fails(self):
        result = ToggleDarkModeSkill().execute({"enabled": "si"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_enabling_dark_mode_writes_zero(self):
        fake_winreg = mock.MagicMock()
        fake_winreg.OpenKey.return_value.__enter__.return_value = mock.sentinel.key
        with mock.patch("skills.system_maintenance.winreg", fake_winreg):
            result = ToggleDarkModeSkill().execute({"enabled": True})
        self.assertTrue(result.success)
        calls = fake_winreg.SetValueEx.call_args_list
        self.assertTrue(calls)
        self.assertTrue(all(call.args[4] == 0 for call in calls))

    def test_disabling_dark_mode_writes_one(self):
        fake_winreg = mock.MagicMock()
        fake_winreg.OpenKey.return_value.__enter__.return_value = mock.sentinel.key
        with mock.patch("skills.system_maintenance.winreg", fake_winreg):
            result = ToggleDarkModeSkill().execute({"enabled": False})
        self.assertTrue(result.success)
        calls = fake_winreg.SetValueEx.call_args_list
        self.assertTrue(calls)
        self.assertTrue(all(call.args[4] == 1 for call in calls))

    def test_a_registry_failure_is_reported_not_raised(self):
        fake_winreg = mock.MagicMock()
        fake_winreg.OpenKey.side_effect = OSError("negato")
        with mock.patch("skills.system_maintenance.winreg", fake_winreg):
            result = ToggleDarkModeSkill().execute({"enabled": True})
        self.assertEqual(result.error, "OPERATION_FAILED")


class ListInstalledAppsTests(unittest.TestCase):
    def _fake_winreg(self, display_names: list):
        """Un solo hive con una sola sottochiave per ogni nome in display_names; l'altro hive
        (per semplicita' lo stesso OpenKey) viene interrogato due volte (HKCU e HKLM) come fa
        davvero il codice, ma qui restituiamo lo stesso elenco per entrambi ed e' compito del
        codice deduplicare (result.data['apps'] usa gia' un set)."""
        fake_winreg = mock.MagicMock()
        fake_winreg.HKEY_CURRENT_USER = "HKCU"
        fake_winreg.HKEY_LOCAL_MACHINE = "HKLM"

        root_cm = mock.MagicMock()
        root_cm.__enter__.return_value = "root"
        root_cm.__exit__.return_value = False
        subkey_cm = mock.MagicMock()
        subkey_cm.__enter__.return_value = "subkey"
        subkey_cm.__exit__.return_value = False

        fake_winreg.OpenKey.side_effect = lambda hive, path: (
            root_cm if path == ListInstalledAppsSkill.UNINSTALL_KEY else subkey_cm
        )
        # EnumKey viene chiamato una volta per hive (HKCU poi HKLM): un nome a testata, poi OSError.
        enum_key_calls = []

        def enum_key(root_key, index):
            enum_key_calls.append(index)
            if index >= len(display_names):
                raise OSError("fine")
            return f"Key{index}"

        fake_winreg.EnumKey.side_effect = enum_key
        fake_winreg.QueryValueEx.side_effect = [
            (name, None) for name in display_names
        ] * 2  # una volta per HKCU, una per HKLM
        return fake_winreg

    def test_lists_installed_apps_deduplicated_and_sorted(self):
        fake_winreg = self._fake_winreg(["Zeta App", "Alpha App"])
        with mock.patch("skills.system_maintenance.winreg", fake_winreg):
            result = ListInstalledAppsSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["apps"], ["Alpha App", "Zeta App"])

    def test_name_filter_matches_case_insensitively(self):
        fake_winreg = self._fake_winreg(["Visual Studio Code", "Notepad++"])
        with mock.patch("skills.system_maintenance.winreg", fake_winreg):
            result = ListInstalledAppsSkill().execute({"name": "visual studio"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["apps"], ["Visual Studio Code"])

    def test_no_matches_reports_not_found(self):
        fake_winreg = self._fake_winreg(["Notepad++"])
        with mock.patch("skills.system_maintenance.winreg", fake_winreg):
            result = ListInstalledAppsSkill().execute({"name": "programma inesistente"})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_missing_registry_key_is_skipped_not_a_crash(self):
        fake_winreg = mock.MagicMock()
        fake_winreg.HKEY_CURRENT_USER = "HKCU"
        fake_winreg.HKEY_LOCAL_MACHINE = "HKLM"
        fake_winreg.OpenKey.side_effect = OSError("nessuna chiave")
        with mock.patch("skills.system_maintenance.winreg", fake_winreg):
            result = ListInstalledAppsSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")


class GetWifiStatusTests(unittest.TestCase):
    def test_a_failure_is_reported_not_raised(self):
        with mock.patch("subprocess.run", side_effect=OSError("boom")):
            result = GetWifiStatusSkill().execute({})
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_no_ssid_reports_not_found(self):
        completed = mock.MagicMock(stdout="nessuna interfaccia connessa")
        with mock.patch("subprocess.run", return_value=completed):
            result = GetWifiStatusSkill().execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_extracts_ssid_and_signal(self):
        completed = mock.MagicMock(stdout="    SSID                   : CasaWiFi\n    Signal                 : 87%\n")
        with mock.patch("subprocess.run", return_value=completed):
            result = GetWifiStatusSkill().execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["ssid"], "CasaWiFi")
        self.assertEqual(result.data["signal_percent"], 87)

    def test_missing_signal_still_succeeds(self):
        completed = mock.MagicMock(stdout="    SSID                   : CasaWiFi\n")
        with mock.patch("subprocess.run", return_value=completed):
            result = GetWifiStatusSkill().execute({})
        self.assertTrue(result.success)
        self.assertIsNone(result.data["signal_percent"])


if __name__ == "__main__":
    unittest.main()
