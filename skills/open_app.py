import os
import subprocess
import sys
import webbrowser

from core.app_resolver import AppResolver
from core.skill_result import SkillResult


class OpenAppSkill:
    metadata = {
        "intent": "OPEN_APP",
        "description": "Apre un'applicazione installata sul computer (programmi, app di Windows come "
        "Blocco note, Calcolatrice, Impostazioni, Esplora file, Terminale...). Per i siti web usa OPEN_URL.",
        "parameters": {
            "app": {
                "type": "string",
                "required": True,
                "description": "Nome dell'applicazione da aprire, come detto dall'utente.",
            },
        },
    }

    def __init__(self, app_resolver: AppResolver = None, match_threshold: float = 0.70,
                 auto_execute_threshold: float = 0.90):
        self.app_resolver = app_resolver or AppResolver(threshold=match_threshold)
        self.auto_execute_threshold = auto_execute_threshold
        self.app_resolver.start_background_discovery()

    def execute(self, parameters: dict = None):
        """Estrae 'app' dai parametri e avvia l'applicazione."""
        parameters = parameters or {}
        app = (parameters.get("app") or "").strip()
        if not app:
            return SkillResult(success=False, data={"app": ""}, error="UNSUPPORTED_APP")

        match = self.app_resolver.resolve(app)
        if match is None:
            return SkillResult(success=False, data={"app": app}, error="UNSUPPORTED_APP")

        if match.score < self.auto_execute_threshold:
            return SkillResult(
                success=False,
                data={
                    "app": match.matched_app,
                    "requested_app": app,
                    "launcher": match.launcher,
                    "score": match.score,
                    "message": f"Intendevi {match.matched_app}?",
                    "confirm_parameters": {"app": match.matched_app},
                },
                error="CONFIRMATION_REQUIRED",
            )

        if self._launch(match.launcher):
            return SkillResult(success=True, data={"app": match.matched_app})
        return SkillResult(success=False, data={"app": match.matched_app}, error="LAUNCH_FAILED")

    @staticmethod
    def _launch(launcher: str) -> bool:
        """Lancia un launcher: percorso .lnk/.exe, nome di eseguibile, URI (ms-settings:),
        app di Store (shell:AppsFolder\\...), o il browser predefinito. Con 'a|b' prova in ordine."""
        for candidate in launcher.split("|"):
            candidate = candidate.strip()
            if not candidate:
                continue
            try:
                if candidate == "__browser__":
                    return webbrowser.open("https://www.google.com")
                if sys.platform != "win32":
                    subprocess.Popen([candidate])
                    return True
                lowered = candidate.lower()
                if lowered.endswith(".lnk") or lowered.startswith("shell:") or ":" in candidate.split("\\")[0] and not lowered.endswith((".exe", ".bat", ".cmd")):
                    os.startfile(candidate)
                    return True
                try:
                    os.startfile(candidate)
                    return True
                except OSError:
                    subprocess.Popen(candidate, shell=True)
                    return True
            except Exception:
                continue
        return False
