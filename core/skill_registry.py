from skills.search_files import SearchFilesSkill
from skills.web_search import WebSearchSkill
from skills.contacts import ContactBook
from core.skill_catalog import (
    build_time_date_skills, build_app_window_skills, build_filesystem_skills, build_web_skills,
    build_memory_notes_todo_skills, build_automation_skills, build_screen_input_skills, build_media_skills,
    build_system_skills, build_dev_tools_skills, build_text_and_math_skills, build_fun_skills,
    build_misc_skills, build_research_skills, build_communication_skills, build_smart_home_skills,
)
from core.ollama_client import OllamaClient
from core.memory_manager import MemoryManager
from core.conversation_state import ConversationStateManager
from core.nest_client import NestClient
from core.config import Config
from core.embedding_provider import EmbeddingProvider
from core.vision_provider import VisionProvider
from core.planner_provider import PlannerProvider
from core.plan_executor import PlanExecutor
from core.workflow_manager import WorkflowManager
from core.procedure_manager import ProcedureManager
from core.trigger_manager import TriggerManager
from core.reminder_manager import ReminderManager
from core.todo_manager import TodoManager
from core.risk import risk_of
from core.logger import get_logger
from core.plugin_manager import PluginManager
from core.skill_executor import SkillExecutor
from copy import deepcopy


