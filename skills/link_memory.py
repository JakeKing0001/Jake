from core.skill_result import SkillResult


class LinkMemorySkill:
    """Collega due ricordi gia' salvati con una relazione con nome (v4.4, Personal Knowledge
    Graph): 'Mario lavora per Acme', 'NEST fa parte di Jake'. Entrambi devono gia' esistere
    come ricordi (creati con REMEMBER, o gia' presenti come contatto/nota): un collegamento
    verso un ricordo mai salvato non punterebbe a nulla."""

    metadata = {
        "intent": "LINK_MEMORY",
        "description": "Collega due informazioni gia' in memoria con una relazione con nome (es. 'Mario lavora "
        "per Acme', 'ricordami che NEST fa parte del progetto Jake'), cosi' ricordare l'una puo' richiamare "
        "l'altra in futuro. Entrambe devono essere gia' state salvate (con REMEMBER) prima di poterle collegare.",
        "parameters": {
            "subject": {"type": "string", "required": True, "description": "Chiave del primo ricordo, cosi' come e' stato salvato."},
            "predicate": {"type": "string", "required": True, "description": "Il tipo di relazione, con le parole dell'utente (es. 'lavora per', 'e' amico di', 'fa parte di')."},
            "object": {"type": "string", "required": True, "description": "Chiave del secondo ricordo, cosi' come e' stato salvato."},
        },
    }

    def __init__(self, memory_manager):
        self.memory_manager = memory_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        subject = (parameters.get("subject") or "").strip()
        predicate = (parameters.get("predicate") or "").strip()
        obj = (parameters.get("object") or "").strip()
        if not subject or not predicate or not obj:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        subject_matches = self.memory_manager.recall(key=subject, limit=1)
        if not subject_matches:
            return SkillResult(success=False, data={"key": subject}, error="NOT_FOUND")
        object_matches = self.memory_manager.recall(key=obj, limit=1)
        if not object_matches:
            return SkillResult(success=False, data={"key": obj}, error="NOT_FOUND")

        self.memory_manager.link(
            subject, subject_matches[0]["category"], predicate, obj, object_matches[0]["category"],
        )
        return SkillResult(success=True, data={"subject": subject, "predicate": predicate, "object": obj})
