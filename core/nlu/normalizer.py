"""Normalizzazione del testo prima del classificatore (v3.0).

Il riconoscimento vocale (Whisper) produce frasi con punteggiatura, numeri in lettere e,
soprattutto, errori sistematici su nomi propri e app ("judy westcode" per "Visual Studio
Code", "post on timer" per "imposta un timer", "a nulla" per "annulla"). Qui si corregge
tutto quello che e' correggibile con regole deterministiche, PRIMA di spendere una chiamata
al modello: piu' il testo in ingresso e' pulito, meno il classificatore sbaglia."""
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

LEARNED_VOCABULARY_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "learned_vocabulary.json"

_UNITS = {
    "zero": 0, "uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6,
    "sette": 7, "otto": 8, "nove": 9, "dieci": 10, "undici": 11, "dodici": 12, "tredici": 13,
    "quattordici": 14, "quindici": 15, "sedici": 16, "diciassette": 17, "diciotto": 18,
    "diciannove": 19, "venti": 20, "trenta": 30, "quaranta": 40, "cinquanta": 50,
    "sessanta": 60, "settanta": 70, "ottanta": 80, "novanta": 90, "cento": 100, "mille": 1000,
}
_TENS_PREFIX = {
    "vent": 20, "trent": 30, "quarant": 40, "cinquant": 50, "sessant": 60, "settant": 70,
    "ottant": 80, "novant": 90,
}
_UNIT_SUFFIX = {
    "uno": 1, "due": 2, "tre": 3, "tré": 3, "quattro": 4, "cinque": 5, "sei": 6, "sette": 7,
    "otto": 8, "nove": 9,
}
# "sei" da solo e' quasi sempre il verbo essere ("sei sveglio?"), non il numero 6: viene
# convertito solo quando e' seguito da un'unita' di tempo/percentuale o preceduto da "alle".
_AMBIGUOUS_NUMBER_WORDS = {"sei", "una", "uno", "mille", "cento"}
_NUMBER_CONTEXT_AFTER = r"(?=\s+(?:minut\w*|second\w*|or[ae]\b|per\s*cento|%|giorn\w*|settiman\w*|volte|gradi|euro|dollar\w*|anni|chil\w*|kg|metri|km|facce|persone|e\s+(?:mezz\w*|un\s+quarto|tre\s+quarti)))"
_NUMBER_CONTEXT_BEFORE = r"(?<=\balle\s)"


def _compound_number(word: str) -> int | None:
    """'venticinque' -> 25, 'trentuno' -> 31, 'centocinquanta' -> 150, 'duecento' -> 200."""
    if word in _UNITS:
        return _UNITS[word]
    # centinaia: "duecento", "trecentocinquanta"
    for unit, value in (("due", 2), ("tre", 3), ("quattro", 4), ("cinque", 5), ("sei", 6), ("sette", 7), ("otto", 8), ("nove", 9)):
        if word.startswith(unit + "cento"):
            rest = word[len(unit) + 5:]
            if not rest:
                return value * 100
            sub = _compound_number(rest)
            return value * 100 + sub if sub is not None and sub < 100 else None
    if word.startswith("cento") and len(word) > 5:
        sub = _compound_number(word[5:])
        return 100 + sub if sub is not None and sub < 100 else None
    for prefix, tens in _TENS_PREFIX.items():
        if word.startswith(prefix):
            rest = word[len(prefix):]
            # "venti"+"cinque" -> "venticinque", "trenta"+"cinque" -> "trentacinque"; con uno/otto
            # la vocale cade: "ventuno", "trentotto".
            if rest in ("i", "a", ""):
                return tens if rest else None
            if rest[0] in "ia":
                rest = rest[1:]
            if rest in _UNIT_SUFFIX:
                return tens + _UNIT_SUFFIX[rest]
    return None


