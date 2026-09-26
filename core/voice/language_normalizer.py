"""Normalizzazione del testo parlato (F2.6.1, F2.6.5): numeri in lettere, sigle, nomi propri,
ellissi e riferimenti ordinali. Puro testo, nessun modello.

Cosa fa e cosa NON fa:
- non traduce mai: le parole inglesi (download, screenshot, playlist...) e i nomi in `vocabulary`
  restano com'erano. Il code-switching italiano/inglese e' normale nel parlato di chi usa un PC: la
  correzione dei nomi propri non deve "italianizzare" un termine tecnico;
- corregge un nome solo se c'e' UN candidato chiaramente piu' vicino: nel dubbio lascia il testo,
  perche' una correzione sbagliata cambia il significato in silenzio, un testo non corretto no;
- risolve un'ellissi ("e a Milano?") solo quando lo slot da riempire e' inequivocabile (un solo
  parametro testuale nell'ultimo comando): altrimenti ritorna None e si chiede."""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---- numeri in lettere -------------------------------------------------------------------------------

_UNITS = {
    "zero": 0, "uno": 1, "una": 1, "un": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6, "sette": 7,
    "otto": 8, "nove": 9, "dieci": 10, "undici": 11, "dodici": 12, "tredici": 13, "quattordici": 14,
    "quindici": 15, "sedici": 16, "diciassette": 17, "diciotto": 18, "diciannove": 19,
}
_TENS = {
    "venti": 20, "trenta": 30, "quaranta": 40, "cinquanta": 50, "sessanta": 60, "settanta": 70, "ottanta": 80,
    "novanta": 90,
}
_TENS_STEMS = {word[:-1]: value for word, value in _TENS.items()}  # "vent", "trent"...


def _parse_below_1000(word: str) -> int | None:
    """Un numero < 1000 scritto senza spazi ("centoventitre", "ventuno", "trentotto", "novecento").
    0 per la parola vuota (serve a chi somma migliaia e resto). None se non e' un numero valido."""
    if not word:
        return 0
    total = 0
    rest = word
    hundreds = re.match(r"(due|tre|quattro|cinque|sei|sette|otto|nove)?cento", rest)
    if hundreds:
        total += (_UNITS[hundreds.group(1)] if hundreds.group(1) else 1) * 100
        rest = rest[hundreds.end():]
        if not rest:
            return total
    if rest in _UNITS:
        return total + _UNITS[rest]
    for tens_word, tens_value in _TENS.items():
        stem = tens_word[:-1]
        if rest == tens_word:
            return total + tens_value
        if rest in (stem + "uno", stem + "otto"):  # elisione: ventuno, trentotto
            return total + tens_value + (1 if rest.endswith("uno") else 8)
        if rest.startswith(tens_word):  # ventidue, trentatre, novantasei
            tail = rest[len(tens_word):]
            if tail in _UNITS and 2 <= _UNITS[tail] <= 9 and tail != "otto":
                return total + tens_value + _UNITS[tail]
    return None


def italian_number_to_int(text: str) -> int | None:
    """"cinquanta" -> 50, "centoventitre" -> 123, "tremilaquattrocento" -> 3400, "mille" -> 1000.
    None se non e' un numero in lettere valido. Solo interi da 0 a 999.999."""
    word = text.strip().lower().replace("é", "e")
    if not word.isalpha():
        return None
    if word == "mille":
        return 1000
    if "mila" in word:
        head, _, tail = word.partition("mila")
        thousands = _parse_below_1000(head) if head else None
        remainder = _parse_below_1000(tail)
        if not thousands or remainder is None:
            return None
        return thousands * 1000 + remainder
    if word.startswith("mille") and len(word) > 5:
        remainder = _parse_below_1000(word[5:])
        return None if remainder is None else 1000 + remainder
    return _parse_below_1000(word)


_ARTICLE_LIKE = {"un", "una", "uno"}


def _number_token(token: str) -> tuple[int | None, str]:
    """(valore, punteggiatura finale) se `token` e' un numero in lettere, altrimenti (None, "")."""
    core = token.rstrip(".,;:!?")
    trailing = token[len(core):]
    if not core.isalpha() or core.lower() in _ARTICLE_LIKE:
        return None, ""
    value = italian_number_to_int(core)
    return (value, trailing) if value is not None else (None, "")


def numbers_to_digits(text: str) -> str:
    """Sostituisce i numeri scritti in lettere con cifre ("volume al cinquanta per cento" ->
    "volume al 50 per cento"). "un"/"una"/"uno" da soli restano parole: sono anche articoli. Numeri
    in piu' parole ("duecento cinquanta", "venti due") si uniscono solo se la somma e' plausibile
    (centinaia + resto, decine + unita', migliaia + resto) e mai attraverso una virgola."""
    parts = re.split(r"(\s+)", text)  # parole agli indici pari, spazi ai dispari
    out: list[str] = []
    i = 0
    while i < len(parts):
        value, trailing = _number_token(parts[i])
        if value is None or _is_idiom_not_number(parts, i):
            out.append(parts[i])
            i += 1
            continue
        j = i
        while not trailing and j + 2 < len(parts) and parts[j + 1].isspace():
            next_value, next_trailing = _number_token(parts[j + 2])
            if next_value is None or not _can_concatenate(value, next_value):
                break
            value += next_value
            trailing = next_trailing
            j += 2
        out.append(f"{value}{trailing}")
        i = j + 1
    return "".join(out)


