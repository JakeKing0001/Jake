"""Quando la memoria personale serve davvero a una risposta (prova reale del 27/09/2026).

"Cosa e' un processore?" ha avuto una risposta giusta piu' "(Dai miei ricordi: riassunto conversazione del
2026-09-27T07:26...)": il riassunto di una conversazione precedente conteneva la parola "processore", e una parola in
comune bastava per scegliere un ricordo. Una domanda di conoscenza generale non ha bisogno della memoria personale.

Qui il tipo di domanda:
- GENERIC: una domanda di conoscenza ("cos'e' un processore", "come funziona TCP", "spiegami le reti neurali") senza
  nessun riferimento a se' -> nessun ricordo, salvo uno nominato esplicitamente (chiave citata: "cos'e' NEST?");
- PERSONAL: parla dell'utente o di cio' che ha detto ("che linguaggi sto studiando?", "che cuffie avevo?");
- MIXED: conoscenza + riferimento personale ("spiegami le reti neurali considerando quello che sto studiando");
- OTHER: il resto (es. "quando e' il compleanno di Giulia?"): recupero normale.
E, a parte, se la domanda si riferisce a conversazioni passate: solo allora un riassunto di conversazione e' pertinente.
"""
from __future__ import annotations

import re

GENERIC, PERSONAL, MIXED, OTHER = "generic", "personal", "mixed", "other"

_PERSONAL = re.compile(
    r"\b(mio|mia|miei|mie|io|ho|avevo|avevi|sto|stavo|ero|faccio|facevo|uso|usavo|studio|lavoro|mi|me|"
    r"ti ho|ti avevo|ricordi|ricordami|nostro|nostra|nostri|nostre|abbiamo|avevamo)\b", re.I)
_KNOWLEDGE = re.compile(
    r"^(che cos|che cosa|cos'|cosa (e|è|sono|significa)|come (funziona|funzionano|si fa|nasce|nascono)|spiegami|"
    r"perch[eé]|chi (e|è|era|fu|ha)|qual (e|è)|quale (e|è)|quanto (e|è|dista|pesa)|definisci|dimmi cos|"
    r"in cosa consiste|a cosa serve)", re.I)
_CONVERSATION = re.compile(
    r"(abbiamo parlato|ne abbiamo|ci siamo detti|ti ho detto|ti avevo detto|avevamo detto|mi hai detto|"
    r"la volta scorsa|l'altra volta|l'ultima volta|di cosa parlavamo|nostra conversazione|\bieri\b|"
    r"settimana scorsa|stamattina|prima (mi|ti) )", re.I)


def query_kind(question: str) -> str:
    text = (question or "").strip().lower().replace("’", "'")
    personal = bool(_PERSONAL.search(text))
    knowledge = bool(_KNOWLEDGE.search(text))
    if knowledge and personal:
        return MIXED
    if knowledge:
        return GENERIC
    return PERSONAL if personal else OTHER


def refers_to_conversation(question: str) -> bool:
    return bool(_CONVERSATION.search((question or "").lower().replace("’", "'")))