class TranscriptNormalizer:
    """Pulisce e corregge una trascrizione. Stateless a parte il vocabolario imparato
    (correzioni "sentito -> inteso" derivate dalle correzioni dell'utente, vedi
    LearningManager), che viene ricaricato quando cambia."""

    # Errori sistematici di Whisper sull'italiano parlato, osservati nel log di Jake.
    WHISPER_FIXES = [
        (re.compile(r"\ba\s+nulla\b"), "annulla"),
        (re.compile(r"\bpost\s*on\s+(?:il\s+|un\s+)?timer\b"), "imposta un timer"),
        (re.compile(r"^(?:udi|giudi|hapri|apre|aprì|april|opri|apr|apri mi|aprime|aprimi)\s+"), "apri "),
        (re.compile(r"^(?:(?:ehi|hey|ok|okay|ciao|senti)\s+)?(?:(?:gek|geek|jack|jake|giacomo|jek|jeic|jaké)[\s,.!]+)+"), ""),
        # Whisper mette virgole dopo il verbo ("Apri, Blender"): via.
        (re.compile(r"^([a-zàèéìòù]+),\s+"), r"\1 "),
        (re.compile(r"\bvisual\s*studio\s*cod\w*\b"), "visual studio code"),
        (re.compile(r"\b(?:vs\s*code|vscode|vi\s*es\s*code|visual\s*code)\b"), "visual studio code"),
        (re.compile(r"\bjudy\s+west\s*code\b"), "visual studio code"),
        (re.compile(r"\bblocco\s+not[ei]\b"), "blocco note"),
        (re.compile(r"\bwhats\s*app\b"), "whatsapp"),
        (re.compile(r"\byou\s*tube\b"), "youtube"),
        (re.compile(r"\bspoti\s*fy\b"), "spotify"),
        (re.compile(r"\bgoo+gle\b"), "google"),
        (re.compile(r"\bwi\s*-?\s*fi\b"), "wifi"),
        (re.compile(r"\bok\s+google\b"), ""),
        (re.compile(r"\bper\s+cento\b"), "per cento"),
        (re.compile(r"\bmezz'?\s*ora\b"), "30 minuti"),
        (re.compile(r"\bun\s+quarto\s+d'?\s*ora\b"), "15 minuti"),
        (re.compile(r"\btre\s+quarti\s+d'?\s*ora\b"), "45 minuti"),
        (re.compile(r"\bun'?\s*ora\s+e\s+mezz\w*\b"), "90 minuti"),
        (re.compile(r"\btra\s+un'?\s*ora\b"), "tra 60 minuti"),
        (re.compile(r"\bdi\s+un'?\s*ora\b"), "di 60 minuti"),
        (re.compile(r"\b(\d+)\s+ore\s+e\s+mezz\w*\b"), lambda m: f"{int(m.group(1)) * 60 + 30} minuti"),
        (re.compile(r"\b(\d+)\s+e\s+mezz\w*\b"), r"\1:30"),
        (re.compile(r"\balle\s+(\d{1,2})\s+e\s+(\d{1,2})\b"), r"alle \1:\2"),
        (re.compile(r"\balle\s+(\d{1,2})\s+e\s+un\s+quarto\b"), r"alle \1:15"),
        (re.compile(r"\balle\s+(\d{1,2})\s+e\s+tre\s+quarti\b"), r"alle \1:45"),
        (re.compile(r"\balle\s+(\d{1,2})[.,](\d{2})\b"), r"alle \1:\2"),
        (re.compile(r"\bore\s+(\d{1,2})[.,:](\d{2})\b"), r"ore \1:\2"),
        (re.compile(r"\s+"), " "),
    ]

    APP_VERBS = re.compile(
        r"\b(?:apri|aprimi|avvia|avviami|lancia|esegui|chiudi|termina|minimizza|massimizza|"
        r"passa a|vai su|torna su|porta in primo piano|mostrami|riduci a icona|ripristina)\b"
    )

    def __init__(self, app_names_provider=None, learned_vocabulary_path: Path = None):
        # callable che restituisce l'elenco dei nomi (leggibili) delle app installate: usato
        # per correggere per somiglianza i nomi storpiati dal riconoscimento vocale.
        self.app_names_provider = app_names_provider
        self.learned_vocabulary_path = Path(learned_vocabulary_path) if learned_vocabulary_path else LEARNED_VOCABULARY_PATH
        self._learned = self._load_learned()
        self._app_names_cache = None

    # ---- vocabolario imparato ----------------------------------------------------------

    def _load_learned(self) -> dict:
        try:
            if self.learned_vocabulary_path.is_file():
                data = json.loads(self.learned_vocabulary_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {str(k).lower(): str(v).lower() for k, v in data.items() if k and v}
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def learn_replacement(self, heard: str, meant: str) -> None:
        """Memorizza che quando il riconoscimento produce 'heard' l'utente intende 'meant'."""
        heard = (heard or "").strip().lower()
        meant = (meant or "").strip().lower()
        if not heard or not meant or heard == meant or len(heard) < 3:
            return
        self._learned[heard] = meant
        try:
            self.learned_vocabulary_path.parent.mkdir(parents=True, exist_ok=True)
            self.learned_vocabulary_path.write_text(
                json.dumps(self._learned, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def learned_replacements(self) -> dict:
        return dict(self._learned)

    # ---- normalizzazione ----------------------------------------------------------------

    def normalize(self, text: str) -> str:
        if not text:
            return ""
        result = text.strip().lower()
        result = result.replace("’", "'").replace("`", "'")
        result = re.sub(r"\s+", " ", result)
        # punteggiatura finale (Whisper chiude quasi ogni frase con un punto)
        result = re.sub(r"[.!?…]+$", "", result).strip()
        result = re.sub(r"^[,;:.!?\-–—\s]+", "", result)

        for pattern, replacement in self.WHISPER_FIXES:
            result = pattern.sub(replacement, result)
        result = result.strip()

        for heard, meant in self._learned.items():
            if heard in result:
                result = result.replace(heard, meant)

        result = self._numbers_to_digits(result)
        # Secondo passaggio: le regole sugli orari ("7 e mezza" -> "7:30") hanno bisogno delle
        # cifre, che al primo passaggio erano ancora parole ("sette e mezza").
        for pattern, replacement in self.WHISPER_FIXES:
            result = pattern.sub(replacement, result)
        result = self._fix_app_names(result)
        return re.sub(r"\s+", " ", result).strip()

    def _numbers_to_digits(self, text: str) -> str:
        def replace(match):
            word = match.group(0)
            if word in _AMBIGUOUS_NUMBER_WORDS:
                return word
            value = _compound_number(word)
            return str(value) if value is not None else word

        text = re.sub(r"\b[a-zàèéìòù]+\b", replace, text)
        # numeri ambigui solo in contesto chiaro: "alle sei" -> "alle 6", "sei minuti" -> "6 minuti"
        text = re.sub(_NUMBER_CONTEXT_BEFORE + r"(sei|una|uno)\b", lambda m: str(_UNITS[m.group(1)]), text)
        text = re.sub(r"\b(sei|uno|una)\b" + _NUMBER_CONTEXT_AFTER, lambda m: str(_UNITS[m.group(1)]), text)
        # "1 quarto" (da "un quarto" non gia' gestito), "un'ora" residui
        text = re.sub(r"\b(\d+)\s+mila\b", lambda m: str(int(m.group(1)) * 1000), text)
        text = re.sub(r"\b(\d+)\s*per\s*cento\b", r"\1 per cento", text)
        return text

    def _app_names(self) -> list[str]:
        if self._app_names_cache is not None:
            return self._app_names_cache
        names = []
        if self.app_names_provider is not None:
            try:
                names = [str(name).lower() for name in (self.app_names_provider() or []) if name]
            except Exception:
                names = []
        if not names:
            return []  # scoperta ancora in corso: riprova al prossimo comando, senza bloccare
        # Solo nomi "parlabili": scarta stringhe troppo corte o piene di simboli/versioni.
        cleaned = []
        for name in names:
            simplified = re.sub(r"\s*\(.*?\)|\s*\d+(\.\d+)*\s*$|\s*x64|\s*x86|\s*64-bit|\s*32-bit", "", name).strip()
            if 3 <= len(simplified) <= 40 and re.search(r"[a-z]", simplified):
                cleaned.append(simplified)
        self._app_names_cache = sorted(set(cleaned), key=len, reverse=True)
        return self._app_names_cache

    def invalidate_app_names(self) -> None:
        self._app_names_cache = None

    def _fix_app_names(self, text: str) -> str:
        """Se la frase contiene un verbo da app ("apri ...") e il resto assomiglia molto al nome
        di un'app installata, sostituisce con il nome corretto (es. 'apri blendr' -> 'apri blender')."""
        match = self.APP_VERBS.search(text)
        if not match:
            return text
        names = self._app_names()
        if not names:
            return text
        remainder = text[match.end():].strip()
        if not remainder or len(remainder) < 3:
            return text
        candidate_words = remainder.split()
        best_name, best_score, best_span = None, 0.0, None
        for width in range(min(4, len(candidate_words)), 0, -1):
            span_text = " ".join(candidate_words[:width])
            for name in names:
                if name == span_text:
                    return text  # gia' corretto
                if abs(len(name) - len(span_text)) > max(4, len(name) // 2):
                    continue
                score = SequenceMatcher(None, span_text, name).ratio()
                if score > best_score:
                    best_name, best_score, best_span = name, score, width
        if best_name is not None and best_score >= 0.82:
            fixed_remainder = best_name + (" " + " ".join(candidate_words[best_span:]) if best_span < len(candidate_words) else "")
            return text[:match.end()].rstrip() + " " + fixed_remainder
        return text
