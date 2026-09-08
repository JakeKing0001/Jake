"""Riconoscimento di comandi su Jake stesso e riscritture di superficie del testo (v3.2).

Estratto da JakeCore: prima queste regex e i metodi che le usano vivevano come costanti
di classe e metodi privati dentro JakeCore, mescolati con l'orchestrazione della pipeline.
Sono in realta' logica pura (testo in ingresso -> testo/Command in uscita, nessuno stato
mutabile oltre a cio' che viene passato esplicitamente), quindi stanno meglio qui: piu'
facili da leggere isolate e da testare senza dover costruire un JakeCore intero."""

import re

from core.command import Command

# Il classificatore a singolo intent non "fallisce" su una richiesta composta: si limita a
# sceglierne una parte e scarta il resto senza segnalarlo. Questi marcatori intercettano le
# richieste esplicitamente multi-step PRIMA che accada, instradandole subito al planner.
# Escluse le richieste che DEFINISCONO un'automazione o un comando (SAVE_WORKFLOW,
# LEARN_COMMAND): li' l'intera frase composta e' voluta dentro un solo parametro libero.
# La sola "e" (congiunzione) conta come multi-step solo se seguita da un altro verbo
# d'azione noto (es. "apri opera E cerca gatti"): senza questo vincolo qualunque "e" dentro
# al contenuto di un comando singolo (es. "cerca ricette pasta e ceci") verrebbe deviata
# inutilmente sul planner invece di restare un unico intent.
_ACTION_VERBS = (
    "apri|aprimi|avvia|avviami|cerca|vai|crea|elimina|cancella|trova|chiudi|termina|"
    "spegni|riavvia|blocca|sospendi|alza|abbassa|aumenta|diminuisci|silenzia|muta|scrivi|"
    "ricordami|ricorda|ricordati|memorizza|dimentica|scorda|manda|invia|esegui|metti|togli|"
    "leggi|mostra|elenca|fai|cattura|scatta|salva|copia|sposta|rinomina|dimmi|dammi|clicca|premi"
)
MULTI_STEP_PATTERN = re.compile(
    rf"\b(?:e poi|poi|quindi|successivamente|dopodich[eé])\b|"
    rf"\be(?=\s+(?:{_ACTION_VERBS})\b)"
)
WORKFLOW_DEFINITION_PATTERN = re.compile(
    r"\bautomazion\w*\b|\bworkflow\b|\bquando dico\b|\bse dico\b|\bimpara\b|\bd'ora in poi\b|\bogni volta che dico\b"
)
# "apri youtube e cerca gatti": la seconda azione E' la prima (ricerca nel browser). Il planner
# farebbe due passi (apri sito + cerca): meglio un solo intent, che il classificatore gestisce.
BROWSER_COMBO_PATTERN = re.compile(
    r"^(?:apri|vai su)\s+(?:youtube|google|opera|chrome|edge|il browser|firefox|amazon|spotify)\s+e\s+(?:cerca|metti|riproduci|fammi sentire)\b"
)
QUESTION_PATTERN = re.compile(
    r"^(?:chi|cosa|che cosa|come|quando|dove|perch[eé]|quanto|quanti|quante|quale|quali|cos'|com'|qual|"
    r"spiegami|dimmi|raccontami|consigliami|suggeriscimi|aiutami|scrivimi|inventa|descrivi|riassumi|"
    r"sai|sapresti|potresti|puoi dirmi|mi dici|mi spieghi|secondo te)\b|\?$"
)
_REQUEST_VERBS = (
    "apri|aprimi|aprire|avvia|avviare|lancia|lanciare|cerca|cercare|vai|crea|creare|elimina|cancella|"
    "trova|chiudi|chiudere|termina|spegni|spegnere|riavvia|blocca|sospendi|alza|alzare|abbassa|abbassare|"
    "aumenta|diminuisci|silenzia|muta|scrivi|scrivere|ricordami|ricorda|memorizza|dimentica|manda|mandare|"
    "invia|inviare|esegui|eseguire|metti|mettere|togli|leggi|leggere|mostra|mostrami|elenca|fai|fare|cattura|"
    "scatta|salva|salvare|copia|sposta|rinomina|dimmi|dammi|dirmi|darmi|clicca|cliccare|premi|premere|imposta|"
    "impostare|attiva|attivare|disattiva|disattivare|riproduci|suona|fammi|porta|passa|minimizza|massimizza|"
    "descrivi|traduci|calcola|converti|controlla|verifica|pulisci|svuota|prendi|segna|appunta|cambia|usa|rispondi"
)
# "quando dico X fai Y": insegnamento deterministico, senza passare dal modello (che tende a
# eseguire Y subito invece di imparare l'associazione).
LEARN_PATTERNS = [
    re.compile(
        r"^(?:impara(?: che)?|ricordati che|ricorda che|d'ora in poi|da ora in poi|da adesso|da oggi|"
        r"ogni volta che|tutte le volte che)?[\s,]*(?:se|quando)\s+(?:ti\s+)?dico\s+(?P<phrase>.+?)"
        r"[\s,:]*(?:devi|dovrai|dovresti|allora|tu|fai|fa'|esegui|vuol dire che|significa che|intendo che|voglio che)?[\s,]*"
        rf"(?P<request>(?:{_REQUEST_VERBS})\b.+)$"
    ),
    re.compile(r"^impara\s*:?\s*(?P<phrase>.+?)\s*(?:=|->|=>|significa|vuol dire)\s*(?P<request>.+)$"),
]
CORRECTION_PATTERNS = [
    re.compile(
        r"^(?:no|nope|sbagliato|errato|non intendevo(?: quello)?|non era quello|non volevo quello)[\s,.!]*"
        r"(?:intendevo dire|intendevo|volevo dire|volevo|dovevi|devi|era|dicevo|ho detto)\s+(?P<request>.+)$"
    ),
    re.compile(r"^(?:intendevo dire|intendevo|volevo dire|dovevi)\s+(?P<request>.+)$"),
]
POSITIVE_ANSWERS = {"si", "sì", "yes", "y", "ok", "okay", "va bene", "certo", "confermo", "procedi", "vai", "esatto", "sisi", "si si", "sì sì", "conferma", "fallo", "assolutamente"}
NEGATIVE_ANSWERS = {"no", "n", "annulla", "cancel", "lascia stare", "non farlo", "no grazie", "nope", "negativo", "ferma", "stop"}

