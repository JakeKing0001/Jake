"""Skill di apprendimento (v3.0): insegnare comandi, correggere, dimenticare.

Queste skill hanno bisogno del core (router, planner, learning manager): vengono registrate
in JakeCore, non in SkillRegistry."""
from core.command import Command
from core.skill_result import SkillResult


class LearnCommandSkill:
    metadata = {
        "intent": "LEARN_COMMAND",
        "description": "Insegna a Jake un comando personalizzato: da ora in poi, quando l'utente dice la "
        "frase, Jake esegue la richiesta associata (anche composta da piu' azioni). Usalo per "
        "'impara che quando dico X devi fare Y', 'quando dico X fai Y', 'd'ora in poi se dico X ...'.",
        "parameters": {
            "phrase": {"type": "string", "required": True, "description": "La frase-comando che l'utente dira' (solo quella, senza 'quando dico')."},
            "request": {"type": "string", "required": True, "description": "Cosa deve fare Jake quando la sente, con le parole dell'utente."},
        },
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        phrase = self.core.normalizer.normalize(parameters.get("phrase") or "")
        request = self.core.normalizer.normalize(parameters.get("request") or "")
        if not phrase or not request:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        command = None
        kind = "command"
        if self.core.MULTI_STEP_PATTERN.search(request):
            plan = self.core.planner_provider.build_plan(request)
            if plan is not None and len(plan.steps) >= 2:
                self.core.skill_registry.workflow_manager.save(phrase, plan)
                command = Command("RUN_WORKFLOW", {"name": phrase})
                kind = "workflow"
        if command is None:
            command = self.core.router.detect_intent(request)
            if command.intent == "UNKNOWN":
                plan = self.core.planner_provider.build_plan(request)
                if plan is None or not plan.steps:
                    return SkillResult(success=False, data={"request": request}, error="PLAN_FAILED")
                self.core.skill_registry.workflow_manager.save(phrase, plan)
                command = Command("RUN_WORKFLOW", {"name": phrase})
                kind = "workflow"

        self.core.learning.teach(phrase, command.intent, command.parameters)
        description = self.core.describe_command(command)
        return SkillResult(success=True, data={
            "phrase": phrase, "intent": command.intent, "parameters": command.parameters,
            "kind": kind, "description": description,
        })


class ListLearnedSkill:
    metadata = {
        "intent": "LIST_LEARNED",
        "description": "Elenca i comandi personalizzati che l'utente ha insegnato a Jake.",
        "parameters": {},
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        taught = self.core.learning.list_taught()
        if not taught:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        commands = [
            {"phrase": example.text, "intent": example.intent, "description": self.core.describe_command(Command(example.intent, example.parameters))}
            for example in taught
        ]
        return SkillResult(success=True, data={"commands": commands, "auto_count": self.core.learning.count_auto()})


class ForgetLearnedSkill:
    metadata = {
        "intent": "FORGET_LEARNED",
        "description": "Cancella un comando personalizzato insegnato in precedenza, data la frase.",
        "parameters": {
            "phrase": {"type": "string", "required": True, "description": "La frase del comando da dimenticare."},
        },
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        phrase = self.core.normalizer.normalize(parameters.get("phrase") or "")
        if not phrase:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        if not self.core.learning.forget(phrase):
            return SkillResult(success=False, data={"phrase": phrase}, error="NOT_FOUND")
        return SkillResult(success=True, data={"phrase": phrase})


class CorrectLastSkill:
    metadata = {
        "intent": "CORRECT_LAST",
        "description": "L'utente corregge l'ultimo comando capito male ('no, intendevo ...', 'sbagliato, "
        "volevo dire ...'): Jake esegue la richiesta corretta e impara ad associarla alla frase precedente.",
        "parameters": {
            "request": {"type": "string", "required": True, "description": "La richiesta corretta, con le parole dell'utente."},
        },
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        request = (parameters.get("request") or "").strip()
        if not request:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        response = self.core.apply_correction(request)
        return SkillResult(success=True, data={"response": response})
