from core.memory_manager import MemoryManager
from core.skill_result import SkillResult

# F5.4.3: sostituire un ricordo importante (importanza 4-5 o fissato) con un valore diverso si conferma prima
IMPORTANT_CONFLICT_THRESHOLD = 4


def _same(a: str, b: str) -> bool:
    return " ".join(str(a).lower().split()) == " ".join(str(b).lower().split())


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
            "ttl_days": {
                "type": "integer",
                "required": False,
                "description": "Se il ricordo vale solo per un periodo limitato (es. 'oggi piove', 'ricordati che scade venerdi''), "
                "il numero di giorni dopo cui puo' essere dimenticato. Ometti per un ricordo permanente: non inventare "
                "una scadenza se l'utente non ne ha detta una.",
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
        ttl_days = self._parse_ttl_days(parameters.get("ttl_days"))

        if not key or not value:
            return SkillResult(success=False, data={"key": key, "value": value}, error="MISSING_PARAMETERS")

        entry = getattr(self.memory_manager, "entry", None)
        current = entry(key, category) if callable(entry) else None
        current = current if isinstance(current, dict) else None
        # aggiornare il testo non declassa un ricordo: senza un'importanza detta dall'utente resta quella di prima
        importance = parameters.get("importance") or (current or {}).get("importance") or 1
        if parameters.get("confirmed") is not True and category in MemoryManager.KNOWLEDGE_CATEGORIES:
            if current and not _same(current["value"], value) and (
                    (current.get("importance") or 1) >= IMPORTANT_CONFLICT_THRESHOLD or current.get("pinned")):
                return SkillResult(success=False, error="CONFIRMATION_REQUIRED", data={
                    "message": (f"Per me {key} è \"{current['value']}\" e l'avevi segnato come importante. "
                                f"Lo sostituisco con \"{value}\"? La versione di prima resta nello storico."),
                    "confirm_parameters": {**parameters, "confirmed": True},
                })

        embedding = None
        if self.embedding_provider is not None:
            embedding = self.embedding_provider.embed(f"{key}: {value}")

        outcome = self.memory_manager.remember(
            key, value, category=category, importance=importance, embedding=embedding, project=project,
            ttl_days=ttl_days,
        ) or {}
        return SkillResult(success=True, data={"key": key, "value": value, "category": category, "ttl_days": ttl_days,
                                               "status": outcome.get("status"), "previous": outcome.get("previous")})

    @staticmethod
    def _parse_ttl_days(raw) -> float | None:
        """None (nessuna scadenza, il caso normale) se raw manca o non e' un numero positivo:
        un ttl invalido non deve far fallire l'intero REMEMBER, il ricordo resta comunque
        salvato, solo senza scadenza - coerente con importance sopra, che degrada allo stesso
        modo invece di rifiutare la richiesta per un parametro facoltativo malformato."""
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None
