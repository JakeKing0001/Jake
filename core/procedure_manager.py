"""Salva e richiama procedure semantiche (F3.8.5, prima fetta - "salvare... selector", la
persistenza a lungo termine mai affrontata finora, vedi ROADMAP_EXECUTION.md sezione F3.8). Una
procedura e' semplicemente una LISTA di `RecordedStep` (F3.8.1, `core/computer_use/procedure.py`)
salvata con un nome - esattamente lo stesso concetto di un workflow (`core/workflow_manager.py`,
"sequenze di passi con nome"), qui applicato a passi di COMPUTER USE (click/type su un
ElementSelector) invece che a passi di SKILL (intent/parametri).

Stesso principio di `WorkflowManager`, deliberatamente non un modulo diverso inventato da zero:
riusa la memoria a lungo termine gia' costruita (`core/memory_manager.py`, una categoria dedicata,
`CATEGORY = "computer_procedure"`) invece di un file per nome su disco. Questo evita per
COSTRUZIONE qualunque rischio di path traversal (un "nome" scelto da un chiamante/dall'utente
finisce come CHIAVE in una riga di database, mai come componente di un percorso filesystem) -
`WorkflowManager` non ha mai avuto bisogno di sanitizzare i nomi per lo stesso motivo, ed e'
esattamente la ragione per cui questo modulo lo imita invece di introdurre un formato di
salvataggio nuovo (JSON su disco, un file per procedura) che avrebbe dovuto reinventare quella
stessa protezione da zero."""
import json

from core.computer_use.procedure import RecordedStep
from core.computer_use.procedure_lifecycle import Procedure, approve


class ProcedureManager:
    """Analogo di `WorkflowManager` per le procedure di F3.8 - vedi il docstring del modulo."""

    CATEGORY = "computer_procedure"

    # Stesso ragionamento di WorkflowManager.MAX_WORKFLOWS/TriggerManager.MAX_TRIGGERS: non un
    # limite di prodotto, solo un tetto di sicurezza - un limite basso troncherebbe silenziosamente
    # le procedure piu' vecchie/meno di recente aggiornate da list_names() superata quella soglia.
    MAX_PROCEDURES = 1000

    def __init__(self, memory_manager):
        self.memory_manager = memory_manager

    def save(self, name: str, steps: list[RecordedStep]) -> None:
        """Upsert su (name, categoria) - la STESSA garanzia gia' offerta da `MemoryManager.
        remember()` per i workflow: salvare due volte con lo stesso nome sostituisce, non
        duplica."""
        steps_data = [step.to_dict() for step in steps]
        self.memory_manager.remember(name, json.dumps(steps_data), category=self.CATEGORY)

    def load(self, name: str) -> list[RecordedStep] | None:
        """`None` (mai una lista vuota indovinata) se nessuna procedura con questo nome esiste -
        la STESSA distinzione "onesto None" gia' seguita da `WorkflowManager.load()`: una
        procedura VUOTA salvata davvero (`steps=[]`) e nessuna procedura salvata affatto sono due
        fatti diversi, non lo stesso caso."""
        procedure = self.load_procedure(name)
        return None if procedure is None else list(procedure.steps)

    def save_procedure(self, procedure: Procedure) -> None:
        """F3.8.5: salva la procedura completa (versione, app, predefiniti, approvazione, stato)."""
        self.memory_manager.remember(procedure.name, json.dumps(procedure.to_dict(), ensure_ascii=False), category=self.CATEGORY)

    def load_procedure(self, name: str) -> Procedure | None:
        """La procedura completa; una salvata nel formato storico (solo lista di passi) viene letta
        come versione 1 approvata al rischio che i suoi passi dichiarano."""
        results = self.memory_manager.recall(key=name, category=self.CATEGORY, limit=1)
        if not results:
            return None
        return Procedure.from_dict(json.loads(results[0]["value"]), name=name)

    def create(self, name: str, steps: list[RecordedStep], *, defaults: dict[str, str] | None = None,
               app_process: str | None = None, app_version: str | None = None) -> Procedure:
        """Una procedura nuova, approvata dall'utente che la salva (versione 1). Sovrascrivendo un nome
        esistente la versione sale e l'approvazione precedente resta: se i passi nuovi chiedono di piu',
        la prossima esecuzione chiedera' una nuova approvazione (F3.8.7)."""
        previous = self.load_procedure(name)
        procedure = Procedure(
            name=name, steps=tuple(steps), defaults=tuple(sorted((defaults or {}).items())),
            app_process=app_process, app_version=app_version, version=0,
        )
        if previous is None:
            procedure = approve(procedure)
        else:
            from dataclasses import replace

            procedure = replace(procedure, version=previous.version + 1, approved_risk=previous.approved_risk,
                                approved_capabilities=previous.approved_capabilities)
        self.save_procedure(procedure)
        return procedure

    def list_names(self) -> list[str]:
        results = self.memory_manager.recall(category=self.CATEGORY, limit=self.MAX_PROCEDURES)
        return [result["key"] for result in results]
