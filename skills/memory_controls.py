"""F5.7 nel runtime: la privacy dashboard sapeva esportare i ricordi e fissarli, ma nessun comando lo faceva.

- EXPORT_MEMORIES ("esporta i miei ricordi"): una copia leggibile (Markdown) e una machine-readable (JSON) in una
  cartella dell'utente. I ricordi segreti restano fuori (li dice quanti), come nell'export della dashboard.
- PIN_MEMORY ("fissa il ricordo X", "puoi dimenticarlo" -> pinned=false): un ricordo fissato non decade nel ranking
  (F5.4.6) e cambiarlo chiede conferma (F5.4.3).
- SET_MEMORY_SENSITIVITY ("segna come segreto il pin del bancomat"): un ricordo segreto non entra mai nel contesto
  automatico delle risposte ne' negli export (F5.5/F5.7) - prima si poteva marcare solo dal codice.
- LIST_MEMORIES ("quali ricordi segreti hai?", "cosa sai di me"): quali ricordi ci sono, per tipo e sensibilita', con
  le sole chiavi - mai i valori, che restano a RECALL."""
from datetime import datetime
from pathlib import Path

from core.skill_result import SkillResult


class ExportMemoriesSkill:
    metadata = {
        "intent": "EXPORT_MEMORIES",
        "description": (
            "Esporta tutti i ricordi di Jake in un file leggibile (Markdown) e in uno JSON nella cartella Documenti\\Jake. "
            "Per 'esporta i miei ricordi', 'fammi una copia di quello che sai di me'. I ricordi segreti restano esclusi."
        ),
        "parameters": {},
    }

    def __init__(self, dashboard, export_dir: Path | None = None, clock=datetime.now):
        self.dashboard = dashboard
        self.export_dir = Path(export_dir) if export_dir else Path.home() / "Documents" / "Jake"
        self.clock = clock

    def execute(self, parameters: dict = None):
        try:
            self.export_dir.mkdir(parents=True, exist_ok=True)
            stamp = self.clock().strftime("%Y%m%d-%H%M%S")
            markdown = self.dashboard.export_markdown(self.export_dir / f"jake-ricordi-{stamp}.md")
            data = self.dashboard.export_json(self.export_dir / f"jake-ricordi-{stamp}.json")
        except OSError as exc:
            return SkillResult(success=False, data={"path": str(self.export_dir), "reason": exc.strerror or str(exc)},
                               error="EXPORT_FAILED")
        payload = self.dashboard.export_payload()
        return SkillResult(success=True, data={
            "markdown": str(markdown), "json": str(data), "count": len(payload["records"]),
            "excluded_secret": payload["excluded_secret"],
        })


def _one_record(dashboard, key: str):
    """Il ricordo indicato con le parole dell'utente: (record, None) o (None, SkillResult di errore)."""
    records = dashboard.search(key, limit=3)
    exact = [r for r in records if r.key.lower() == key.lower()]
    matches = exact or records
    if not matches:
        return None, SkillResult(success=False, data={"key": key}, error="NOT_FOUND")
    if len(matches) > 1:
        return None, SkillResult(success=False, data={"candidates": [r.key for r in matches]}, error="AMBIGUOUS")
    return matches[0], None


class PinMemorySkill:
    metadata = {
        "intent": "PIN_MEMORY",
        "description": (
            "Fissa un ricordo importante: non perde peso col tempo e per cambiarlo Jake chiede conferma. Per 'fissa il "
            "ricordo X', 'non dimenticare mai X'; con pinned=false per 'X non e' piu' importante, puoi lasciarlo andare'."
        ),
        "parameters": {
            "key": {"type": "string", "required": True, "description": "Il ricordo, con le parole dell'utente."},
            "pinned": {"type": "boolean", "required": False, "description": "false per togliere il fissaggio."},
        },
    }

    def __init__(self, dashboard):
        self.dashboard = dashboard

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        key = str(parameters.get("key") or "").strip()
        if not key:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        pinned = parameters.get("pinned") is not False
        record, problem = _one_record(self.dashboard, key)
        if problem is not None:
            return problem
        self.dashboard.pin(record.key, record.category, pinned=pinned)
        return SkillResult(success=True, data={"key": record.key, "pinned": pinned})


SENSITIVITY_WORDS = {"public": "pubblico", "personal": "personale", "sensitive": "sensibile", "secret": "segreto"}


class SetMemorySensitivitySkill:
    metadata = {
        "intent": "SET_MEMORY_SENSITIVITY",
        "description": (
            "Cambia quanto e' riservato un ricordo: 'secret' (mai nel contesto delle risposte ne' negli export), "
            "'sensitive', 'personal' o 'public'. Per 'segna come segreto X', 'X e' un'informazione riservata', "
            "'X non e' piu' segreto'."
        ),
        "parameters": {
            "key": {"type": "string", "required": True, "description": "Il ricordo, con le parole dell'utente."},
            "sensitivity": {"type": "string", "required": True,
                            "description": "Uno tra secret, sensitive, personal, public."},
        },
    }

    def __init__(self, dashboard):
        self.dashboard = dashboard

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        key = str(parameters.get("key") or "").strip()
        sensitivity = str(parameters.get("sensitivity") or "").strip().lower()
        if not key or sensitivity not in SENSITIVITY_WORDS:
            return SkillResult(success=False, data={"levels": list(SENSITIVITY_WORDS)}, error="MISSING_PARAMETERS")
        record, problem = _one_record(self.dashboard, key)
        if problem is not None:
            return problem
        self.dashboard.edit(record.key, record.category, sensitivity=sensitivity)
        return SkillResult(success=True, data={"key": record.key, "sensitivity": sensitivity})


class ListMemoriesSkill:
    metadata = {
        "intent": "LIST_MEMORIES",
        "description": (
            "Elenca quali ricordi Jake ha (solo i nomi, mai i contenuti), anche per sensibilita' (secret, sensitive, "
            "personal, public) o tipo (entity = fatti e preferenze, episode = conversazioni ed eventi, procedure = "
            "automazioni). Per 'cosa sai di me', 'quali ricordi segreti hai', 'che automazioni ricordi'."
        ),
        "parameters": {
            "sensitivity": {"type": "string", "required": False, "description": "secret, sensitive, personal o public."},
            "kind": {"type": "string", "required": False, "description": "entity, episode o procedure."},
        },
    }

    MAX_KEYS = 12

    def __init__(self, dashboard):
        self.dashboard = dashboard

    def execute(self, parameters: dict = None):
        from core.memory_schema import MEMORY_KINDS, kind_of

        parameters = parameters or {}
        sensitivity = str(parameters.get("sensitivity") or "").strip().lower() or None
        kind = str(parameters.get("kind") or "").strip().lower() or None
        if (sensitivity and sensitivity not in SENSITIVITY_WORDS) or (kind and kind not in MEMORY_KINDS):
            return SkillResult(success=False, data={}, error="INVALID_PARAMETERS")
        records = [r for r in self.dashboard.search(sensitivity=sensitivity, limit=500)
                   if kind is None or kind_of(r.category) == kind]
        return SkillResult(success=True, data={
            "count": len(records), "keys": [r.key for r in records[: self.MAX_KEYS]],
            "sensitivity": sensitivity, "kind": kind,
        })
