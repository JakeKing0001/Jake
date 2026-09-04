from core.skill_result import SkillResult


class SetTriggerSkill:
    """Collega un'automazione salvata a una condizione che la fa partire da sola (v3.0)."""

    metadata = {
        "intent": "SET_TRIGGER",
        "description": "Fa partire automaticamente un'automazione gia' salvata, a un orario "
        "fisso ogni giorno oppure quando una certa app va in primo piano.",
        "parameters": {
            "name": {
                "type": "string",
                "required": True,
                "description": "Nome con cui identificare questo trigger.",
            },
            "workflow_name": {
                "type": "string",
                "required": True,
                "description": "Nome dell'automazione gia' salvata (con SAVE_WORKFLOW) da far partire.",
            },
            "trigger_type": {
                "type": "string",
                "required": True,
                "description": "'time' per un orario fisso ogni giorno, 'app_focus' per quando un'app va in primo piano.",
            },
            "at_time": {
                "type": "string",
                "required": False,
                "description": "Orario 'HH:MM' (24 ore), obbligatorio se trigger_type e' 'time'.",
            },
            "app_contains": {
                "type": "string",
                "required": False,
                "description": "Testo da cercare nel titolo della finestra, obbligatorio se trigger_type e' 'app_focus'.",
            },
        },
    }

    def __init__(self, trigger_manager):
        self.trigger_manager = trigger_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        name = (parameters.get("name") or "").strip()
        workflow_name = (parameters.get("workflow_name") or "").strip()
        trigger_type = (parameters.get("trigger_type") or "").strip()
        at_time = (parameters.get("at_time") or "").strip()
        app_contains = (parameters.get("app_contains") or "").strip()

        if not name or not workflow_name or trigger_type not in ("time", "app_focus"):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        if trigger_type == "time" and not at_time:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        if trigger_type == "app_focus" and not app_contains:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        if not self.trigger_manager.workflow_exists(workflow_name):
            return SkillResult(success=False, data={"name": workflow_name}, error="NOT_FOUND")

        spec = {"at": at_time} if trigger_type == "time" else {"app_contains": app_contains}
        self.trigger_manager.save(name, workflow_name, trigger_type, spec)
        return SkillResult(success=True, data={"name": name, "workflow_name": workflow_name})


class ListTriggersSkill:
    metadata = {
        "intent": "LIST_TRIGGERS",
        "description": "Elenca i trigger impostati per far partire automazioni da sole.",
        "parameters": {},
    }

    def __init__(self, trigger_manager):
        self.trigger_manager = trigger_manager

    def execute(self, parameters: dict = None):
        triggers = self.trigger_manager.list_all()
        if not triggers:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"triggers": triggers})


class DeleteTriggerSkill:
    metadata = {
        "intent": "DELETE_TRIGGER",
        "description": "Rimuove un trigger, l'automazione collegata smette di partire da sola.",
        "parameters": {
            "name": {
                "type": "string",
                "required": True,
                "description": "Nome del trigger da rimuovere.",
            },
        },
    }

    def __init__(self, trigger_manager):
        self.trigger_manager = trigger_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        name = (parameters.get("name") or "").strip()
        if not name:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        removed = self.trigger_manager.delete(name)
        if not removed:
            return SkillResult(success=False, data={"name": name}, error="NOT_FOUND")
        return SkillResult(success=True, data={"name": name})
