"""Skill che espongono la fucina (v3.0): creare, elencare, eliminare capacita' auto-generate.

F1.3 (execution_safety.INTENT_SAFETY_REGISTRY, verificatore per CREATE_SKILL/DELETE_CREATED_
SKILL): "path" in aggiunta a "file" (solo il nome, non basta a ricostruire il percorso completo
altrove - plugins_dir e' iniettabile, non un valore fisso da poter assumere in un verificatore
indipendente) - vedi core/execution_safety.py."""
from core.skill_forge import ForgeError
from core.skill_result import SkillResult


class CreateSkillSkill:
    metadata = {
        "intent": "CREATE_SKILL",
        "description": "Jake si scrive da solo una NUOVA capacita' che ancora non ha (genera il codice, lo "
        "verifica e chiede conferma prima di attivarlo). Usalo SOLO per richieste esplicite tipo 'impara "
        "a fare X', 'crea una capacita' che ...', 'scriviti una skill per ...'. Mai per cose che sai gia' fare.",
        "parameters": {
            "request": {"type": "string", "required": True, "description": "Cosa deve saper fare la nuova capacita', con le parole dell'utente."},
        },
    }

    def __init__(self, forge):
        self.forge = forge

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        request = (parameters.get("request") or "").strip()
        if not request:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        if parameters.get("confirmed") and parameters.get("draft_id"):
            try:
                draft, path = self.forge.install(parameters["draft_id"])
            except ForgeError as exc:
                return SkillResult(success=False, data={"message": str(exc)}, error="FORGE_FAILED")
            return SkillResult(success=True, data={
                "intent": draft.intent, "description": draft.description,
                "examples": draft.examples, "file": path.name, "path": str(path),
            })

        try:
            draft = self.forge.propose(request)
        except ForgeError as exc:
            return SkillResult(success=False, data={"message": str(exc)}, error="FORGE_FAILED")

        example = draft.examples[0] if draft.examples else ""
        message = (
            f"Ho scritto una nuova capacità, {draft.intent.replace('_', ' ').lower()}: {draft.description} "
            f"Si userà dicendo per esempio «{example}». La attivo?"
        )
        return SkillResult(
            success=False,
            data={
                "message": message,
                "confirm_parameters": {"request": request, "draft_id": draft.draft_id, "confirmed": True},
                "intent": draft.intent, "description": draft.description, "code": draft.code,
            },
            error="CONFIRMATION_REQUIRED",
        )


class ListCreatedSkillsSkill:
    metadata = {
        "intent": "LIST_CREATED_SKILLS",
        "description": "Elenca le capacita' che Jake ha creato da solo (plugin auto-generati).",
        "parameters": {},
    }

    def __init__(self, forge):
        self.forge = forge

    def execute(self, parameters: dict = None):
        created = self.forge.list_created()
        if not created:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"skills": created})


class DeleteCreatedSkillSkill:
    metadata = {
        "intent": "DELETE_CREATED_SKILL",
        "description": "Elimina una capacita' creata da Jake (per nome o argomento).",
        "parameters": {
            "name": {"type": "string", "required": True, "description": "Nome o argomento della capacita' da eliminare."},
        },
    }

    def __init__(self, forge, learning=None):
        self.forge = forge
        self.learning = learning

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        name = (parameters.get("name") or "").strip()
        if not name:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        info = self.forge.delete(name)
        if info is None:
            return SkillResult(success=False, data={"name": name}, error="NOT_FOUND")
        if self.learning is not None and info.get("intent"):
            self.learning.forget_intent(info["intent"])
        return SkillResult(success=True, data=info)
