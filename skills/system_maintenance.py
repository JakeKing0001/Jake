import re
import shutil
import subprocess
import tempfile
import time
import winreg
from pathlib import Path

from core.skill_result import SkillResult


def _wait_until_explorer_running(wait_seconds: float, poll_interval: float = 0.1) -> bool:
    """F1.3.2 (stesso pattern gia' trovato e corretto tre volte in questa sessione per
    processi/finestre/casa - vedi skills/window_control.py::_wait_until_window_closed):
    subprocess.Popen("explorer.exe") e' fire-and-forget quanto PostMessage(WM_CLOSE) - non
    dice se Explorer sia DAVVERO ripartito, solo che il tentativo di avviarlo non ha sollevato
    un'eccezione immediata. Attende fino a wait_seconds che almeno un processo explorer.exe
    compaia (psutil, nessuna dipendenza da un PID specifico: qui non se ne conosce uno prima
    di riavviarlo, a differenza di KillProcessByPortSkill)."""
    import psutil

    deadline = time.monotonic() + wait_seconds
    while True:
        for process in psutil.process_iter(["name"]):
            try:
                if (process.info.get("name") or "").lower() == "explorer.exe":
                    return True
            except psutil.Error:
                continue
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval)


class ClearTempFilesSkill:
    """Ripulisce %TEMP%: azione irreversibile (i file non vanno nel cestino), quindi richiede
    sempre conferma. Gli errori di permessi su singoli file (in uso da altri processi) vengono
    ignorati invece di far fallire l'intera pulizia."""

    metadata = {
        "intent": "CLEAR_TEMP_FILES",
        "description": "Elimina i file temporanei accumulati in %TEMP%, liberando spazio su disco.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        if not parameters.get("confirmed"):
            return SkillResult(
                success=False,
                data={
                    "message": "Confermi di voler eliminare i file temporanei? Non finiranno nel cestino.",
                    "confirm_parameters": {"confirmed": True},
                },
                error="CONFIRMATION_REQUIRED",
            )

        temp_dir = Path(tempfile.gettempdir())
        deleted = 0
        for entry in temp_dir.iterdir():
            try:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                deleted += 1
            except OSError:
                continue

        return SkillResult(success=True, data={"deleted": deleted})


class RestartExplorerSkill:
    """F1.3.2: prima di questa correzione, success=True veniva restituito subito dopo
    subprocess.Popen("explorer.exe"), senza aspettare che Explorer fosse DAVVERO ripartito -
    vedi _wait_until_explorer_running sopra per il ragionamento completo."""

    EXPLORER_RESTART_WAIT_SECONDS = 10.0
    # Reso configurabile SOLO per i test, stesso principio gia' usato per CLOSE_WAIT_SECONDS
    # in skills/close_window.py - il comportamento di produzione non cambia.
    _POLL_INTERVAL_SECONDS = 0.1

    metadata = {
        "intent": "RESTART_EXPLORER",
        "description": "Riavvia Esplora risorse di Windows (utile se il desktop o la barra delle applicazioni si bloccano).",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        try:
            subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], check=True, capture_output=True)
            subprocess.Popen("explorer.exe")
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        if _wait_until_explorer_running(self.EXPLORER_RESTART_WAIT_SECONDS, self._POLL_INTERVAL_SECONDS):
            return SkillResult(success=True, data={})
        return SkillResult(success=False, data={}, error="OPERATION_FAILED")


class FlushDnsSkill:
    metadata = {
        "intent": "FLUSH_DNS",
        "description": "Svuota la cache DNS del computer (utile se un sito non si raggiunge piu' dopo un cambio di server).",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        try:
            subprocess.run(["ipconfig", "/flushdns"], check=True, capture_output=True, timeout=15)
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")
        return SkillResult(success=True, data={})


class ListStartupAppsSkill:
    """Legge le voci di avvio automatico da registro (HKCU\\...\\Run): copre la maggior parte dei
    programmi che si avviano da soli con Windows, non i servizi di sistema."""

    metadata = {
        "intent": "LIST_STARTUP_APPS",
        "description": "Elenca i programmi che si avviano automaticamente all'accensione del computer.",
        "parameters": {},
    }

    REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def execute(self, parameters: dict = None):
        apps = []
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.REGISTRY_PATH) as key:
                index = 0
                while True:
                    try:
                        name, _value, _type = winreg.EnumValue(key, index)
                        apps.append(name)
                        index += 1
                    except OSError:
                        break
        except OSError:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        if not apps:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"apps": apps})


