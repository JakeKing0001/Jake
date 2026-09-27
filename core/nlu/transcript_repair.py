"""Trascrizione corrotta o imperfetta ma recuperabile? (prova reale del 27/09/2026)

Conversazione reale, tutte trascritte da Whisper con confidenza fra 0.42 e 0.67:
- "gerizono" e "COSAEM, Procesora": davvero illeggibili, giusto chiedere di ripetere;
- "chiore sono": e' "che ore sono", con una parola fusa;
- "Che cosa e' un Prozessor?" (0.48): domanda chiarissima con una parola storpiata, rifiutata solo perche' 0.48 < 0.50.

Una soglia unica di confidenza non separa questi casi. Qui si guardano insieme:
- la confidenza di Whisper;
- quante parole sono note (esempi di Jake, parole comuni, conversazione recente) e quante andrebbero riparate;
- se una riparazione e' vicina e UNICA (italianizzazione "prozessor" -> "processore", fusione "chiore" -> "che ore");
- se la frase ha una cornice di domanda INTATTA ("che cos'e'", "come funziona", "spiegami"...);
- se la frase riparata e' un esempio esatto di un intent, e con quale rischio: la scorciatoia vale solo per intent
  a sola lettura. Per il resto nessuna correzione aggressiva: decide il router, e il PolicyEngine governa come sempre.

Nessuna riparazione verso parole sconosciute al lessico: un nome proprio o una parola nuova restano come sono."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

OK, REPAIRED, UNCLEAR, AMBIGUOUS = "ok", "repaired", "unclear", "ambiguous"

_WORD = re.compile(r"[a-zàèéìòóù]+", re.I)
_VOWEL_END = re.compile(r"[aeiouàèéìòóù]$")
_FOREIGN_LETTERS = re.compile(r"[kwyxj]")

# parole funzionali e verbi frequenti: senza di loro ogni frase avrebbe "parole sconosciute"
CORE_WORDS = set("""
a ad al alla alle allo agli ai anche ancora avere ce che chi ci come con cosa cos cosi da dal dalla dei del della delle
dello dentro di dove dopo e ed era ero gli ha hai hanno ho i il in io la le lei li lo loro lui ma me mi mia mie miei mio
molto ne nei nel nella no noi non o ogni per perche piu poi quale quali qual quando quanta quante quanti quanto quello
questa queste questi questo se sei si sia siamo sono su sua sue sul sulla suo suoi te ti tra tu tua tuo un una uno vi voi
fa fai fare fatto funziona funzionano spiegami spiega dimmi dammi sai puoi vorrei voglio serve significa cosa ora ore
oggi domani ieri adesso tempo bene grazie ciao si
è é perché più così
""".split())

# cornici di domanda: se sono all'inizio e intatte, la frase e' una domanda anche con una parola storpiata dopo
_QUESTION_FRAMES = (
    ("che", "cos", "è"), ("che", "cosa", "è"), ("che", "cosa", "e"), ("cos", "è"), ("cosa", "è"), ("cosa", "e"),
    ("che", "cosa", "sono"), ("cosa", "sono"), ("come", "funziona"), ("come", "funzionano"), ("come", "si"),
    ("come", "è"), ("spiegami",), ("perché",), ("perche",), ("chi", "è"), ("chi", "era"), ("quanto",), ("quanti",),
    ("dove", "si"), ("dove", "è"), ("quando", "è"), ("cosa", "significa"), ("che", "significa"),
)

# italianizzazioni tipiche di una parola trascritta "all'estera": una sostituzione alla volta (+ vocale finale)
_ITALIANIZE = (("z", "c"), ("z", "s"), ("z", "zz"), ("ck", "c"), ("k", "c"), ("k", "ch"), ("w", "v"), ("y", "i"),
               ("ph", "f"), ("th", "t"), ("j", "g"), ("j", "i"), ("sch", "sc"))


def words(text: str) -> list[str]:
    return [token.lower() for token in _WORD.findall(text or "")]


def italian_shaped(token: str) -> bool:
    return bool(_VOWEL_END.search(token)) and not _FOREIGN_LETTERS.search(token)


@dataclass
class Assessment:
    verdict: str
    text: str
    reason: str = ""
    options: list[str] = field(default_factory=list)


class TranscriptRepair:
    FLOOR = 0.35             # sotto: sempre ripetere, qualunque altro segnale
    SHORT_WORDS = 4
    SHORT_MIN = 0.62         # frase breve senza cornice ne' esempio: servono indizi forti (era la regola di prima)
    LONG_MIN = 0.5
    FRAMED_MIN = 0.4         # domanda con cornice intatta e al piu' una parola storta
    CLOSE = 0.84             # somiglianza minima per una correzione (difflib)
    TIE = 0.03               # due correzioni cosi' vicine sono ambigue

    def __init__(self, vocabulary=(), bigrams=()):
        self.lexicon = set(CORE_WORDS)
        self.lexicon.update(word for word in vocabulary if len(word) > 1)
        self.bigrams = {first + second: f"{first} {second}" for first, second in bigrams}

    @classmethod
    def from_examples(cls, examples, trusted=("builtin", "taught", "corrected")) -> "TranscriptRepair":
        vocabulary, bigrams = set(), set()
        for example in examples or []:
            if getattr(example, "source", "builtin") not in trusted:
                continue  # un esempio imparato da solo puo' essere proprio una trascrizione storta ("chiore sono")
            tokens = words(example.text)
            vocabulary.update(tokens)
            bigrams.update(zip(tokens, tokens[1:]))
        return cls(vocabulary, bigrams)

    def add_context(self, text: str) -> None:
        """Parole della conversazione recente: se ne hai appena parlato, e' plausibile che tu la stia ripetendo."""
        self.lexicon.update(token for token in words(text) if len(token) > 3)

    # ---- riparazione di una parola ----------------------------------------------------------------------
    def candidates(self, token: str) -> list[tuple[str, float]]:
        if len(token) <= 3:
            return []
        found: dict[str, float] = {}
        variants = {token}
        for source, target in _ITALIANIZE:
            if source in token:
                variants.add(token.replace(source, target))
        for variant in list(variants):
            if not _VOWEL_END.search(variant):
                variants.update(variant + vowel for vowel in "eoai")
        for variant in variants - {token}:
            if variant in self.lexicon:
                found[variant] = 1.0  # regola fonetica esplicita: la piu' affidabile
        if not found:
            for match in difflib.get_close_matches(token, [w for w in self.lexicon if w[0] == token[0]], n=3,
                                                   cutoff=self.CLOSE):
                found[match] = difflib.SequenceMatcher(None, token, match).ratio()
            # due parole fuse ("chiore" -> "che ore"): solo verso coppie viste negli esempi
            for joined, pair in self.bigrams.items():
                if joined[0] == token[0] and abs(len(joined) - len(token)) <= 2:
                    ratio = difflib.SequenceMatcher(None, token, joined).ratio()
                    if ratio >= self.CLOSE - 0.04:
                        found[pair] = max(found.get(pair, 0.0), ratio)
        return sorted(found.items(), key=lambda item: -item[1])

    def _frame(self, tokens: list[str]) -> bool:
        return any(tuple(tokens[:len(frame)]) == frame and len(tokens) > len(frame) for frame in _QUESTION_FRAMES)

    # ---- valutazione di una frase -------------------------------------------------------------------------
    def assess(self, text: str, confidence: float | None, find_exact=None, safe_intent=None) -> Assessment:
        """`find_exact(text) -> Example|None`, `safe_intent(intent) -> bool` (solo lettura)."""
        if confidence is None:
            return Assessment(OK, text, "testo senza confidenza (scritto)")
        tokens = words(text)
        if not tokens:
            return Assessment(UNCLEAR, text, "nessuna parola")
        if confidence < self.FLOOR:
            return Assessment(UNCLEAR, text, f"confidenza {confidence:.2f} sotto il minimo")

        repaired, safe_repaired, unknown, ambiguous = [], [], 0, []
        for token in tokens:
            if token in self.lexicon:
                repaired.append(token)
                safe_repaired.append(token)
                continue
            unknown += 1
            options = self.candidates(token)
            if len(options) > 1 and options[0][1] - options[1][1] < self.TIE:
                ambiguous.append((token, [option for option, _ in options[:2]]))
                repaired.append(token)
            else:
                repaired.append(options[0][0] if options else token)
            # dentro una domanda si corregge solo una parola dall'aspetto straniero con una regola fonetica esplicita
            # e unica ("prozessor" -> "processore"): una parola italiana nuova o un nome proprio restano com'erano
            unique_rule = len(options) == 1 and options[0][1] == 1.0
            safe_repaired.append(options[0][0] if unique_rule and not italian_shaped(token) else token)
        repaired_text = " ".join(repaired)

        if repaired != tokens and find_exact is not None and not ambiguous:
            example = find_exact(repaired_text)
            if example is not None and (safe_intent is None or safe_intent(example.intent)):
                return Assessment(REPAIRED, repaired_text, f"esempio esatto di {example.intent} dopo la correzione")

        framed = self._frame(tokens)  # cornice INTATTA: una cornice ottenuta riparando non conta
        if framed and unknown <= 1 and confidence >= self.FRAMED_MIN:
            if safe_repaired != tokens:
                fixed = text
                for old, new in zip(tokens, safe_repaired):
                    if old != new:  # nel testo originale, cosi' apostrofi e punteggiatura restano
                        fixed = re.sub(rf"\b{re.escape(old)}\b", new, fixed, flags=re.I)
                return Assessment(REPAIRED, fixed, "domanda con cornice chiara, parola corretta")
            return Assessment(OK, text, "domanda con cornice chiara")
        if ambiguous:
            token, options = ambiguous[0]
            return Assessment(AMBIGUOUS, text, f"'{token}' potrebbe essere {' o '.join(options)}", options)
        if len(tokens) <= self.SHORT_WORDS:
            needed = self.SHORT_MIN
        else:
            needed = self.LONG_MIN if unknown * 3 <= len(tokens) else self.SHORT_MIN
        if confidence < needed:
            return Assessment(UNCLEAR, text, f"confidenza {confidence:.2f} < {needed:.2f}, {unknown} parole ignote")
        return Assessment(OK, text, "trascrizione affidabile")
