"""Risposte immediate alle frasi di cortesia (v3.0): saluti, ringraziamenti, "come stai",
"chi sei". Prima finivano in "Non so ancora fare questa cosa" (vedi 'grazie.' nel log).
Deterministiche e istantanee: nessuna chiamata al modello per dire "prego"."""
import random
import re
from datetime import datetime

from core.version import VERSION

_RULES = [
    ("thanks", re.compile(r"\b(grazie|ti ringrazio|thanks|thank you|graziee+)\b")),
    ("bye", re.compile(r"\b(a dopo|a presto|ci vediamo|arrivederci|buonanotte|notte|addio|ciao ciao|bye)\b")),
    ("greeting", re.compile(r"^(ciao|hey|ehi|salve|buongiorno|buon giorno|buonasera|buon pomeriggio|hola|yo)\b")),
    ("howareyou", re.compile(r"\b(come stai|come va|tutto bene|come te la passi|come ti senti)\b")),
    ("whoareyou", re.compile(r"\b(chi sei|come ti chiami|cosa sei|sei un robot|sei un'?intelligenza)\b")),
    ("presence", re.compile(r"\b(ci sei|mi senti|sei sveglio|sei li|sei lì|sei acceso|sei online|mi ascolti)\b|^(?:ehi |hey |ciao )?jake\??$")),
    ("praise", re.compile(r"\b(bravo|bravissimo|ottimo|perfetto|grande|fantastico|sei un genio|sei forte|ben fatto|ottimo lavoro|mitico|top)\b")),
    ("love", re.compile(r"\b(ti voglio bene|ti amo|sei il migliore|mi piaci)\b")),
    ("nothing", re.compile(r"\b(no niente|niente|lascia stare|lascia perdere|nulla|non importa|fa niente)\b|^(?:no|nope|ok|okay|va bene|vabbe|vabbè|d'accordo|capito|si|sì)$")),
    ("sorry", re.compile(r"\b(scusa|scusami|pardon|mi dispiace)\b")),
]

_REPLIES = {
    "thanks": ["Prego.", "Di nulla.", "Figurati.", "Quando vuoi.", "Sempre a disposizione."],
    "bye": ["A dopo.", "A presto.", "Ci sono, quando ti servo.", "Buona giornata."],
    "greeting": None,  # dipende dall'ora
    "howareyou": ["Tutto in ordine, sistemi operativi al cento per cento. Tu?", "Alla grande. Dimmi cosa serve.", "Bene, pronto a lavorare."],
    "whoareyou": ["Sono Jake, il tuo assistente. Vivo su questo computer e faccio le cose al posto tuo: apro programmi, cerco, scrivo, ricordo, e imparo comandi nuovi.", f"Jake. Assistente personale, versione {VERSION}. Dimmi cosa fare."],
    "presence": ["Ci sono.", "Ti ascolto.", "Sempre.", "Eccomi."],
    "praise": ["Grazie, faccio del mio meglio.", "Lo so.", "Troppo gentile.", "Fa parte del lavoro."],
    "love": ["Anch'io, a modo mio.", "Sei il mio utente preferito. L'unico, in effetti."],
    "nothing": ["Ok.", "Va bene.", "Come vuoi.", "Ci sono, quando ti serve."],
    "sorry": ["Nessun problema.", "Tranquillo.", "Figurati."],
}


def _greeting_reply() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 13:
        return random.choice(["Buongiorno. Dimmi pure.", "Buongiorno, come posso aiutarti?", "Ciao. Pronto quando vuoi."])
    if 13 <= hour < 18:
        return random.choice(["Buon pomeriggio. Dimmi pure.", "Ciao, che si fa?", "Eccomi."])
    return random.choice(["Buonasera. Dimmi pure.", "Ciao. Cosa ti serve?", "Eccomi, dimmi."])


def classify(text: str) -> str | None:
    lowered = (text or "").strip().lower()
    if not lowered:
        return None
    for category, pattern in _RULES:
        if pattern.search(lowered):
            return category
    return None


def reply(text: str) -> str | None:
    """Risposta pronta per una frase di cortesia, o None se non e' chitchat riconosciuto."""
    category = classify(text)
    if category is None:
        return None
    if category == "greeting":
        return _greeting_reply()
    # _REPLIES["greeting"] e' l'unica voce a None ed e' gia' esclusa dal ramo sopra: le altre
    # sono sempre liste vere, ma l'assert lo rende esplicito invece di un cast silenzioso.
    replies = _REPLIES[category]
    assert replies is not None
    return random.choice(replies)
