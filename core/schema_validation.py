"""Validazione della busta di conferma/autenticazione (F1, Trustworthy Agent Core 3.0):
"sostituire JSON 'quasi strutturato' con JSON Schema validato per... richiesta di
chiarimento" - vedi la fase F1 in ROADMAP.md.

Non copre ogni skill (~200, ognuna con una propria forma di 'data' - farlo per tutte sarebbe un
progetto a se', non affrontato qui): copre la busta CONFIRMATION_REQUIRED/AUTH_REQUIRED, l'UNICA
struttura usata da TUTTE le skill che gestiscono da sole la propria conferma (~15, vedi
core/risk.py SELF_CONFIRMING_INTENTS - es. skills/delete_path.py) prima che JakeCore/TaskAgent la
consumino per mettere in pausa e poi rieseguire l'azione. Un bug li' - un campo mancante o del
tipo sbagliato in una nuova skill - puo' rompere in modo subdolo il ciclo conferma/esecuzione: un
"confermi?" senza messaggio vero, o peggio dei confirm_parameters malformati che fanno rieseguire
l'azione con i parametri sbagliati dopo il si' dell'utente, invece di fermarsi con un errore
leggibile."""

# confirm_intent NON e' nello schema obbligatorio: e' facoltativo per costruzione (le skill self-
# confirming come DeletePathSkill non lo includono mai, JakeCore usa l'intent corrente come
# default - vedi core/jake_core.py _resolve_and_execute/_finalize_pending_action, cosi' da prima
# di questo modulo).
REQUIRED_CONFIRM_ENVELOPE_FIELDS = {
    "message": str,
    "confirm_parameters": dict,
}


def validate_confirm_envelope(data) -> list[str]:
    """Restituisce la lista dei problemi trovati in una busta CONFIRMATION_REQUIRED/AUTH_REQUIRED
    (vuota se e' valida). Non solleva mai un'eccezione: chi chiama decide cosa fare - vedi
    core/jake_core.py, che logga un avviso e ripiega su un default sicuro invece di fidarsi di
    una busta malformata."""
    if not isinstance(data, dict):
        return [f"data deve essere un dict, non {type(data).__name__}"]
    problems = []
    for field, expected_type in REQUIRED_CONFIRM_ENVELOPE_FIELDS.items():
        if field not in data:
            problems.append(f"campo obbligatorio mancante: '{field}'")
        elif not isinstance(data[field], expected_type):
            problems.append(f"'{field}' deve essere {expected_type.__name__}, e' {type(data[field]).__name__}")
    if "confirm_intent" in data and not isinstance(data["confirm_intent"], str):
        problems.append("'confirm_intent' deve essere str se presente")
    return problems
