import json
from urllib import error, request

from core.skill_result import SkillResult
from core.ollama_client import DEFAULT_BASE_URL


class ListModelsSkill:
    metadata = {
        "intent": "LIST_MODELS",
        "description": "Elenca i modelli Ollama installati su questo computer.",
        "parameters": {},
    }

    def __init__(self, base_url: str = None, timeout: float = 10):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def execute(self, parameters: dict = None):
        try:
            with request.urlopen(f"{self.base_url}/api/tags", timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError, json.JSONDecodeError):
            return SkillResult(success=False, data={}, error="OLLAMA_UNAVAILABLE")

        models = [model["name"] for model in payload.get("models", [])]
        if not models:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"models": models})


class SetModelSkill:
    """Cambia il modello Ollama usato da Jake, subito e in modo persistente (v2.0: modelli
    intercambiabili). Aggiorna tutti i componenti che parlano con Ollama (classificatore,
    planner, riassuntore) e salva la scelta in config/settings.json per i prossimi avvii."""

    metadata = {
        "intent": "SET_MODEL",
        "description": "Cambia il modello Ollama che Jake usa per capire le richieste (deve essere gia' scaricato con 'ollama pull').",
        "parameters": {
            "model": {
                "type": "string",
                "required": True,
                "description": "Nome esatto del modello Ollama, es. 'qwen2.5:7b'.",
            },
        },
    }

    def __init__(self, updatable_targets: list, config):
        # Oggetti che espongono un attributo .model da tenere allineato (classificatore
        # single-intent, planner, riassuntore della cronologia).
        self.updatable_targets = updatable_targets
        self.config = config

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        model = (parameters.get("model") or "").strip()
        if not model:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        for target in self.updatable_targets:
            target.model = model
        self.config.set("ollama_model", model)

        return SkillResult(success=True, data={"model": model})
