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
from core.path_resolver import resolve_user_path
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
from core.resource_lock import ResourceLockManager
from core.sandboxed_skill_worker import SandboxedSkillWorker
from core.action_snapshot import capture_snapshot, SnapshotStore
from core.skill_result import SkillResult
from copy import deepcopy
from pathlib import Path
import contextlib
import os


class SkillRegistry:
    # F1.6.8: numero di violazioni (SANDBOX_WORKER_TIMEOUT) attribuite allo STESSO plugin prima
    # di metterlo in quarantena. Non 1 (un singolo timeout puo' capitare per una chiamata di rete
    # lenta dentro la skill, non necessariamente malevolenza/un bug vero), non un numero grande
    # (un plugin che continua a far cadere il worker condiviso danneggia anche tutte le altre
    # skill forgiate, non solo se stesso - vedi _get_or_start_sandbox_worker).
    _QUARANTINE_THRESHOLD = 3

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
        # F1.8.1 (ultimo pezzo, "una coda per azioni concorrenti"): vedi _resource_lock_keys() più
        # sotto per quali intent lo usano davvero e perché.
        self._resource_locks = ResourceLockManager()
        # F1.3.4 (adozione - prima fetta, vedi core/action_snapshot.py): un'istanza propria,
        # stesso principio minimale gia' usato per self._resource_locks sopra - nulla la
        # inietta ancora dall'esterno perche' oggi solo execute() la consuma.
        self.snapshot_store = SnapshotStore()

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
        self.skills.update(build_text_and_math_skills(self.config, self.conversation_state))
        self.skills.update(build_fun_skills())
        self.skills.update(build_misc_skills())
        self.skills.update(build_research_skills(self.config, web_search_skill, search_files_skill))
        self.skills.update(build_communication_skills(self.ollama_client, model, self.contact_book))

        # F1.6 (collegamento del worker sandboxato alle skill forgiate): {intent: percorso del
        # file plugin} per OGNI intent registrato tramite un plugin (Skill Forge o plugins/ di
        # terze parti) - vedi register_skill()/core/plugin_loader.py. Le skill built-in non ci
        # finiscono mai (self.skills.update(...) sopra non passa mai da register_skill()).
        self._forged_intents: dict[str, str] = {}
        # Worker persistente (core/sandboxed_skill_worker.py), avviato PIGRAMENTE solo alla prima
        # invocazione di una skill forgiata - Jake non paga il costo di avvio di un processo in
        # piu' se non ha mai installato nessuna skill forgiata. Invalidato (rimesso a None) da
        # register_skill() quando arriva un NUOVO intent forgiato dopo che il worker esiste gia':
        # il worker carica i plugin UNA VOLTA all'avvio, quindi un elenco di plugin cambiato dopo
        # richiede un riavvio per essere visto.
        self._sandbox_worker: SandboxedSkillWorker | None = None
        # F1.6.8 ("terminare e mettere in quarantena plugin che viola limiti o protocollo"):
        # {plugin_path: conteggio} delle violazioni (SANDBOX_WORKER_TIMEOUT - il worker non ha
        # risposto in tempo, o e' morto a meta' richiesta, es. terminato dal Job Object per aver
        # superato memoria/CPU) attribuite a quel plugin. Vuoto per default: nessun plugin ha mai
        # violato nulla finche' non succede per davvero.
        self._plugin_violation_counts: dict[str, int] = {}
        # Popolato quando un plugin raggiunge _QUARANTINE_THRESHOLD violazioni - i suoi intent
        # smettono di essere eseguibili (SKILL_QUARANTINED) e il plugin viene escluso da un
        # futuro riavvio del worker, invece di continuare a farlo ripartire con lo stesso
        # plugin che lo fa cadere in continuazione a ogni chiamata.
        self._quarantined_plugins: set[str] = set()

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
            self._forged_intents[intent] = plugin_path
            if self._sandbox_worker is not None:
                self._sandbox_worker.stop()
                self._sandbox_worker = None
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

    PATH_PARAMETERS = ("path", "destination")

    # F1.8.1 (ultimo pezzo, "una coda per azioni concorrenti"): le quattro skill di mutazione
    # filesystem gia' raggruppate insieme per F1.2.2 (stessa capability, stessi due parametri
    # PATH_PARAMETERS) condividono tutte un controllo-poi-agisci non atomico (`target.exists()`
    # poi `mkdir`/`rename`/`shutil.move`/`unlink` alcune righe piu' sotto, MAI sotto lock) - vedi
    # _resource_lock_keys() sotto per il buco reale riprodotto empiricamente (non ipotizzato) e il
    # perche' del limite dichiarato sul parametro "destination".
    _FILESYSTEM_MUTATION_INTENTS = frozenset({"CREATE_PATH", "RENAME_PATH", "MOVE_PATH", "DELETE_PATH"})

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
        """F1.3.4 (adozione - prima fetta, vedi core/action_snapshot.py): action_id/private sono
        opzionali (default None/False, nessun cambio di comportamento per chi non li passa ancora
        - oggi l'agente/PlanExecutor, vedi il docstring di JakeCore._resolve_and_execute per quale
        chiamante passa gia' cosa) - quando presenti e l'intent e' DELETE_PATH, cattura un
        ActionSnapshot del file PRIMA di chiamare la skill vera (vedi _maybe_capture_snapshot
        sotto), DENTRO lo stesso lock per resource key gia' acquisito per questa mutazione (vedi
        _resource_lock_keys) - senza quel lock un'altra mutazione concorrente sullo stesso
        percorso potrebbe intervenire esattamente nella finestra tra la cattura e la cancellazione
        vera, lo stesso principio "mutare esattamente nel punto giusto" gia' applicato al buco
        reale di F1.8.1 per il lock stesso.

        F1.2.1 (percorso 7, l'ultimo dei tre "percorso N" dichiarati aperti - i percorsi 3 e 6
        sono gia' fail-closed, vedi docs/action-execution-paths.md): questo dispatcher grezzo non
        controllava MAI la policy da solo - un chiamante che lo invoca direttamente, saltando
        `JakeCore._authorize_command()` (o `PlanExecutor`/`decide_automated`), eseguiva la skill
        SENZA alcun controllo su `blocked_intents`. Nei due chiamanti di produzione reali
        (`JakeCore._resolve_and_execute`/`_run_confirmed_action`) questo non era gia' sfruttabile -
        entrambi chiamano `_authorize_command()` PRIMA di arrivare qui - ma era un default
        pericoloso per un futuro chiamante che se lo dimenticasse, stesso principio "nega per
        default" gia' applicato ai percorsi 3/6. `policy_engine=None` e' quindi FAIL-CLOSED (come
        i percorsi 3/6, non piu' "nessun controllo"): solo `blocked_intents` viene ricontrollato
        qui (non una decisione interattiva/automatica completa - CONFIRM/REQUIRE_AUTH non hanno
        senso in un dispatcher sincrono senza un utente pronto a rispondere, la decisione vera e'
        gia' stata presa da chi ha chiamato prima di arrivare qui), stesso identico principio
        minimale gia' applicato a `rollback_effect()` (percorso 6)."""
        skill = self.get_skill(intent)
        if skill is None:
            return None
        if policy_engine is None or intent in policy_engine.blocked_intents:
            return SkillResult(success=False, data={}, error="POLICY_BLOCKED")

        # Percorsi "parlati" (v3.0): "desktop\note.txt", "download" -> percorso reale.
        if parameters:
            for name in self.PATH_PARAMETERS:
                value = parameters.get(name)
                if isinstance(value, os.PathLike):
                    value = os.fspath(value)
                if isinstance(value, str) and value.strip():
                    resolved = resolve_user_path(value, prefer_existing=intent != "CREATE_PATH")
                    if resolved != value:
                        parameters = dict(parameters)
                        parameters[name] = resolved

        # F1.6: una skill forgiata (o di un plugin di terze parti) esegue nel worker sandboxato
        # invece che qui in processo - vedi register_skill() per come un intent finisce in
        # _forged_intents, e il docstring del modulo core/sandboxed_skill_worker.py per il
        # perche' (un Job Object/l'integrita' Low si applicano a un PROCESSO, non a una singola
        # chiamata dentro il processo di Jake).
        lock_keys = self._resource_lock_keys(intent, parameters)

        if intent in self._forged_intents:
            if lock_keys:
                with self._acquire_all_writes(lock_keys):
                    return self._execute_forged(intent, parameters or {})
            return self._execute_forged(intent, parameters or {})

        if lock_keys:
            with self._acquire_all_writes(lock_keys):
                self._maybe_capture_snapshot(intent, parameters, action_id, private)
                return skill.execute(parameters)
        self._maybe_capture_snapshot(intent, parameters, action_id, private)
        return skill.execute(parameters)

    def _maybe_capture_snapshot(
        self, intent: str, parameters: dict | None, action_id: str | None, private: bool,
    ) -> None:
        """F1.3.4 (adozione - prima fetta): DELETE_PATH e' oggi l'unico candidato - e' l'unica
        delle quattro mutazioni filesystem senza un rollback naturale (vedi
        core/execution_safety.py::INTENT_SAFETY_REGISTRY, "cancellare non ha un inverso
        naturale"), quindi solo uno snapshot del contenuto PRIMA della cancellazione rende un
        futuro ripristino possibile - CREATE_PATH/RENAME_PATH/MOVE_PATH hanno gia' un rollback
        vero (ri-eseguire l'inverso), non serve loro anche uno snapshot. `action_id=None` (il
        default di execute()) preserva il comportamento di sempre - nessuno snapshot - per ogni
        chiamante che non ne passa ancora uno."""
        if intent != "DELETE_PATH" or action_id is None or not parameters:
            return
        path = parameters.get("path")
        if not isinstance(path, str) or not path.strip():
            return
        snapshot = capture_snapshot(action_id, path, private=private)
        if snapshot is not None:
            self.snapshot_store.save(snapshot)

    def _resource_lock_keys(self, intent: str, parameters: dict | None) -> tuple[str, ...]:
        """Le resource key da serializzare per QUESTA chiamata - vuoto per ogni intent che non è
        una delle quattro mutazioni filesystem (nessun cambio di comportamento per gli altri ~200
        intent, lock creati pigramente solo quando davvero richiesti).

        Buco reale riprodotto empiricamente prima di scrivere questo fix (due thread veri, `Path.
        exists()` rallentato ad arte nella finestra esatta tra il controllo e l'azione, stesso
        principio "mutare esattamente nel punto giusto" già usato altrove in questa sessione): due
        `MOVE_PATH` concorrenti con sorgenti diverse ma stesso nome file verso la STESSA cartella
        di destinazione superano ENTRAMBI il controllo "il file di destinazione non esiste ancora"
        prima che uno dei due lo crei - risultato, ENTRAMBI riportano `success=True`, ma uno dei
        due file sparisce silenziosamente sovrascritto dall'altro, senza alcun `ALREADY_EXISTS` e
        senza errore di sorta. Lock per RESOURCE KEY (non un lock unico globale sul filesystem,
        che serializzerebbe anche mutazioni su percorsi completamente indipendenti) tramite
        `core/resource_lock.py::ResourceLockManager`, il meccanismo già costruito e testato in
        isolamento (fase 7 del piano multi-device, F1.4) ma mai ancora collegato a un chokepoint
        di produzione reale prima di questo incremento.

        Stesso identico limite già accettato per la capability filesystem di F1.2.2
        (`PolicyEngine._filesystem_capability_allows`): "destination" è la CARTELLA indicata dal
        chiamante, non il percorso finale con il nome del file già appeso (calcolarlo
        duplicherebbe la logica interna della skill, es. `destination_dir / source.name` di
        `MovePathSkill`) - una serializzazione dell'intera cartella di destinazione, più larga del
        necessario ma mai più stretta, quindi comunque corretta per il buco sopra. Stesso limite
        anche per `RENAME_PATH`: `new_name` non è tra `PATH_PARAMETERS`, quindi due `RENAME_PATH`
        con `path` diversi ma stesso `new_name` nella stessa cartella non vengono serializzati tra
        loro - un residuo dichiarato apertamente, non nascosto, della stessa forma già accettata
        altrove in questa sessione (es. `allowed_apps`/`allowed_contacts` sulla stringa grezza)."""
        if intent not in self._FILESYSTEM_MUTATION_INTENTS or not parameters:
            return ()
        keys = []
        for name in self.PATH_PARAMETERS:
            value = parameters.get(name)
            if isinstance(value, os.PathLike):
                value = os.fspath(value)
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                resolved = Path(value).expanduser().resolve()
            except (OSError, ValueError):
                continue
            keys.append(f"filesystem:{os.path.normcase(str(resolved))}")
        return tuple(sorted(set(keys)))

    @contextlib.contextmanager
    def _acquire_all_writes(self, resource_keys: tuple[str, ...]):
        """Acquisisce più lock in ordine ORDINATO (già garantito da `_resource_lock_keys`, che
        restituisce `sorted(set(...))`) per evitare il classico deadlock che si otterrebbe se due
        chiamate concorrenti (es. un MOVE_PATH e il suo inverso) bloccassero le stesse due
        resource key in ordine opposto."""
        with contextlib.ExitStack() as stack:
            for key in resource_keys:
                stack.enter_context(self._resource_locks.acquire_write(key))
            yield

    def _execute_forged(self, intent: str, parameters: dict) -> SkillResult:
        plugin_path = self._forged_intents.get(intent)
        if plugin_path is not None and plugin_path in self._quarantined_plugins:
            return SkillResult(success=False, data={}, error="SKILL_QUARANTINED")
        worker = self._get_or_start_sandbox_worker()
        if worker is None:
            return SkillResult(success=False, data={}, error="SANDBOX_WORKER_UNAVAILABLE")
        result = worker.invoke(intent, parameters)
        # F1.6.8: SANDBOX_WORKER_TIMEOUT copre sia "il worker non ha risposto in tempo" sia "il
        # worker e' morto a meta' richiesta" (es. terminato dal Job Object per aver superato
        # memoria/CPU, vedi core/sandboxed_skill_worker.py) - entrambi attribuibili a QUESTA
        # chiamata. SANDBOX_WORKER_UNAVAILABLE non conta: puo' capitare per un ambiente rotto
        # (pywin32 mancante) che non e' colpa di nessun plugin specifico.
        if plugin_path is not None and result.error == "SANDBOX_WORKER_TIMEOUT":
            self._record_plugin_violation(plugin_path)
        return result

    def _record_plugin_violation(self, plugin_path: str) -> None:
        count = self._plugin_violation_counts.get(plugin_path, 0) + 1
        self._plugin_violation_counts[plugin_path] = count
        if count < self._QUARANTINE_THRESHOLD:
            return
        self._quarantined_plugins.add(plugin_path)
        self.logger.warning(
            "Plugin %s messo in quarantena dopo %d violazioni (timeout/crash nel worker sandboxato)",
            plugin_path, count,
        )
        # Il worker gia' vivo potrebbe aver caricato il plugin appena messo in quarantena: fermato
        # e dimenticato, cosi' il PROSSIMO avvio (_get_or_start_sandbox_worker sotto) lo esclude
        # dall'elenco dei plugin caricati - altrimenti resterebbe servibile fino al prossimo
        # riavvio naturale del worker.
        if self._sandbox_worker is not None:
            self._sandbox_worker.stop()
            self._sandbox_worker = None

    def clear_quarantine(self, plugin_path: str) -> None:
        """F1.6.8: rimuove un plugin dalla quarantena - un'azione esplicita (non c'e' modo
        automatico di 'scontare' le violazioni passate), pensata per un amministratore che ha
        controllato/corretto il plugin, non per un ripristino silenzioso."""
        self._quarantined_plugins.discard(plugin_path)
        self._plugin_violation_counts.pop(plugin_path, None)

    def _get_or_start_sandbox_worker(self) -> SandboxedSkillWorker | None:
        if self._sandbox_worker is not None and self._sandbox_worker.is_alive():
            return self._sandbox_worker
        project_root = str(Path(__file__).resolve().parent.parent)
        # F1.6.8: un plugin in quarantena non viene MAI ricaricato da un worker nuovo, anche se
        # altri intent forgiati (non in quarantena) lo richiedono nel frattempo.
        plugin_paths = sorted(set(self._forged_intents.values()) - self._quarantined_plugins)
        worker = SandboxedSkillWorker(
            project_root=project_root, plugin_paths=plugin_paths, logger=self.logger,
        )
        try:
            worker.start()
        except Exception:
            self.logger.exception("Impossibile avviare il worker sandboxato per le skill forgiate")
            return None
        self._sandbox_worker = worker
        return worker

    def stop_sandbox_worker(self) -> None:
        """Chiamato da JakeCore.shutdown(): nessun worker da fermare se nessuna skill forgiata
        e' mai stata invocata (self._sandbox_worker resta None per costruzione in quel caso)."""
        if self._sandbox_worker is not None:
            self._sandbox_worker.stop()
            self._sandbox_worker = None