_MEASURE_WORDS = {
    "minuti", "minuto", "secondi", "secondo", "ore", "ora", "giorni", "giorno", "mesi", "mese", "anni", "anno",
    "euro", "gradi", "volte", "file", "elementi", "risultati", "righe", "pagine", "settimane", "persone", "per",
}


def _is_idiom_not_number(parts: list[str], index: int) -> bool:
    """Casi in cui una parola-numero NON e' un numero: "per cento" (percentuale, resta com'e' per
    non cambiare il testo che l'NLU gia' capisce) e "sei" da solo, che e' molto piu' spesso il verbo
    "essere" ("sei pronto?") che il numero: lo e' solo davanti a un'unita' di misura."""
    word = parts[index].rstrip(".,;:!?").lower()
    if word == "cento" and index >= 2 and parts[index - 2].lower() == "per":
        return True
    if word == "sei":
        following = parts[index + 2].rstrip(".,;:!?").lower() if index + 2 < len(parts) else ""
        return following not in _MEASURE_WORDS
    return False


def _can_concatenate(left: int, right: int) -> bool:
    if left >= 100 and left % 100 == 0 and 0 < right < 100:
        return True
    if left >= 20 and left % 10 == 0 and 0 < right < 10:
        return True
    return left >= 1000 and left % 1000 == 0 and 0 < right < 1000


# ---- sigle e nomi propri ----------------------------------------------------------------------------------

ENGLISH_PASSTHROUGH = frozenset({
    "download", "upload", "screenshot", "playlist", "email", "mail", "wifi", "bluetooth", "browser", "click",
    "desktop", "file", "folder", "backup", "reset", "update", "login", "logout", "online", "offline", "app",
    "software", "hardware", "player", "stream", "streaming", "play", "pause", "stop", "shuffle", "volume",
    "timer", "reminder", "meeting", "team", "chat", "post", "link", "like", "share", "story", "video",
})

_SINGLE_LETTERS = re.compile(r"\b(?:[A-Za-z]\s+){1,}[A-Za-z]\b")


def join_spoken_acronyms(text: str) -> str:
    """"g p t" -> "GPT", "u s b" -> "USB". Solo sequenze di 2+ lettere isolate: "a" e "e" tra parole
    italiane ("a Roma e a Milano") non sono sigle, quindi si escludono sequenze con solo a/e/i/o/u."""
    def replace(match: re.Match) -> str:
        letters = match.group(0).split()
        if len(letters) < 2 or all(letter.lower() in "aeiou" for letter in letters):
            return match.group(0)
        return "".join(letter.upper() for letter in letters)

    return _SINGLE_LETTERS.sub(replace, text)


def _edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def correct_proper_names(text: str, vocabulary: list[str] | tuple[str, ...]) -> str:
    """Sostituisce una parola con il termine del vocabolario piu' vicino (nomi di app, contatti, cartelle)
    solo se: la parola non e' gia' nel vocabolario ne' un termine inglese di uso comune, e' lunga almeno 5
    lettere, la distanza e' <= 1 (<= 2 sopra le 8 lettere) e il candidato e' UNICO a quella distanza. Il
    maiuscolo iniziale della parola originale si conserva."""
    known = {term.lower() for term in vocabulary}
    single_words = [term for term in vocabulary if term.isalpha()]

    def fix(match: re.Match) -> str:
        word = match.group(0)
        lowered = word.lower()
        if len(lowered) < 5 or lowered in known or lowered in ENGLISH_PASSTHROUGH:
            return word
        limit = 2 if len(lowered) >= 9 else 1
        best_distance, candidates = limit + 1, []
        for term in single_words:
            if abs(len(term) - len(lowered)) > limit:
                continue
            distance = _edit_distance(lowered, term.lower())
            if distance < best_distance:
                best_distance, candidates = distance, [term]
            elif distance == best_distance:
                candidates.append(term)
        if best_distance > limit or len(candidates) != 1:
            return word
        replacement = candidates[0]
        return replacement[0].upper() + replacement[1:] if word[0].isupper() and not replacement[0].isupper() else replacement

    return re.sub(r"[^\W\d_]+", fix, text)


def normalize_transcript(text: str, vocabulary: list[str] | tuple[str, ...] = (), numbers: bool = True) -> str:
    """Catena: sigle -> numeri in cifre -> nomi propri. Ogni passo e' idempotente."""
    result = join_spoken_acronyms(text)
    if numbers:
        result = numbers_to_digits(result)
    if vocabulary:
        result = correct_proper_names(result, vocabulary)
    return result


# ---- ellissi e riferimenti ---------------------------------------------------------------------------------

