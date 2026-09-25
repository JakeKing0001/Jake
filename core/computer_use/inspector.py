"""F3.3.6 — inspector del selettore: perche' Jake ha scelto (o non ha potuto scegliere) un elemento.

Lavora sull'albero UI Automation della SOLA finestra bersaglio letto tramite `TreeCache` (F3.2.2):
descrizioni immutabili (`ElementInfo`), nessun riferimento COM vivo trattenuto, rilettura a scadenza
o dopo un'azione. Per ogni elemento calcola quanto corrisponde al selettore e perche':

- `chosen`: l'unico elemento che soddisfa TUTTI i criteri (stessa semantica di SelectorEngine);
  None se nessuno o piu' di uno (l'ambiguita' non viene mai risolta a caso);
- `alternatives`: i candidati piu' vicini con punteggio e motivo (nome simile, ruolo diverso...).

Usato da ComputerAgent per rendere visibile la diagnosi di NOT_FOUND/AMBIGUOUS_MATCH, e da
`python -m tools.selector_inspector` a mano."""
from __future__ import annotations

import difflib
from dataclasses import dataclass

from core.computer_use.selector import ElementSelector
from core.computer_use.ui_automation_adapter import ElementInfo


@dataclass(frozen=True)
class Candidate:
    name: str
    control_type: str
    automation_id: str
    score: float
    reason: str


@dataclass(frozen=True)
class InspectionReport:
    chosen: Candidate | None
    alternatives: tuple[Candidate, ...]
    verdict: str  # "unique" | "ambiguous" | "no_match"
    reason: str

    def to_dict(self) -> dict:
        def c(candidate):
            return None if candidate is None else candidate.__dict__.copy()
        return {"verdict": self.verdict, "reason": self.reason, "chosen": c(self.chosen),
                "alternatives": [c(a) for a in self.alternatives]}


def _walk(info: ElementInfo | None):
    if info is None:
        return
    yield info
    for child in info.children:
        yield from _walk(child)


def _score(info: ElementInfo, selector: ElementSelector) -> tuple[float, bool, str]:
    parts, reasons, total, matched_all = [], [], 0, True
    if selector.name is not None:
        total += 1
        similarity = difflib.SequenceMatcher(None, selector.name.lower(), (info.name or "").lower()).ratio()
        if info.name == selector.name:
            parts.append(1.0)
            reasons.append("nome identico")
        else:
            matched_all = False
            parts.append(similarity * 0.8)
            reasons.append(f"nome diverso ({info.name!r}, somiglianza {similarity:.2f})")
    if selector.control_type is not None:
        total += 1
        if info.control_type == selector.control_type:
            parts.append(1.0)
            reasons.append("ruolo identico")
        else:
            matched_all = False
            parts.append(0.0)
            reasons.append(f"ruolo {info.control_type} invece di {selector.control_type}")
    if selector.automation_id is not None:
        total += 1
        if info.automation_id == selector.automation_id:
            parts.append(1.0)
            reasons.append("automation id identico")
        else:
            matched_all = False
            parts.append(0.0)
            reasons.append("automation id diverso")
    if not info.enabled:
        reasons.append("disabilitato")
    score = sum(parts) / total if total else 0.0
    return round(score, 3), matched_all, ", ".join(reasons)


def inspect(tree: ElementInfo | None, selector: ElementSelector, limit: int = 5) -> InspectionReport:
    scored = []
    for info in _walk(tree):
        score, full, reason = _score(info, selector)
        scored.append((full, score, Candidate(info.name, info.control_type, info.automation_id, score, reason)))
    exact = [candidate for full, _score_, candidate in scored if full]
    partial = sorted((candidate for full, _s, candidate in scored if not full and candidate.score > 0),
                     key=lambda c: -c.score)[:limit]
    if len(exact) == 1:
        return InspectionReport(exact[0], tuple(partial), "unique", "l'unico elemento che soddisfa tutti i criteri")
    if len(exact) > 1:
        return InspectionReport(None, tuple(exact[:limit]), "ambiguous",
                                f"{len(exact)} elementi soddisfano tutti i criteri: serve un criterio in piu' (automation id)")
    best = f"; il piu' vicino: {partial[0].name!r} ({partial[0].reason})" if partial else ""
    return InspectionReport(None, tuple(partial), "no_match", "nessun elemento soddisfa tutti i criteri" + best)
