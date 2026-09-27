"""F5.7 nel runtime: la privacy dashboard sapeva esportare i ricordi e fissarli, ma nessun comando lo faceva.

- EXPORT_MEMORIES ("esporta i miei ricordi"): una copia leggibile (Markdown) e una machine-readable (JSON) in una
  cartella dell'utente. I ricordi segreti restano fuori (li dice quanti), come nell'export della dashboard.
- PIN_MEMORY ("fissa il ricordo X", "puoi dimenticarlo" -> pinned=false): un ricordo fissato non decade nel ranking
  (F5.4.6) e cambiarlo chiede conferma (F5.4.3)."""
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
        records = self.dashboard.search(key, limit=3)
        exact = [r for r in records if r.key.lower() == key.lower()]
        matches = exact or records
        if not matches:
            return SkillResult(success=False, data={"key": key}, error="NOT_FOUND")
        if len(matches) > 1:
            return SkillResult(success=False, data={"candidates": [r.key for r in matches]}, error="AMBIGUOUS")
        record = matches[0]
        self.dashboard.pin(record.key, record.category, pinned=pinned)
        return SkillResult(success=True, data={"key": record.key, "pinned": pinned})
