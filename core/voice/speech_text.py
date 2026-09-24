"""Testo -> unita' pronunciabili (F2.5.1, F2.5.4).

Una risposta di Jake e' scritta per essere LETTA (Markdown, blocchi di codice, link, elenchi) ma
la voce deve dirla: leggere "asterisco asterisco importante asterisco asterisco" o un blocco di
codice riga per riga e' il peggior modo di usare un TTS. Questo modulo e' puro (nessun audio, nessuna
rete) in due passi:

1. `clean_for_speech` toglie la sintassi Markdown, sostituisce il codice con una frase e i link con
   il loro testo;
2. `split_prosodic` spezza il risultato in unita' che finiscono dove una persona respirerebbe
   (fine frase, poi virgola/punto e virgola per le frasi molto lunghe), senza tagliare "3.5", "es."
   o "10.000", e unendo i frammenti troppo brevi."""
from __future__ import annotations

import re
from dataclasses import dataclass

CODE_OMITTED = "Ti ho lasciato il codice a schermo."
_ABBREVIATIONS = ("es", "ecc", "sig", "sigg", "dott", "prof", "ing", "avv", "sig.ra", "cfr", "pag", "tel", "n", "art", "vs")

_FENCE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_UNCLOSED_FENCE = re.compile(r"```.*\Z|~~~.*\Z", re.DOTALL)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_BARE_URL = re.compile(r"https?://[^\s)]+")
_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s*")
_BLOCKQUOTE = re.compile(r"(?m)^\s{0,3}>\s?")
_LIST_MARKER = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+")
_RULE = re.compile(r"(?m)^\s*(?:[-*_]\s*){3,}$")
_TABLE_SEPARATOR = re.compile(r"(?m)^\s*\|?[\s:|-]{3,}\|[\s:|-]*$")
_EMPHASIS = re.compile(r"(\*\*|\*|~~)(?=\S)(.+?)(?<=\S)\1")
# Il trattino basso conta come enfasi solo ai bordi di parola: dentro "mio_file_di_test" e' parte del nome.
_UNDERSCORE_EMPHASIS = re.compile(r"(?<!\w)(__|_)(?=\S)(.+?)(?<=\S)\1(?!\w)")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿️‍]")
_PRONUNCIATION_REPLACEMENTS = (
    (re.compile(r"\bfile\b", re.IGNORECASE), "fàil"),
)

