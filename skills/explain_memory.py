from core.skill_result import SkillResult


class ExplainMemorySkill:
    """F5.7.1/F5.7.3: "da dove sai X?", "perche' ricordi X?". Spiega un ricordo senza modificarlo: chi
    l'ha salvato, da quale fonte, quanto e' sensibile, quando scade, quante volte e' stato usato. Usa
    `core/memory_privacy.py::MemoryPrivacyDashboard` (la stessa spiegazione della privacy dashboard)."""

    metadata = {
        "intent": "EXPLAIN_MEMORY",
        "description": (
            "Spiega perche' Jake ricorda qualcosa e da dove viene l'informazione (chi l'ha salvata, fonte, "
            "sensibilita', scadenza, quante volte e' stata usata). Per 'da dove sai X', 'perche' ricordi X', "
            "'chi ti ha detto X'. Non modifica nulla."
        ),
        "parameters": {
            "key": {
                "type": "string",
                "required": True,
                "description": "Il ricordo di cui l'utente chiede la provenienza, con le sue parole.",
            },
        },
    }

    MAX_RECORDS = 3

    def __init__(self, dashboard):
        self.dashboard = dashboard

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        key = (parameters.get("key") or "").strip()
        if not key:
            return SkillResult(success=False, data={"key": key}, error="MISSING_PARAMETERS")
        records = self.dashboard.search(key, limit=self.MAX_RECORDS)
        if not records:
            return SkillResult(success=False, data={"key": key}, error="NOT_FOUND")
        return SkillResult(success=True, data={
            "key": key,
            "explanations": [self.dashboard.explain(record) for record in records],
        })