EXIT_WORDS = ("esci", "usci", "chiudi jake", "spegniti", "jake spegniti")
EXIT_PREFIXES = ("esci ", "usci ")

# Riferimenti ("aprilo", "chiudilo"): risolti col riferimento piu' recente adatto (v3.1).
# Ancorati a tutta la frase (^...$) apposta: non devono scattare su un "quello" dentro una
# frase piu' lunga, solo su un comando pronominale secco.
_PRONOUN_OPTIONAL = r"\s*(?:lo|la|li|le|quello|quella|questo|questa)?$"
_PRONOUN_OPEN = re.compile(r"^(?:apri|aprilo|aprila|aprimelo|aprimela)" + _PRONOUN_OPTIONAL)
_PRONOUN_CLOSE = re.compile(r"^(?:chiudi|chiudilo|chiudila)" + _PRONOUN_OPTIONAL)
_PRONOUN_READ = re.compile(r"^(?:leggi|leggilo|leggila|leggimelo|leggimela)" + _PRONOUN_OPTIONAL)
_PRONOUN_DELETE = re.compile(r"^(?:elimina|eliminalo|eliminala|cancella|cancellalo|cancellala)" + _PRONOUN_OPTIONAL)


def is_exit(text: str) -> bool:
    return text in EXIT_WORDS or text.startswith(EXIT_PREFIXES)


def is_multi_step_request(text: str) -> bool:
    """Vero se il testo va instradato al planner/agente invece che al classificatore a
    singolo intent: contiene un marcatore multi-step ma non e' la definizione di
    un'automazione ne' il combo browser (che restano volutamente un intent solo)."""
    return bool(
        MULTI_STEP_PATTERN.search(text)
        and not WORKFLOW_DEFINITION_PATTERN.search(text)
        and not BROWSER_COMBO_PATTERN.search(text)
    )


def is_question(text: str) -> bool:
    return bool(QUESTION_PATTERN.search(text))


def is_positive_answer(text: str) -> bool:
    return text in POSITIVE_ANSWERS


def is_negative_answer(text: str) -> bool:
    return text in NEGATIVE_ANSWERS


def match_meta_command(text: str, has_last_exchange: bool) -> Command | None:
    """Comandi su Jake stesso riconosciuti da regole precise (insegnare, correggere): troppo
    importanti per lasciarli al modello, che a volte esegue invece di imparare."""
    for pattern in LEARN_PATTERNS:
        match = pattern.match(text)
        if match:
            phrase = match.group("phrase").strip(" ,:")
            request = match.group("request").strip()
            if phrase and request:
                return Command("LEARN_COMMAND", {"phrase": phrase, "request": request})
    for pattern in CORRECTION_PATTERNS:
        match = pattern.match(text)
        if match and has_last_exchange:
            return Command("CORRECT_LAST", {"request": match.group("request").strip()})
    return None


def resolve_pronouns(text: str, entities: dict) -> str:
    """'aprilo', 'chiudilo', 'leggilo', 'eliminalo': sostituisce il riferimento generico
    con l'ultima entita' pertinente (file, app, finestra...) ricordata da conversation_state.
    Se non c'e' nulla di adatto in memoria, lascia il testo com'era: meglio UNKNOWN (o una
    domanda dell'agente) che un valore inventato."""
    if not entities:
        return text
    if _PRONOUN_OPEN.match(text):
        target = entities.get("path") or entities.get("app") or entities.get("url")
        if target:
            return f"apri {target}"
    elif _PRONOUN_CLOSE.match(text):
        target = entities.get("app") or entities.get("title")
        if target:
            return f"chiudi {target}"
    elif _PRONOUN_READ.match(text):
        target = entities.get("path")
        if target:
            return f"leggi il file {target}"
    elif _PRONOUN_DELETE.match(text):
        target = entities.get("path")
        if target:
            return f"elimina {target}"
    return text