def normalize_pronunciation(text: str) -> str:
    """Adatta solo il testo pronunciato, mai quello mostrato all'utente."""
    for pattern, replacement in _PRONUNCIATION_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def clean_for_speech(text: str) -> str:
    """Rende leggibile a voce un testo Markdown. Non aggiunge informazione e non ne toglie oltre
    alla sintassi: il contenuto delle frasi resta identico."""
    had_code = bool(_FENCE.search(text) or _UNCLOSED_FENCE.search(text))
    text = _FENCE.sub("\n", text)
    text = _UNCLOSED_FENCE.sub("\n", text)  # un blocco non chiuso (risposta troncata) e' comunque codice
    text = _IMAGE.sub(lambda m: m.group(1), text)
    text = _LINK.sub(lambda m: m.group(1), text)
    text = _BARE_URL.sub("un link", text)
    text = _HTML_TAG.sub("", text)
    text = _RULE.sub("", text)
    text = _TABLE_SEPARATOR.sub("", text)
    text = _HEADING.sub("", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _LIST_MARKER.sub("", text)
    for _ in range(2):  # annidati: ***testo*** -> **testo** -> testo
        text = _EMPHASIS.sub(lambda m: m.group(2), text)
        text = _UNDERSCORE_EMPHASIS.sub(lambda m: m.group(2), text)
    text = _INLINE_CODE.sub(lambda m: m.group(1), text)
    text = text.replace("|", ", ")
    text = _EMOJI.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    lines = [line.strip() for line in text.split("\n")]
    cleaned = "\n".join(line for line in lines if line)
    if had_code:
        cleaned = (cleaned + "\n" + CODE_OMITTED).strip()
    return cleaned


def _is_sentence_end(text: str, index: int) -> bool:
    """`text[index]` e' uno tra . ! ? : e chiude davvero una frase? (non un decimale, un'abbreviazione
    o i puntini di sospensione)."""
    char = text[index]
    following = text[index + 1:index + 2]
    if following and not following.isspace():
        return False  # "3.5", "10.000", "file.txt", "?!"
    if char != ".":
        return True
    if text[max(0, index - 2):index + 1] == "...":
        return False
    word_match = re.search(r"([A-Za-z]+)$", text[:index])
    if word_match and word_match.group(1).lower() in _ABBREVIATIONS:
        return False
    if re.search(r"\b[A-Z]$", text[:index]):
        return False  # iniziale: "J. K. Rowling"
    return True


def split_sentences(text: str) -> list[str]:
    """Frasi complete, con la punteggiatura finale attaccata. Le righe vuote separano sempre."""
    sentences: list[str] = []
    for block in text.split("\n"):
        block = block.strip()
        start = 0
        for index, char in enumerate(block):
            if char in ".!?;" and _is_sentence_end(block, index):
                piece = block[start:index + 1].strip()
                if piece:
                    sentences.append(piece)
                start = index + 1
        tail = block[start:].strip()
        if tail:
            sentences.append(tail)
    return sentences


def _split_long(sentence: str, max_chars: int) -> list[str]:
    """Una frase piu' lunga di `max_chars` si spezza alla virgola (o "e"/"ma"/"che") piu' vicina
    alla meta' del limite, mai in mezzo a una parola; se non c'e' un punto naturale, allo spazio."""
    if len(sentence) <= max_chars:
        return [sentence]
    window = sentence[:max_chars]
    cut = max(window.rfind(", "), window.rfind("; "))
    if cut < max_chars // 3:
        matches = list(re.finditer(r"\s(?:e|ma|che|perche'|perché|quindi|oppure)\s", window))
        cut = matches[-1].start() if matches else -1
    if cut < max_chars // 3:
        cut = window.rfind(" ")
    if cut <= 0:
        cut = max_chars
    head, rest = sentence[:cut + 1].strip(), sentence[cut + 1:].strip()
    return [head] + (_split_long(rest, max_chars) if rest else [])


def split_prosodic(text: str, max_chars: int = 220, min_chars: int = 25) -> list[str]:
    """Unita' da sintetizzare una alla volta: frasi, con quelle piu' lunghe di `max_chars` spezzate
    a un punto naturale e quelle piu' corte di `min_chars` unite alla successiva (ogni chiamata di
    sintesi ha un costo fisso: non vale la pena farne una per "Ok.")."""
    units: list[str] = []
    for sentence in split_sentences(text):
        units.extend(_split_long(sentence, max_chars))
    merged: list[str] = []
    carry = ""
    for unit in units:
        candidate = f"{carry} {unit}".strip() if carry else unit
        if len(candidate) < min_chars:
            carry = candidate
            continue
        merged.append(candidate)
        carry = ""
    if carry:
        if merged:
            merged[-1] = f"{merged[-1]} {carry}"
        else:
            merged.append(carry)
    return merged


@dataclass(frozen=True)
class SpeechStyle:
    """Come dire una risposta (F2.5.4). `max_units` limita quante unita' si pronunciano (None = tutte);
    `rate_delta_percent` e `volume` (0-1) sono richieste che ogni provider applica se le supporta."""

    name: str
    max_units: int | None
    rate_delta_percent: int
    volume: float
    trailing_note: str = ""


STYLES: dict[str, SpeechStyle] = {
    "normal": SpeechStyle("normal", None, 0, 1.0),
    "brief": SpeechStyle("brief", 2, 8, 1.0, "Il resto e' a schermo."),
    "detailed": SpeechStyle("detailed", None, -5, 1.0),
    "whisper": SpeechStyle("whisper", 2, -10, 0.35, "Il resto e' a schermo."),
    "night": SpeechStyle("night", 1, -10, 0.45, "Il resto e' a schermo."),
}


def apply_style(units: list[str], style: SpeechStyle) -> list[str]:
    """Riduce le unita' al massimo dello stile. Se ne scarta qualcuna lo dice con una nota finale,
    cosi' chi ascolta sa che c'e' altro da leggere invece di credere che la risposta sia finita."""
    if style.max_units is None or len(units) <= style.max_units:
        return list(units)
    kept = list(units[:style.max_units])
    if style.trailing_note:
        kept.append(style.trailing_note)
    return kept


def prepare_for_speech(text: str, style: SpeechStyle = STYLES["normal"]) -> str:
    """Testo finale da dare a un provider TTS: Markdown/codice tolti (`clean_for_speech`), unita'
    limitate dallo stile (`apply_style`), poi riunite in un unico testo. Il provider riceve UNA sola
    chiamata: quelli che gia' preparano la frase successiva mentre suona la precedente (Edge TTS)
    mantengono la loro fluidita', che una chiamata per unita' interromperebbe."""
    cleaned = clean_for_speech(text)
    spoken = normalize_pronunciation(cleaned)

    units = apply_style(
        split_prosodic(spoken),
        style,
    )
    return " ".join(units) if units else ""
