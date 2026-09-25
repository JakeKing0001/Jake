"""Azioni UI sensibili DICHIARATE dal chiamante (F3.4.3 adottato nelle skill di Computer Use).

Un click su "Invia" in un'app qualsiasi e un click su "Aggiungi" sono, per `CLICK_TEXT`/
`CLICK_ELEMENT`/`TYPE_TEXT`/`PRESS_KEY`, la stessa azione `LOCAL_REVERSIBLE`: la skill non puo'
sapere cosa fa quel bottone e NON lo deduce dal testo (fragile e dipendente dalla lingua: vedi la
regola del progetto "rischio dichiarato dal chiamante, mai inferito dal contenuto"). Chi chiama
(utente, agente, piano, procedura) dichiara l'effetto con il parametro `effect`; da quel momento:

1. la policy viene consultata PRIMA di qualunque input (un intent sintetico `UI_*` con il proprio
   livello di rischio in core/risk.py, cosi' `blocked_intents` e le capability valgono anche qui);
2. serve sempre una conferma esplicita dell'utente (busta CONFIRMATION_REQUIRED standard): un
   piano/automazione non ha nessuno che risponda e si ferma li';
3. senza un motore di policy collegato l'azione dichiarata sensibile e' bloccata (fail-closed).

Un effetto sconosciuto e' un errore, mai indovinato."""
from __future__ import annotations

from core.skill_result import SkillResult

SENSITIVE_UI_EFFECTS: dict[str, str] = {
    "send": "UI_SEND",
    "submit": "UI_SUBMIT",
    "upload": "UI_UPLOAD",
    "delete": "UI_DELETE",
    "purchase": "UI_PURCHASE",
}
_EFFECT_VERBS = {
    "send": "inviare", "submit": "confermare/inviare il modulo", "upload": "caricare un file",
    "delete": "eliminare", "purchase": "acquistare",
}

EFFECT_PARAMETER = {
    "type": "string",
    "required": False,
    "description": (
        "Solo se questa azione INVIA, CONFERMA un modulo, CARICA un file, ELIMINA o ACQUISTA qualcosa "
        "nell'app: 'send', 'submit', 'upload', 'delete' o 'purchase'. Richiede la conferma dell'utente. "
        "Ometterlo per click e tasti ordinari."
    ),
}


def gate_sensitive_ui_action(parameters: dict, policy_engine, target: str) -> SkillResult | None:
    """None = procedi. Altrimenti il risultato da restituire SENZA toccare mouse/tastiera."""
    effect = parameters.get("effect")
    if effect in (None, ""):
        return None
    key = str(effect).strip().lower()
    risk_intent = SENSITIVE_UI_EFFECTS.get(key)
    if risk_intent is None:
        return SkillResult(success=False, data={"effect": effect, "allowed": sorted(SENSITIVE_UI_EFFECTS)},
                           error="INVALID_PARAMETERS")
    if policy_engine is None:
        return SkillResult(success=False, data={"effect": key, "reason": "nessuna policy collegata"}, error="POLICY_BLOCKED")

    from core.policy_engine import PolicyDecision

    decision, reason = policy_engine.decide_interactive_with_reason(risk_intent, parameters)
    if decision == PolicyDecision.BLOCK:
        return SkillResult(success=False, data={"effect": key, "risk_intent": risk_intent, "reason": reason},
                           error="POLICY_BLOCKED")
    if decision == PolicyDecision.REQUIRE_AUTH and not parameters.get("authenticated"):
        return SkillResult(success=False, data={
            "message": f"Per {_EFFECT_VERBS[key]} con '{target}' serve l'autenticazione.",
            "confirm_parameters": {**parameters, "authenticated": True},
        }, error="AUTH_REQUIRED")
    if not parameters.get("confirmed"):
        return SkillResult(success=False, data={
            "message": f"Stai per {_EFFECT_VERBS[key]} con '{target}'. Confermi?",
            "confirm_parameters": {**parameters, "confirmed": True},
        }, error="CONFIRMATION_REQUIRED")
    return None
