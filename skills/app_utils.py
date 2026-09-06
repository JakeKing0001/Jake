import os
import subprocess
from pathlib import Path

from core.skill_result import SkillResult


class ListRecentFilesSkill:
    """Legge la cartella 'Recenti' di Windows (scorciatoie .lnk ai file aperti di recente in
    qualsiasi applicazione), non solo quelli di un singolo programma."""

    metadata = {
        "intent": "LIST_RECENT_FILES",
        "description": "Elenca i file aperti piu' di recente su questo computer.",
        "parameters": {},
    }

    MAX_RESULTS = 15

    def execute(self, parameters: dict = None):
        recent_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
        if not recent_dir.is_dir():
            return SkillResult(success=False, data={}, error="NOT_FOUND")

        entries = sorted(
            (entry for entry in recent_dir.glob("*.lnk") if entry.is_file()),
            key=lambda entry: entry.stat().st_mtime, reverse=True,
        )
        names = [entry.stem for entry in entries[:self.MAX_RESULTS]]
        if not names:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"files": names})


class OpenIncognitoWindowSkill:
    metadata = {
        "intent": "OPEN_INCOGNITO_WINDOW",
        "description": "Apre il browser predefinito in una finestra di navigazione in incognito/privata.",
        "parameters": {},
    }

    BROWSER_FLAGS = {
        "chrome": "--incognito", "msedge": "--inprivate", "opera": "--private",
        "brave": "--incognito", "vivaldi": "--incognito",
    }

    def execute(self, parameters: dict = None):
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
            ) as key:
                prog_id = winreg.QueryValueEx(key, "ProgId")[0]
        except OSError:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        browser_key = next((name for name in self.BROWSER_FLAGS if name in prog_id.lower()), None)
        if browser_key is None:
            return SkillResult(success=False, data={}, error="UNSUPPORTED_APP")

        try:
            subprocess.Popen([browser_key + ".exe", self.BROWSER_FLAGS[browser_key]], shell=True)
        except Exception:
            return SkillResult(success=False, data={}, error="LAUNCH_FAILED")

        return SkillResult(success=True, data={})


class EmptyClipboardSkill:
    metadata = {
        "intent": "EMPTY_CLIPBOARD",
        "description": "Svuota il contenuto attuale degli appunti.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        import win32clipboard

        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={})