class ListInstalledAppsSkill:
    metadata = {
        "intent": "LIST_INSTALLED_APPS",
        "description": "Elenca i programmi installati sul computer (i primi risultati, eventualmente filtrati per nome).",
        "parameters": {
            "name": {
                "type": "string",
                "required": False,
                "description": "Filtra per nome (anche parziale). Se omesso elenca i primi trovati.",
            },
        },
    }

    UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    MAX_RESULTS = 20

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        name_filter = (parameters.get("name") or "").strip().lower()
        apps = []

        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, self.UNINSTALL_KEY) as root_key:
                    index = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(root_key, index)
                            index += 1
                        except OSError:
                            break
                        try:
                            with winreg.OpenKey(root_key, subkey_name) as subkey:
                                display_name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                        except OSError:
                            continue
                        if display_name and (not name_filter or name_filter in display_name.lower()):
                            apps.append(display_name)
                            if len(apps) >= self.MAX_RESULTS:
                                break
            except OSError:
                continue

        if not apps:
            return SkillResult(success=False, data={"name": name_filter}, error="NOT_FOUND")
        return SkillResult(success=True, data={"apps": sorted(set(apps))})


class SetPowerPlanSkill:
    metadata = {
        "intent": "SET_POWER_PLAN",
        "description": "Cambia il piano di alimentazione del computer.",
        "parameters": {
            "plan": {
                "type": "string",
                "required": True,
                "description": "Uno tra: 'balanced' (bilanciato), 'high_performance' (prestazioni elevate), 'power_saver' (risparmio energetico).",
            },
        },
    }

    PLAN_GUIDS = {
        "balanced": "381b4222-f694-41f0-9685-ff5bb260df2e",
        "high_performance": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
        "power_saver": "a1841308-3541-4fab-bc81-f71556f20b4a",
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        plan = (parameters.get("plan") or "").strip().lower()
        guid = self.PLAN_GUIDS.get(plan)
        if guid is None:
            return SkillResult(success=False, data={"plan": plan}, error="MISSING_PARAMETERS")

        try:
            subprocess.run(["powercfg", "/setactive", guid], check=True, capture_output=True, timeout=10)
        except Exception:
            return SkillResult(success=False, data={"plan": plan}, error="OPERATION_FAILED")
        return SkillResult(success=True, data={"plan": plan})


class ToggleDarkModeSkill:
    metadata = {
        "intent": "TOGGLE_DARK_MODE",
        "description": "Attiva o disattiva il tema scuro di Windows.",
        "parameters": {
            "enabled": {
                "type": "boolean",
                "required": True,
                "description": "true per attivare il tema scuro, false per quello chiaro.",
            },
        },
    }

    REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        enabled = parameters.get("enabled")
        if not isinstance(enabled, bool):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        value = 0 if enabled else 1
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.REGISTRY_PATH, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, value)
                winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, value)
        except OSError:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")
        return SkillResult(success=True, data={"enabled": enabled})


class GetWifiStatusSkill:
    metadata = {
        "intent": "GET_WIFI_STATUS",
        "description": "Restituisce la rete Wi-Fi a cui il computer e' connesso e l'intensita' del segnale.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        try:
            output = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                check=True, capture_output=True, timeout=15, text=True, encoding="utf-8", errors="ignore",
            ).stdout
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        ssid_match = re.search(r"^\s*SSID\s*:\s*(.+)$", output, re.MULTILINE)
        signal_match = re.search(r"^\s*Signal\s*:\s*(\d+)%", output, re.MULTILINE)
        if not ssid_match:
            return SkillResult(success=False, data={}, error="NOT_FOUND")

        return SkillResult(success=True, data={
            "ssid": ssid_match.group(1).strip(),
            "signal_percent": int(signal_match.group(1)) if signal_match else None,
        })
