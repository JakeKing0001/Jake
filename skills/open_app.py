import subprocess
import os
import sys

from core.app_resolver import AppResolver
from core.skill_result import SkillResult


class OpenAppSkill:
    metadata = {
        "intent": "OPEN_APP",
        "description": "Apre un'applicazione installata sul computer.",
        "parameters": {
            "app": {
                "type": "string",
                "required": True,
                "description": "Nome dell'applicazione da aprire.",
            },
        },
    }

    def __init__(self, app_resolver: AppResolver = None, match_threshold: float = 0.60,
                 auto_execute_threshold: float = 0.90):
        self.app_resolver = app_resolver or AppResolver(threshold=match_threshold)
        self.auto_execute_threshold = auto_execute_threshold

    def execute(self, parameters: dict = None):
        """
        Estrae 'app' dai parametri e avvia l'applicazione.
        Restituisce un risultato standardizzato con success, data ed error.
        """
        if parameters is None:
            parameters = {}
        
        app = parameters.get("app", "").strip()
        
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

        app_command = match.launcher
        
        try:
            if sys.platform == "win32":
                if app_command.lower().endswith(".lnk"):
                    os.startfile(app_command)
                else:
                    subprocess.Popen(app_command, shell=True)
            else:
                subprocess.Popen([app_command])
            
            return SkillResult(success=True, data={"app": match.matched_app})
        except Exception as e:
            return SkillResult(success=False, data={"app": match.matched_app}, error="LAUNCH_FAILED")
    