class SkillRegistry:
    """Catalogo delle skill: costruzione, registrazione, capacita' e rischio. L'esecuzione e' delegata a
    `SkillExecutor` (core/skill_executor.py) e le skill dei plugin a `PluginManager` (core/plugin_manager.py)."""

    _QUARANTINE_THRESHOLD = PluginManager.QUARANTINE_THRESHOLD
    PATH_PARAMETERS = SkillExecutor.PATH_PARAMETERS
    _FILESYSTEM_MUTATION_INTENTS = SkillExecutor.FILESYSTEM_MUTATION_INTENTS

    def __init__(
        self,
        memory_manager: MemoryManager | None = None,
        conversation_state: ConversationStateManager | None = None,
        nest_client: NestClient | None = None,
        config: Config | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        reminder_manager: ReminderManager | None = None,
        logger=None,
    ):
        self.logger = logger or get_logger()
        self.memory_manager = memory_manager or MemoryManager()
        self.conversation_state = conversation_state or ConversationStateManager()
        self.nest_client = nest_client or NestClient()
        self.config = config or Config()
        self.embedding_provider = embedding_provider or EmbeddingProvider(
            model=self.config.get("embedding_model", "nomic-embed-text")
        )
        self.vision_provider = VisionProvider(model=self.config.get("vision_model", "qwen2.5vl:7b"))
        self.planner_provider = PlannerProvider(self, model=self.config.get("ollama_model", "qwen2.5:7b"))
        self.plan_executor = PlanExecutor(self)
        self.workflow_manager = WorkflowManager(self.memory_manager)
        self.trigger_manager = TriggerManager(self.memory_manager, self.workflow_manager)
        # F3.8.5 (adozione): stesso principio di self.workflow_manager sopra - una procedura di
        # computer use (F3.8) e' concettualmente "una sequenza di passi salvata con nome", solo
        # di passi UI Automation invece che di intent/skill.
        self.procedure_manager = ProcedureManager(self.memory_manager)
        self.reminder_manager = reminder_manager or ReminderManager()
        self.todo_manager = TodoManager()
        # Client Ollama condiviso (v3.0: 127.0.0.1, keep_alive lungo) e rubrica.
        self.ollama_client = OllamaClient()
        self.contact_book = ContactBook(self.memory_manager)
        model = self.config.get("ollama_model", "qwen2.5:7b")

        # Condivise anche col research agent (v1.5), che le orchestra invece di limitarsi a
        # incatenarle come fa il Planner generico.
        web_search_skill = WebSearchSkill()
        search_files_skill = SearchFilesSkill(self.nest_client, self.conversation_state)

        # Il catalogo delle skill built-in (~180) vive in core/skill_catalog.py, raggruppato
        # per dominio (v3.2): qui si uniscono solo i sotto-dizionari.
        self.skills = {}
        self.skills.update(build_time_date_skills())
        self.skills.update(build_app_window_skills())
        self.skills.update(build_filesystem_skills(self.nest_client, self.conversation_state, search_files_skill))
        self.skills.update(build_web_skills(self.config, web_search_skill))
        self.skills.update(build_memory_notes_todo_skills(self.memory_manager, self.embedding_provider, self.todo_manager))
        self.skills.update(build_automation_skills(
            self.reminder_manager, self.planner_provider, self.workflow_manager,
            self.trigger_manager, self.plan_executor, self.procedure_manager,
        ))
        self.skills.update(build_screen_input_skills(self.vision_provider))
        self.skills.update(build_media_skills())
        self.skills.update(build_system_skills())
        self.skills.update(build_smart_home_skills(self.config))
        self.skills.update(build_dev_tools_skills())
        self.skills.update(build_text_and_math_skills(self.config, self.conversation_state, self.memory_manager,
                                                      self.embedding_provider))
        self.skills.update(build_fun_skills())
        self.skills.update(build_misc_skills())
        self.skills.update(build_research_skills(self.config, web_search_skill, search_files_skill))
        self.skills.update(build_communication_skills(self.ollama_client, model, self.contact_book))

        self._plugins = PluginManager(logger=self.logger)
        self._executor = SkillExecutor(self._plugins)

    def get_skill(self, intent: str):
        return self.skills.get(intent, None)

    def has_skill(self, intent: str) -> bool:
        return intent in self.skills

    def register_skill(self, intent: str, skill, plugin_path: str | None = None) -> None:
        """Registra una skill aggiuntiva a runtime: il punto di estensione per plugin di terze
        parti e per la Skill Forge (core/skill_forge.py), senza dover modificare l'elenco
        hardcoded in __init__. La skill deve esporre un attributo 'metadata' (intent/
        description/parameters) e un metodo execute(parameters).

        F1: le skill built-in vengono caricate con self.skills.update(...) direttamente in
        __init__, MAI passando da qui - questo metodo e' quindi solo il punto d'ingresso di
        plugin/fucina, il posto giusto per accorgersi se uno dei due sta silenziosamente
        sostituendo un intent gia' esistente (built-in o di un altro plugin) mantenendone il
        nome. Prima di questo controllo un plugin scritto a mano (o un file copiato per
        errore/malevolenza dentro plugins/) poteva dichiarare `register(registry):
        registry.register_skill("SYSTEM_POWER", MiaClasse())` e rimpiazzare del tutto il codice
        reale dietro un intent gia' classificato ADMIN in core/risk.py, senza che nulla lo
        segnalasse - il livello di rischio esposto da list_capabilities() sarebbe rimasto
        invariato (deriva dal nome dell'intent, non dall'implementazione) mentre il codice
        eseguito sarebbe stato tutt'altro. Non blocca la sostituzione (un plugin che rimpiazza
        di proposito una skill built-in e' un uso legittimo del punto di estensione, e la Skill
        Forge gia' rifiuta da sola un intent duplicato prima di generare codice - vedi
        SkillForge._validate): la registra comunque, ma lo rende visibile invece di silenzioso.

        F1.6: `plugin_path` (passato da core/plugin_loader.py per OGNI skill che viene da un
        file di plugin, mai dalle skill built-in sopra) marca l'intent come da eseguire nel
        worker sandboxato (core/sandboxed_skill_worker.py) invece che in processo - vedi
        execute() sotto. Un worker gia' avviato viene invalidato (fermato, dimenticato) qui:
        carica i plugin una volta sola all'avvio, quindi un nuovo intent forgiato arrivato dopo
        (un'installazione a caldo dalla Skill Forge) richiede un riavvio per essere servito."""
        if plugin_path is not None:
            self._plugins.register(intent, plugin_path)
        existing = self.skills.get(intent)
        if existing is not None and existing is not skill:
            self.logger.warning(
                "L'intent %s era gia' registrato (%s) ed e' stato sovrascritto da %s: controlla "
                "che sia voluto, specialmente se una delle due skill viene da un plugin o dalla "
                "Skill Forge.", intent, type(existing).__name__, type(skill).__name__,
            )
        self.skills[intent] = skill

    def is_remote(self, intent: str) -> bool:
        """Vero se la skill richiede rete/servizi esterni (distinzione tool locali/remoti)."""
        skill = self.get_skill(intent)
        return bool(getattr(skill, "metadata", {}).get("remote", False))

    def list_capabilities(self) -> list[dict]:
        """Restituisce i metadata delle skill registrate, incluso il livello di rischio
        (v3.2, vedi core/risk.py: READ_ONLY/LOCAL_REVERSIBLE/EXTERNAL_ACTION/DESTRUCTIVE/ADMIN).

        F1.8.2: buco reale, riprodotto per davvero prima del fix - `register_skill()` (il punto
        d'ingresso della Skill Forge/dei plugin, raggiungibile da un comando voce/companion
        mentre un'ALTRA richiesta concorrente sta chiamando `list_capabilities()`, es. per il
        routing/retrieval semantico di un intent) aggiunge una nuova chiave a `self.skills`. Un
        `for intent, skill in self.skills.items():` diretto itera il dict LIVE un elemento alla
        volta (un punto di cambio thread naturale a ogni iterazione): se `register_skill()`
        cambia la dimensione del dict a meta' di questo ciclo, Python solleva
        `RuntimeError: dictionary changed size during iteration`, facendo fallire l'intera
        richiesta in corso. Riprodotto con `sys.setswitchinterval()` abbassato per forzare la
        sovrapposizione reale. Corretto iterando su `list(self.skills.items())`, uno snapshot
        preso in un'unica chiamata atomica (mai interrotta a meta' da una mutazione Python-level
        di un altro thread), invece del dict live."""
        capabilities = []
        for intent, skill in list(self.skills.items()):
            metadata = deepcopy(getattr(skill, "metadata", {}))
            metadata.setdefault("intent", intent)
            metadata.setdefault("description", "")
            metadata.setdefault("parameters", {})
            metadata["risk"] = risk_of(intent).value
            capabilities.append(metadata)
        return capabilities

    def risk_of(self, intent: str):
        """Livello di rischio (core.risk.RiskLevel) dell'intent, ADMIN per default se non
        censito: punto d'accesso unico, cosi' chi deve decidere se chiedere conferma non deve
        importare core/risk.py direttamente."""
        return risk_of(intent)

    def unregister_skill(self, intent: str) -> bool:
        return self.skills.pop(intent, None) is not None

    def app_names(self) -> list[str]:
        """Nomi delle app installate (v3.0: vocabolario per correggere le trascrizioni vocali)."""
        skill = self.get_skill("OPEN_APP")
        resolver = getattr(skill, "app_resolver", None)
        try:
            return resolver.display_names() if resolver is not None else []
        except Exception:
            return []

    def execute(
        self, intent: str, parameters: dict | None = None, policy_engine=None, *,
        action_id: str | None = None, private: bool = False,
    ):
        """Esegue la skill dell'intent: vedi SkillExecutor.execute (policy fail-closed con `policy_engine=None`,
        lock per risorsa, snapshot di DELETE_PATH, worker sandboxato per i plugin). None se l'intent non esiste."""
        return self._executor.execute(self.get_skill(intent), intent, parameters, policy_engine,
                                      action_id=action_id, private=private)

    def clear_quarantine(self, plugin_path: str) -> None:
        """F1.6.8: rimuove un plugin dalla quarantena - azione esplicita di un amministratore."""
        self._plugins.clear_quarantine(plugin_path)

    def stop_sandbox_worker(self) -> None:
        """Chiamato da JakeCore.shutdown()."""
        self._plugins.stop_worker()

    # ---- compatibilita': stato che ora vive nei componenti ---------------------------------

    def _resource_lock_keys(self, intent: str, parameters: dict | None) -> tuple[str, ...]:
        return self._executor.resource_lock_keys(intent, parameters)

    def _record_plugin_violation(self, plugin_path: str) -> None:
        self._plugins.record_violation(plugin_path)

    def _get_or_start_sandbox_worker(self):
        return self._plugins.get_or_start_worker()

    @property
    def snapshot_store(self):
        return self._executor.snapshot_store

    @snapshot_store.setter
    def snapshot_store(self, value) -> None:
        self._executor.snapshot_store = value

    @property
    def _resource_locks(self):
        return self._executor.resource_locks

    @_resource_locks.setter
    def _resource_locks(self, value) -> None:
        self._executor.resource_locks = value

    @property
    def _forged_intents(self) -> dict[str, str]:
        return self._plugins.forged_intents

    @_forged_intents.setter
    def _forged_intents(self, value: dict[str, str]) -> None:
        self._plugins.forged_intents = value

    @property
    def _sandbox_worker(self):
        return self._plugins.worker

    @_sandbox_worker.setter
    def _sandbox_worker(self, value) -> None:
        self._plugins.worker = value

    @property
    def _plugin_violation_counts(self) -> dict[str, int]:
        return self._plugins.violation_counts

    @_plugin_violation_counts.setter
    def _plugin_violation_counts(self, value: dict[str, int]) -> None:
        self._plugins.violation_counts = value

    @property
    def _quarantined_plugins(self) -> set[str]:
        return self._plugins.quarantined

    @_quarantined_plugins.setter
    def _quarantined_plugins(self, value: set[str]) -> None:
        self._plugins.quarantined = value
