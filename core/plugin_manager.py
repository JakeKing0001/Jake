"""Skill che vengono da un file di plugin (Skill Forge o plugins/ di terze parti): esecuzione nel worker sandboxato
e quarantena dei plugin che lo fanno cadere (F1.6/F1.6.8).

Estratto da `SkillRegistry` (3.2 Reliability & Architecture): il registro tiene il catalogo, questo modulo possiede
lo stato dei plugin - quali intent sono forgiati, il worker condiviso, violazioni e quarantena."""
from __future__ import annotations

from pathlib import Path

from core.logger import get_logger
from core.sandboxed_skill_worker import SandboxedSkillWorker
from core.skill_result import SkillResult


class PluginManager:
    # F1.6.8: numero di violazioni (SANDBOX_WORKER_TIMEOUT) attribuite allo STESSO plugin prima
    # di metterlo in quarantena. Non 1 (un singolo timeout puo' capitare per una chiamata di rete
    # lenta dentro la skill, non necessariamente malevolenza/un bug vero), non un numero grande
    # (un plugin che continua a far cadere il worker condiviso danneggia anche tutte le altre
    # skill forgiate, non solo se stesso - vedi get_or_start_worker).
    QUARANTINE_THRESHOLD = 3

    def __init__(self, logger=None, project_root: Path | None = None):
        self.logger = logger or get_logger()
        self.project_root = project_root or Path(__file__).resolve().parent.parent
        # {intent: percorso del file plugin} per OGNI intent registrato tramite un plugin. Le skill
        # built-in non ci finiscono mai.
        self.forged_intents: dict[str, str] = {}
        # Worker persistente avviato PIGRAMENTE alla prima skill forgiata invocata: Jake non paga un processo in piu'
        # se non ha mai installato una skill forgiata. Carica i plugin UNA VOLTA all'avvio, quindi un nuovo intent
        # forgiato arrivato dopo lo invalida.
        self.worker: SandboxedSkillWorker | None = None
        # {plugin_path: conteggio} delle violazioni (timeout o worker morto a meta' richiesta, es. terminato dal Job
        # Object per aver superato memoria/CPU) attribuite a quel plugin.
        self.violation_counts: dict[str, int] = {}
        # Plugin arrivati a QUARANTINE_THRESHOLD: i loro intent non si eseguono piu' (SKILL_QUARANTINED) e un futuro
        # riavvio del worker li esclude.
        self.quarantined: set[str] = set()

    def register(self, intent: str, plugin_path: str) -> None:
        self.forged_intents[intent] = plugin_path
        self.stop_worker()  # il worker vivo non vede il plugin nuovo: si riavvia alla prossima chiamata

    def is_forged(self, intent: str) -> bool:
        return intent in self.forged_intents

    def execute(self, intent: str, parameters: dict) -> SkillResult:
        plugin_path = self.forged_intents.get(intent)
        if plugin_path is not None and plugin_path in self.quarantined:
            return SkillResult(success=False, data={}, error="SKILL_QUARANTINED")
        worker = self.get_or_start_worker()
        if worker is None:
            return SkillResult(success=False, data={}, error="SANDBOX_WORKER_UNAVAILABLE")
        result = worker.invoke(intent, parameters)
        # SANDBOX_WORKER_TIMEOUT copre "non ha risposto in tempo" e "morto a meta' richiesta": entrambi attribuibili a
        # QUESTA chiamata. SANDBOX_WORKER_UNAVAILABLE no: un ambiente rotto (pywin32 mancante) non e' colpa del plugin.
        if plugin_path is not None and result.error == "SANDBOX_WORKER_TIMEOUT":
            self.record_violation(plugin_path)
        return result

    def record_violation(self, plugin_path: str) -> None:
        count = self.violation_counts.get(plugin_path, 0) + 1
        self.violation_counts[plugin_path] = count
        if count < self.QUARANTINE_THRESHOLD:
            return
        self.quarantined.add(plugin_path)
        self.logger.warning(
            "Plugin %s messo in quarantena dopo %d violazioni (timeout/crash nel worker sandboxato)",
            plugin_path, count,
        )
        # Il worker vivo potrebbe aver caricato il plugin appena messo in quarantena: il prossimo avvio lo esclude.
        self.stop_worker()

    def clear_quarantine(self, plugin_path: str) -> None:
        """Azione esplicita di un amministratore che ha controllato il plugin: le violazioni non si scontano da sole."""
        self.quarantined.discard(plugin_path)
        self.violation_counts.pop(plugin_path, None)

    def get_or_start_worker(self) -> SandboxedSkillWorker | None:
        if self.worker is not None and self.worker.is_alive():
            return self.worker
        # un plugin in quarantena non viene MAI ricaricato da un worker nuovo
        plugin_paths = sorted(set(self.forged_intents.values()) - self.quarantined)
        worker = SandboxedSkillWorker(project_root=str(self.project_root), plugin_paths=plugin_paths, logger=self.logger)
        try:
            worker.start()
        except Exception:
            self.logger.exception("Impossibile avviare il worker sandboxato per le skill forgiate")
            return None
        self.worker = worker
        return worker

    def stop_worker(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.worker = None