COMMAND_VERBS = frozenset({
    "apri", "chiudi", "cerca", "metti", "scrivi", "leggi", "dimmi", "fai", "vai", "avvia", "spegni", "accendi",
    "imposta", "mostra", "ricordami", "crea", "cancella", "elimina", "invia", "manda", "riproduci", "alza",
    "abbassa", "salva", "copia", "sposta", "trova", "mostrami", "dammi", "calcola", "converti", "traduci",
    # richieste discorsive: "e spiegami X" dopo un calcolo e' una domanda nuova, non un valore per lo slot
    "spiegami", "spiega", "raccontami", "racconta", "parlami", "descrivi", "descrivimi", "riassumi", "elenca",
    "confronta", "fammi", "aiutami", "insegnami", "dimostrami",
    "open", "close", "search", "play", "set", "show", "send", "delete", "create", "turn", "explain", "tell",
})

# Un'ellissi e' un frammento ("e a Milano?", "anche Discord"): una frase lunga o una domanda e' una
# richiesta nuova. Gate hardware 26/09/2026: "e spiegami le differenze tra Java e Python" dopo "quanto
# fa 6 per 8" finiva come nuova espressione per la calcolatrice.
_ELLIPSIS_MAX_WORDS = 5
_ELLIPSIS_QUESTION_WORDS = frozenset({
    "come", "perche", "perché", "cosa", "che", "quale", "quali", "quanto", "quanti", "chi", "dove", "quando",
    "how", "why", "what", "which", "who", "where", "when",
})

_ELLIPSIS = re.compile(
    r"^(?:e poi|e invece|e|anche|pure|invece|poi)\s+(?:(?:a|in|di|da|per|su|con|al|alla|allo|ai|nel|nella|il|la|lo|l'|i|gli|le)\s+)*(.+?)[\s?!.]*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EllipsisResolution:
    intent: str
    parameters: dict
    filled_param: str
    value: str


def resolve_ellipsis(text: str, last_intent: str | None, last_parameters: dict | None) -> EllipsisResolution | None:
    """"e a Milano?" dopo "che tempo fa a Roma" -> stesso intent, parametro city=Milano. Solo se
    l'ultimo comando ha UN unico parametro testuale (lo slot e' inequivocabile) e la frase non
    contiene un verbo di comando (in quel caso e' un comando nuovo, non un'ellissi)."""
    if not last_intent or not last_parameters:
        return None
    match = _ELLIPSIS.match(text.strip())
    if not match:
        return None
    value = match.group(1).strip()
    words = [word.lower() for word in re.findall(r"\w+", value)]
    if not value or len(words) > _ELLIPSIS_MAX_WORDS or any(word in COMMAND_VERBS for word in words):
        return None
    if words[0] in _ELLIPSIS_QUESTION_WORDS:
        return None
    text_slots = [name for name, current in last_parameters.items() if isinstance(current, str)]
    if len(text_slots) != 1:
        return None
    slot = text_slots[0]
    if slot == "expression" and not re.search(r"\d", value):
        return None  # un calcolo si completa con numeri, non con parole
    updated = dict(last_parameters)
    updated[slot] = value
    return EllipsisResolution(last_intent, updated, slot, value)


_ORDINALS = {
    "primo": 0, "prima": 0, "secondo": 1, "seconda": 1, "terzo": 2, "terza": 2, "quarto": 3, "quarta": 3,
    "quinto": 4, "quinta": 4, "sesto": 5, "sesta": 5, "settimo": 6, "ottavo": 7, "nono": 8, "decimo": 9,
}
_COUNT_WORDS = {"due": 2, "tre": 3, "quattro": 4, "cinque": 5}


def resolve_ordinals(text: str, total: int) -> list[int] | None:
    """Indici (0-based) a cui si riferisce "il primo e il terzo", "l'ultimo", "i primi due", "tutti",
    "il 2" su una lista di `total` elementi. None se non ci sono riferimenti o se uno sfora la lista
    (meglio chiedere che aprire l'elemento sbagliato)."""
    lowered = text.lower()
    if total <= 0:
        return None
    if re.search(r"\b(?:tutti|tutte|ognuno|ognuna)\b", lowered):
        return list(range(total))
    match = re.search(r"\bi primi (due|tre|quattro|cinque)\b", lowered)
    if match:
        count = _COUNT_WORDS[match.group(1)]
        return list(range(min(count, total))) if count <= total else None
    match = re.search(r"\bgli ultimi (due|tre|quattro|cinque)\b", lowered)
    if match:
        count = _COUNT_WORDS[match.group(1)]
        return list(range(total - count, total)) if count <= total else None
    indexes: list[int] = []
    for word in re.findall(r"\w+", lowered):
        if word in _ORDINALS:
            indexes.append(_ORDINALS[word])
        elif word in ("ultimo", "ultima"):
            indexes.append(total - 1)
        elif word in ("penultimo", "penultima"):
            indexes.append(total - 2)
    for number in re.findall(r"\b(?:il|la|numero|n\.?)\s*(\d{1,2})\b", lowered):
        indexes.append(int(number) - 1)
    if not indexes:
        return None
    if any(index < 0 or index >= total for index in indexes):
        return None
    seen: list[int] = []
    for index in indexes:
        if index not in seen:
            seen.append(index)
    return seen
