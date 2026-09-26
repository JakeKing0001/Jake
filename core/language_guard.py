"""Coerenza della lingua delle risposte generate (gate hardware F2, 26/09/2026).

Qwen 2.5, anche con "Rispondi in italiano" nel prompt di sistema, puo' scivolare nel cinese a meta' di
una risposta lunga ("storia di Internet"). Nessuna traduzione a posteriori: si riconosce lo script
estraneo (CJK, kana, hangul) e si tiene la parte italiana fino all'ultima frase completa; chi chiama
decide se ritentare una volta. Se l'utente ha chiesto quella lingua (o una traduzione), nulla cambia.
"""
from __future__ import annotations

import re

_FOREIGN_SCRIPT = re.compile(
    "[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿！-｠]"
)
_OTHER_LANGUAGE_REQUEST = re.compile(
    r"\b(?:cinese|mandarino|giapponese|coreano|chinese|mandarin|japanese|korean|kanji|hiragana|katakana|hangul)\b"
    r"|\btradu\w*|\btranslat\w*",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")


def other_language_requested(question: str) -> bool:
    return bool(_OTHER_LANGUAGE_REQUEST.search(question or ""))


def keep_reply_language(answer: str, question: str) -> tuple[str, bool]:
    """(testo da usare, deriva trovata). Con deriva, il testo e' il prefisso nella lingua giusta
    troncato all'ultima frase completa (puo' essere vuoto)."""
    if not answer or other_language_requested(question):
        return answer, False
    match = _FOREIGN_SCRIPT.search(answer)
    if match is None:
        return answer, False
    prefix = answer[: match.start()]
    ends = list(_SENTENCE_END.finditer(prefix))
    kept = prefix[: ends[-1].end()] if ends else ""
    return kept.strip(), True
