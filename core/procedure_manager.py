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
        results = self.memory_manager.recall(key=name, category=self.CATEGORY, limit=1)
        if not results:
            return None
        steps_data = json.loads(results[0]["value"])
        return [RecordedStep.from_dict(step) for step in steps_data]

    def list_names(self) -> list[str]:
        results = self.memory_manager.recall(category=self.CATEGORY, limit=self.MAX_PROCEDURES)
        return [result["key"] for result in results]
