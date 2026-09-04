from core.skill_result import SkillResult


class RememberSkill:
    metadata = {
        "intent": "REMEMBER",
        "description": "Salva un'informazione o una preferenza in memoria a lungo termine.",
        "parameters": {
            "key": {
                "type": "string",
                "required": True,
                "description": (
                    "Nome breve con cui identificare l'informazione, nella stessa lingua e con le "
                    "stesse parole usate dall'utente. Non tradurre e non riassumere."
                ),
            },
            "value": {
                "type": "string",
                "required": True,
                "description": "Contenuto da memorizzare, con le stesse parole usate dall'utente.",
            },
            "category": {
                "type": "string",
                "required": False,
                "description": "Categoria del ricordo, ad es. 'fact' o 'preference'. Default 'fact'.",
            },
            "project": {
                "type": "string",
                "required": False,
                "description": "Progetto a cui appartiene questo ricordo, se l'utente lo specifica (es. 'NEST', 'Jake').",
            },
            "importance": {
                "type": "integer",
                "required": False,
                "description": "Priorita' da 1 (normale) a 5 (molto importante). Usa un valore alto solo se l'utente lo chiede esplicitamente.",
            },
        },
    }

    def __init__(self, memory_manager, embedding_provider=None):
        self.memory_manager = memory_manager
        self.embedding_provider = embedding_provider

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        key = (parameters.get("key") or "").strip()
        value = (parameters.get("value") or "").strip()
        category = (parameters.get("category") or "fact").strip()
        project = (parameters.get("project") or "").strip() or None
        importance = parameters.get("importance") or 1

        if not key or not value:
            return SkillResult(success=False, data={"key": key, "value": value}, error="MISSING_PARAMETERS")

        embedding = None
        if self.embedding_provider is not None:
            embedding = self.embedding_provider.embed(f"{key}: {value}")

        self.memory_manager.remember(
            key, value, category=category, importance=importance, embedding=embedding, project=project,
        )
        return SkillResult(success=True, data={"key": key, "value": value, "category": category})
