"""Metriche pure per i benchmark voce (F2.1.3/F2.1.5): nessun audio, nessun modello, nessun
hardware - solo aritmetica su risultati gia' ottenuti, cosi' ogni formula si prova con numeri
scritti a mano nei test invece di fidarsi di una corsa dal vivo."""
from __future__ import annotations

import os
import platform
import re


def confusion(truth: list[bool], predicted: list[bool]) -> dict:
    """Matrice di confusione frame per frame. false_accept_rate = frame di non-parlato dichiarati
    parlato / tutti i frame di non-parlato; false_reject_rate = parlato scartato / tutto il
    parlato. None (non 0.0) quando il denominatore e' vuoto: "nessun dato" non e' "nessun errore"."""
    if len(truth) != len(predicted):
        raise ValueError(f"lunghezze diverse: {len(truth)} etichette vs {len(predicted)} predizioni")
    tp = sum(1 for t, p in zip(truth, predicted, strict=True) if t and p)
    fn = sum(1 for t, p in zip(truth, predicted, strict=True) if t and not p)
    fp = sum(1 for t, p in zip(truth, predicted, strict=True) if not t and p)
    tn = sum(1 for t, p in zip(truth, predicted, strict=True) if not t and not p)
    return _with_rates(tp, fp, fn, tn)


def merge_confusions(items: list[dict]) -> dict:
    """Somma le matrici di piu' clip e ricalcola i tassi sul totale (mai la media dei tassi: una
    clip di 2 frame peserebbe quanto una di 2000)."""
    return _with_rates(
        sum(i["tp"] for i in items), sum(i["fp"] for i in items),
        sum(i["fn"] for i in items), sum(i["tn"] for i in items),
    )


def _with_rates(tp: int, fp: int, fn: int, tn: int) -> dict:
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "false_accept_rate": _ratio(fp, fp + tn),
        "false_reject_rate": _ratio(fn, fn + tp),
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


_WORD = re.compile(r"[\w']+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def word_error_rate(reference: str, hypothesis: str, normalize_numbers: bool = False) -> float | None:
    """WER = (sostituzioni + inserzioni + cancellazioni) / parole del riferimento, dopo aver
    ignorato maiuscole e punteggiatura. None se il riferimento non ha parole (indefinito).
    `normalize_numbers` porta in cifre i numeri scritti in lettere PRIMA del confronto, cosi' "dieci"
    e "10" non contano come errore: Whisper scrive le cifre, chi detta la frase di riferimento le
    lettere, e quell'errore non e' di riconoscimento (F2.6.5)."""
    if normalize_numbers:
        from core.voice.language_normalizer import numbers_to_digits

        reference, hypothesis = numbers_to_digits(reference), numbers_to_digits(hypothesis)
    ref, hyp = _tokens(reference), _tokens(hypothesis)
    if not ref:
        return None
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i]
        for j, hyp_word in enumerate(hyp, start=1):
            current.append(min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + (ref_word != hyp_word)))
        previous = current
    return round(previous[-1] / len(ref), 4)


def hardware_profile(cuda: bool | None = None) -> dict:
    """Descrive la macchina che ha prodotto un numero (F2.1.5): senza questo un p95 non si puo'
    confrontare con niente. `profile` ("cpu"/"gpu") separa le baseline: un risultato su CPU non
    deve mai essere confrontato con uno su GPU. cuda=None interroga ctranslate2."""
    if cuda is None:
        try:
            from core.voice.stt_provider import cuda_available
            cuda = cuda_available()
        except Exception:
            cuda = False
    return {
        "profile": "gpu" if cuda else "cpu",
        "cpu": platform.processor() or platform.machine(),
        "logical_cores": os.cpu_count(),
        "python": platform.python_version(),
        "os": platform.platform(),
    }
