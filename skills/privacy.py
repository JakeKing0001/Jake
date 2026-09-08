from core.skill_result import SkillResult


class PurgeOldHistorySkill:
    """Politica di retention (v5.6, Privacy Engine): elimina la cronologia di conversazione piu'
    vecchia di N giorni. Richiede sempre conferma, ed e' sempre su richiesta esplicita
    dell'utente, mai automatica: cancellare dati senza che siano stati chiesti sarebbe un danno
    silenzioso, non una funzionalita' di privacy. Non tocca i ricordi che l'utente ha chiesto
    esplicitamente di ricordare (REMEMBER)."""

    metadata = {
        "intent": "PURGE_OLD_HISTORY",
        "description": "Elimina la cronologia di conversazione (e i suoi riassunti automatici) più vecchia di "
        "un certo numero di giorni: non tocca le informazioni salvate esplicitamente con REMEMBER. Usalo per "
        "'cancella la cronologia più vecchia di 30 giorni', 'elimina le vecchie conversazioni'.",
        "parameters": {
            "days": {"type": "integer", "required": True, "description": "Elimina tutto ciò che è più vecchio di questo numero di giorni."},
        },
    }

    def __init__(self, memory_manager):
        self.memory_manager = memory_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        days = parameters.get("days")
        if not isinstance(days, int) or days < 1:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        if not parameters.get("confirmed"):
            return SkillResult(
                success=False,
                data={
                    "days": days,
                    "message": f"Sei sicuro di voler eliminare la cronologia più vecchia di {days} giorni? Non è recuperabile.",
                    "confirm_parameters": {"days": days, "confirmed": True},
                },
                error="CONFIRMATION_REQUIRED",
            )

        removed = self.memory_manager.purge_history_older_than(days)
        return SkillResult(success=True, data={"days": days, "removed": removed})
