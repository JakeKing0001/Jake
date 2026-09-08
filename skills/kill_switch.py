"""Kill switch globale (F1, Trustworthy Agent Core 3.0): ferma subito agenti e automazioni,
senza passare dalla normale conferma si'/no - un interruttore d'emergenza che chiedesse il
permesso prima di scattare non servirebbe a molto. Vedi core/kill_switch.py per il meccanismo
condiviso e JakeCore.activate_kill_switch()/reset_kill_switch() per cosa succede davvero."""
from core.skill_result import SkillResult


class KillSwitchSkill:
    metadata = {
        "intent": "KILL_SWITCH",
        "description": "Ferma immediatamente qualunque agente e automazione in corso, senza chiedere conferma. "
        "Usalo per 'ferma tutto', 'stop di emergenza', 'fermati subito', 'blocca tutto'.",
        "parameters": {},
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        self.core.activate_kill_switch()
        return SkillResult(success=True, data={
            "text": "Fermato tutto: nessun agente o automazione procede oltre il passo in corso. "
            "Di' 'riprendi' quando vuoi riattivarli.",
        })


class ResetKillSwitchSkill:
    metadata = {
        "intent": "RESET_KILL_SWITCH",
        "description": "Riattiva agenti e automazioni dopo un kill switch (KILL_SWITCH). Usalo per 'riprendi', "
        "'riattiva tutto', 'ricomincia a lavorare', solo dopo che l'utente ha fermato tutto in precedenza.",
        "parameters": {},
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        self.core.reset_kill_switch()
        return SkillResult(success=True, data={"text": "Riattivato: agenti e automazioni possono riprendere."})
