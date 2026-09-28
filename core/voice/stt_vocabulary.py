"""Vocabolario del prompt iniziale di Whisper, generato da cio' che Jake sa fare davvero.

Prova reale del 27/09/2026: il prompt era "Jake, apri, chiudi, ..." seguito da 80 nomi di app del menu Start in
ordine di scoperta - "Administrative Tools, BlueStacks Services, Nahimic Companion, Uninstall OP Auto Clicker,
IDLE (Python 3.10 64-bit), ...". Whisper tratta il prompt come il testo che precede la frase: un elenco quasi tutto
inglese lo spingeva verso forme straniere ("Prozessor", "ERIK", "Horizon") proprio su frasi italiane.

Qui il prompt diventa italiano parlato: una frase d'esempio per ciascuno degli intent piu' comuni (dagli esempi curati
e da quelli insegnati o corretti dall'utente - MAI quelli imparati da soli, che possono essere trascrizioni storte come
"chiore sono"), piu' pochi nomi di app pronunciabili (niente disinstallatori, manuali, servizi, versioni)."""
from __future__ import annotations

import re
from collections import Counter

# fonti affidabili: curate o confermate dall'utente
_TRUSTED_SOURCES = {"builtin", "taught", "corrected"}
_QUESTION_START = re.compile(r"^(che|cosa|cos'|come|quanto|quanti|quale|qual|dove|quando|perch|chi)\b", re.I)
_APP_NOISE = re.compile(
    r"uninstall|disinstalla|help|manual|docs|guide|tools|services|driver|settings|console|management|recorder|"
    r"legacy|runtime|updater|installer|\(|\)|\.exe|\d+\.\d+|^run$|^wsl", re.I)


def spoken_app_names(names, limit: int = 10, mentioned: str = "") -> list[str]:
    """Nomi di app che una persona pronuncia: brevi, senza versioni ne' voci tecniche del menu Start. Prima quelle
    nominate in `mentioned` (il testo degli esempi: le app di cui si parla davvero), poi l'ordine di scoperta."""
    mentioned = mentioned.lower()
    names = [str(name).strip() for name in names or []]
    names.sort(key=lambda name: 0 if name and re.search(r"\b" + re.escape(name.lower()) + r"\b", mentioned) else 1)
    chosen, seen = [], set()
    for name in names:
        name = str(name).strip()
        key = name.lower()
        if not name or len(name) > 24 or key in seen or _APP_NOISE.search(name):
            continue
        seen.add(key)
        chosen.append(name)
        if len(chosen) >= limit:
            break
    return chosen


def _sentence(text: str) -> str:
    text = text.strip().rstrip(".?!")
    if not text:
        return ""
    end = "?" if _QUESTION_START.match(text) else "."
    return text[0].upper() + text[1:] + end


def intent_phrases(examples, limit: int = 16) -> list[str]:
    """Una frase tipica per ciascuno dei `limit` intent con piu' esempi affidabili (proxy dell'uso reale): di 2-6
    parole, la piu' vicina alla lunghezza mediana, cosi' non e' ne' un comando monco ne' un paragrafo."""
    by_intent: dict[str, list[str]] = {}
    for example in examples or []:
        if getattr(example, "source", "builtin") not in _TRUSTED_SOURCES:
            continue
        text = (getattr(example, "text", "") or "").strip()
        if 2 <= len(text.split()) <= 6 and not any(ch.isdigit() for ch in text):
            by_intent.setdefault(example.intent, []).append(text)
    counts = Counter({intent: len(texts) for intent, texts in by_intent.items()})
    phrases = []
    for intent, _count in counts.most_common(limit):
        texts = sorted(by_intent[intent], key=len)
        questions = [text for text in texts if _QUESTION_START.match(text)]
        texts = questions if len(questions) * 3 >= len(texts) else texts  # un intent di domande resta una domanda
        phrases.append(_sentence(texts[len(texts) // 2]))
    return [phrase for phrase in phrases if phrase]
