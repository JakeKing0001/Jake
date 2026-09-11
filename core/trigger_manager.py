import json

from core.workflow_manager import WorkflowManager


class TriggerManager:
    """Salva e richiama i trigger che fanno partire un'automazione da sole (v3.0: Jake
    proattivo), senza che l'utente debba dire "esegui l'automazione X".

    Stesso schema di persistenza di WorkflowManager: un trigger e' un record JSON salvato
    nella memoria a lungo termine con una categoria dedicata, cosi' riusa REMEMBER/RECALL/
    FORGET invece di introdurre una tabella sqlite parallela."""

    CATEGORY = "trigger"

    def __init__(self, memory_manager, workflow_manager: WorkflowManager | None = None):
        self.memory_manager = memory_manager
        self.workflow_manager = workflow_manager or WorkflowManager(memory_manager)

    def save(self, name: str, workflow_name: str, trigger_type: str, spec: dict) -> None:
        record = {
            "workflow_name": workflow_name,
            "type": trigger_type,
            "spec": spec,
            "last_fired": None,
        }
        self.memory_manager.remember(name, json.dumps(record), category=self.CATEGORY)

    def workflow_exists(self, workflow_name: str) -> bool:
        return self.workflow_manager.load(workflow_name) is not None

    def mark_fired(self, name: str, when_iso: str) -> None:
        record = self._load_raw(name)
        if record is None:
            return
        record["last_fired"] = when_iso
        self.memory_manager.remember(name, json.dumps(record), category=self.CATEGORY)

    def _load_raw(self, name: str) -> dict | None:
        results = self.memory_manager.recall(key=name, category=self.CATEGORY, limit=1)
        if not results:
            return None
        return json.loads(results[0]["value"])

    # Non un limite di prodotto (l'utente non creerebbe mai centinaia di automazioni a mano):
    # solo un tetto di sicurezza contro una query sql senza fine. TriggerScheduler._fire() (core/
    # trigger_scheduler.py) itera list_all() a OGNI ciclo di controllo per decidere cosa far
    # scattare: un limite basso troncherebbe silenziosamente i trigger piu' vecchi/meno di
    # recente aggiornati (recall() ordina per importance/updated_at), che smetterebbero di
    # scattare mai piu' superata quella soglia - un limite di 50 era raggiungibile per davvero
    # con un uso normale nel tempo (ogni SET_TRIGGER e' un record permanente).
    MAX_TRIGGERS = 1000

    def list_all(self) -> list[dict]:
        results = self.memory_manager.recall(category=self.CATEGORY, limit=self.MAX_TRIGGERS)
        triggers = []
        for entry in results:
            record = json.loads(entry["value"])
            record["name"] = entry["key"]
            triggers.append(record)
        return triggers

    def delete(self, name: str) -> bool:
        return self.memory_manager.forget(name, category=self.CATEGORY)
