import contextlib
import threading
import time
from datetime import time as datetime_time

from core import fallbacks
from core import intent_patterns
from core.action_contracts import ActionError, ActionProposal, validate_action_error, validate_action_proposal
from core.action_ledger import ActionLedger, ActionReceipt, authorization_of, idempotency_key_of, new_action_id
from core.agent import TaskAgent
from core.agent_checkpoint import AgentCheckpoint, AgentCheckpointStore
from core.auth_gate import AuthGate
from core.autonomy_budget import AutonomyBudget
from core.command import Command
from core.companion_guard import CompanionAudit, CompanionGuard, is_loopback_host
from core.companion_server import DEFAULT_HOST as DEFAULT_COMPANION_HOST
from core.companion_server import CompanionServer
from core.companion_tls import build_server_context, current_fingerprint, ensure_certificate
from core.context_summarizer import ContextSummarizer
from core.desktop_context import DesktopContextTracker
from core.device_credential_store import DeviceCredentialStore
from core.event_bus import EventBus
from core.execution_safety import ActionExecution
from core.hud_protocol import EventType, HudEvent
from core.identity import current_windows_user
from core.kill_switch import KillSwitch
from core.turn_cancellation import TurnCancelled, current_turn_cancelled
from core.learning_manager import LearningManager
from core.logger import get_logger, log_action, new_trace_id
from core.nlu import chitchat
from core.nlu.examples import ExampleStore
from core.nlu.index import lexical_similarity
from core.nlu.normalizer import TranscriptNormalizer
from core.nlu.retriever import CapabilityRetriever
from core.notification_center import NotificationCenter
from core.notification_policy import NotificationPolicy, QuietHours
from core.ollama_client import OllamaClient
from core import orchestrator
from core.orchestrator import JakeOrchestrator
from core.pairing_service import PairingService
from core.policy_engine import (
    POLICY_REASON_LOW_RECOGNITION_CONFIDENCE, POLICY_REASONS, PolicyDecision, PolicyEngine,
    strip_authorization_signals,
)
from core.plugin_loader import load_plugins
from core.profiles import ProfileError, ProfileManager
from core.request_context import (
    current_action_id, current_command_source_intent, current_device_id, current_session_id,
    current_speaker_profile_id, current_stt_confidence, reset_current_command_source_intent,
    set_current_command_source_intent,
)
from core.taint import wrap_external_content
from core.response_formatter import format_plan_outcome, format_skill_result
from core.risk import risk_of
from core.voice.dialogue import (
    DialogueContext,
    ReplyKind,
    classify_reply,
    needs_confirmation,
)
from core.voice.dialogue_runtime import DialogueRuntime
from core.voice.language_normalizer import resolve_ellipsis, resolve_ordinals
from core.router import Router
from core.scheduler import ReminderScheduler
from core.schema_validation import validate_confirm_envelope
from core.session_hooks import SessionHooks
from core.session_recorder import SessionRecorder
from core.skill_forge import SkillForge
from core.skill_registry import SkillRegistry
from core.skill_result import SkillResult
from core.sync_crypto import Keyring
from core.system_advisor import SystemAdvisor
from core.task_monitor import MonitorStore, TaskMonitorRegistry
from core.task_notification_bridge import TaskNotificationBridge
from core.trigger_scheduler import TriggerScheduler
from core.undo_store import UndoStore, generate_undo_descriptor
from skills.kill_switch import KillSwitchSkill, ResetKillSwitchSkill
from skills.learn import CorrectLastSkill, ForgetLearnedSkill, LearnCommandSkill, ListLearnedSkill
from skills.model_control import ListModelsSkill, SetModelSkill
from skills.session_control import (
    HelpSkill, PauseListeningSkill, PrivateModeSkill, RepeatLastSkill, ResumeInterruptedTaskSkill,
    StartDictationSkill, StopDictationSkill, StopTalkingSkill,
)
from skills.notification_mode import GetNotificationModeSkill, SetNotificationModeSkill
from skills.pairing import ApprovePairingSkill
from skills.skill_forge_skills import CreateSkillSkill, DeleteCreatedSkillSkill, ListCreatedSkillsSkill
from skills.undo import UndoLastActionSkill


def _parse_quiet_hours(start: str | None, end: str | None) -> QuietHours | None:
    """F6.3: "HH:MM"-"HH:MM" da config.json (opt-in, come companion_token/session_recording_*) -
    None se non impostate (comportamento invariato: NotificationPolicy senza quiet hours) o se il
    formato non e' valido (un valore corrotto in config non deve mai far fallire l'avvio di Jake)."""
    if not start or not end:
        return None
    try:
        start_h, start_m = (int(part) for part in start.split(":", 1))
        end_h, end_m = (int(part) for part in end.split(":", 1))
        return QuietHours(datetime_time(start_h, start_m), datetime_time(end_h, end_m))
    except (ValueError, TypeError):
        return None


class JakeCore:
    EXIT_SENTINEL = "l'utente vuole uscire"
    NO_PLAN = "Non so ancora fare questa cosa"

    _NOT_A_FAILURE = {"success", "confirmation_required", "auth_required"}

    def __init__(self):
        self.logger = get_logger()
        self.skill_registry = SkillRegistry()
        config = self.skill_registry.config
        self.config = config
        self.ollama = self.skill_registry.ollama_client
        self.model = config.get("ollama_model", "qwen2.5:7b")

        # Modalita' di notifica (v4.3, Notification/Priority system): decide se un promemoria,
        # un avviso proattivo o un'automazione partita da sola interrompe subito o resta in
        # coda per dopo. Creato presto: sia lo scheduler dei promemoria sia il trigger scheduler
        # sia il system advisor, costruiti piu' sotto, notificano gia' passando da qui.
        self.notification_center = NotificationCenter()

        # Modalita' privata (v5.6, Privacy Engine): quando attiva, answer() non scrive lo
        # scambio ne' nella memoria a lungo termine ne' nel log operativo (vedi answer()). Non
        # e' salvata su disco: riparte sempre disattivata a ogni avvio di Jake, cosi' non puo'
        # restare attiva per sbaglio senza che l'utente se ne accorga in una sessione successiva.
        self.private_mode = False

        # Replay anonimizzato/deterministico delle sessioni fallite (F0): disattivato per
        # default, come companion_server_enabled/system_advisor_enabled - va acceso di proposito
        # in config/settings.json, non e' un log che parte da solo. session_recording_verbatim
        # (anch'esso per default disattivato) toglie la redazione dei parametri: serve a chi sta
        # davvero debuggando un fallimento su questa macchina e sa che sta scrivendo dati veri.
        self.session_recorder = SessionRecorder(
            enabled=bool(config.get("session_recording_enabled", False)),
            verbatim=bool(config.get("session_recording_verbatim", False)),
        )
        # PlanExecutor e' costruito dentro SkillRegistry (self.skill_registry.plan_executor),
        # prima che self.session_recorder esista qui: gli viene assegnato subito dopo, invece di
        # passargli un secondo SessionRecorder scollegato che scriverebbe altrove.
        self.skill_registry.plan_executor.session_recorder = self.session_recorder

        # Action ledger append-only (F1, Trustworthy Agent Core 3.0): "chi ha chiesto cosa,
        # quale agente ha deciso, quale skill ha agito, con quale autorizzazione e quale
        # risultato" - vedi core/action_ledger.py. Sempre attivo (a differenza di session_
        # recorder, che e' un debug tool opt-in): un registro di responsabilita' non ha senso se
        # e' disattivato per default. Rispetta comunque la modalita' privata, come tutto il resto.
        self.action_ledger = ActionLedger()
        self.skill_registry.plan_executor.action_ledger = self.action_ledger

        # F1.8.4 ("checkpoint... da cui riprendere"): un solo checkpoint alla volta, salvato dopo
        # ogni passo dell'agente "general" - vedi core/agent_checkpoint.py per lo scope
        # deliberatamente stretto (nessuna ripresa automatica, un compito alla volta).
        self.agent_checkpoints = AgentCheckpointStore()

        # Kill switch globale (F1): un solo interruttore condiviso da tutti gli agenti e le
        # automazioni - vedi core/kill_switch.py, skills/kill_switch.py, activate_kill_switch()/
        # reset_kill_switch() piu' sotto. Azionabile da voce/testo (skill KILL_SWITCH) e, quando
        # il resto dell'infrastruttura lo aggancera' (hotkey globale, tray - non fatto in questa
        # sessione), da li' allo stesso oggetto.
        self.kill_switch = KillSwitch()
        self.skill_registry.plan_executor.kill_switch = self.kill_switch
        # F1.8.3: RUN_COMMAND e' l'unica skill con un subprocess bloccante abbastanza lungo
        # (fino a 30s) da rendere il controllo "solo tra un passo e il successivo" di
        # TaskAgent/PlanExecutor insufficiente - vedi il docstring di skills/run_command.py.
        run_command_skill = self.skill_registry.get_skill("RUN_COMMAND")
        if run_command_skill is not None:
            run_command_skill.kill_switch = self.kill_switch

        # Protocollo eventi + server companion (v4.9.1 HUD IPC transport, v5.8 Mobile
        # Companion, v5.9 Ambient Computing): qualsiasi presentazione esterna (HUD nativo,
        # app companion su un altro dispositivo) puo' iscriversi a self.event_bus senza essere
        # un processo Python nello stesso interprete (vedi core/hud_protocol.py, core/
        # companion_server.py). Il server e' costruito sempre ma NON avviato per default:
        # parte solo se companion_server_enabled e' esplicitamente vero in config.json, stesso
        # pattern gia' usato da system_advisor_enabled.
        self.event_bus = EventBus()

        # F6.3/F6.7 (Notification Intelligence + Task Monitor, primo collegamento reale a
        # JakeCore/EventBus/HUD): entrambi i moduli esistevano gia' come librerie testate ma
        # scollegate da qualunque punto reale della pipeline. Il ponte (core/task_notification_
        # bridge.py) segue un compito composto (TaskAgent) passo per passo in silenzio
        # (task_monitor, F6.7.1) e, quando incontra un evento IMPORTANTE a meta' strada (una
        # conferma/autenticazione richiesta, una domanda di chiarimento - vedi _run_agent piu'
        # sotto), lo valuta per urgenza/contesto con notification_policy (F6.3: priorita', la
        # modalita' CORRENTE, quiet hours, contatti critici/VIP) e pubblica su event_bus un
        # HudEvent NOTIFICATION con task/session/device id, le azioni GIA' eseguite e la
        # decisione richiesta - leggibile da un HUD o un'app companion in tempo reale, non solo
        # dal testo della conversazione. mode_source legge notification_center.mode (sopra) AD
        # OGNI decisione invece di tenerne una seconda copia che potrebbe disallinearsi (la
        # stessa classe di buco gia' trovata e corretta piu' volte in questa sessione per altri
        # stati condivisi tra thread/componenti).
        self.task_monitor = TaskMonitorRegistry()
        self.task_monitor_store = MonitorStore()
        self.notification_policy = NotificationPolicy(
            mode=self.notification_center.mode,
            quiet_hours=_parse_quiet_hours(
                config.get("notification_quiet_hours_start"), config.get("notification_quiet_hours_end"),
            ),
            critical_contacts=set(config.get("notification_critical_contacts") or []),
            vip_contacts=set(config.get("notification_vip_contacts") or []),
        )
        self.task_bridge = TaskNotificationBridge(
            self.task_monitor, self.notification_policy, self.event_bus,
            mode_source=lambda: self.notification_center.mode,
        )

        # F1 (Identity & Authentication, "capability token... per dispositivo" - vedi
        # ROADMAP.md): opt-in - se companion_token non e' mai stato impostato in config.json,
        # il server resta come prima di questa fase (nessuna autenticazione), per non cambiare
        # comportamento a chi ha gia' un uso locale/fidato. Cifrato a riposo via DPAPI come
        # admin_passphrase (core/config.py, SECRET_KEYS).
        # F1.4.6 (fase 6 del piano multi-device): credential_store e' costruito SEMPRE (costa
        # solo l'apertura di un file SQLite, nessun I/O di rete) - permette a companion_server di
        # autenticare per-dispositivo appena il primo pairing avviene, senza bisogno di un
        # riavvio di Jake per "attivare" la funzionalita'.
        self.device_credential_store = DeviceCredentialStore()
        # F7.1.2/Companion Mobile MVP: il servizio di pairing (fase 4 del piano) collegato per
        # davvero agli endpoint HTTP (core/companion_server.py) - vedi _on_pairing_requested piu'
        # sotto per il lato "conferma sul PC". sync_keyring (F7.6) e' il portachiavi dei
        # dispositivi companion per la sincronizzazione cifrata: pairing e' anche il momento in
        # cui le chiavi pubbliche di sync di un dispositivo (se le manda) entrano qui - vedi
        # skills/pairing.py. Nessun trasporto/transport reale usa ancora questo portachiavi
        # (limite dichiarato F7.6), qui solo la registrazione.
        self.sync_keyring = Keyring()
        self.pairing_service = PairingService(self.device_credential_store)
        # F1.3.5 (adozione - collegato a tutti e tre i chokepoint reali: il percorso a comando
        # diretto qui sotto, i tre TaskAgent - vedi self.agent/coding_agent/research_agent - e
        # PlanExecutor subito sotto, stesso principio "un solo store condiviso" gia' applicato a
        # session_recorder/action_ledger/kill_switch).
        self.undo_store = UndoStore()
        # PlanExecutor e' costruito dentro SkillRegistry, prima che self.undo_store esista qui -
        # stesso motivo/stesso pattern gia' usato sopra per session_recorder/action_ledger/
        # kill_switch: assegnato subito dopo, invece di lasciargli l'istanza locale che
        # UndoStore.__init__ crea da solo quando nessuno gliene passa una.
        self.skill_registry.plan_executor.undo_store = self.undo_store
        # F7.1.4/F7.2 (Companion Mobile MVP): un telefono VERO e' una macchina diversa dal PC - non puo'
        # raggiungere il server su un'interfaccia diversa da 127.0.0.1 senza TLS (check_bind_policy lo impone
        # gia': "un bind LAN senza TLS non deve esistere nemmeno per un istante"). Fino a questo incremento
        # nessun tls_context veniva mai costruito, quindi il server poteva SOLO ascoltare in loopback - qui lo
        # si costruisce (certificato autofirmato persistito, core/companion_tls.py) quando companion_server_host
        # e' stato impostato a qualcosa di diverso dal default loopback, o esplicitamente richiesto per il solo
        # TLS (companion_server_tls_enabled - utile anche in loopback, per sviluppo/test dell'app companion).
        # Comportamento INVARIATO (nessun certificato generato, come da sempre) per chi non ha mai toccato
        # nessuna delle due chiavi.
        companion_host = config.get("companion_server_host") or DEFAULT_COMPANION_HOST
        companion_tls_context = None
        self.companion_tls_fingerprint = None
        if not is_loopback_host(companion_host) or bool(config.get("companion_server_tls_enabled", False)):
            cert_path, key_path = ensure_certificate()
            companion_tls_context = build_server_context(cert_path, key_path)
            self.companion_tls_fingerprint = current_fingerprint(cert_path)
            self.logger.info("TLS companion attivo - impronta del certificato: %s", self.companion_tls_fingerprint)
        self.companion_server = CompanionServer(
            event_bus=self.event_bus, command_handler=self.answer, host=companion_host,
            port=int(config.get("companion_server_port", 8765) or 8765),
            token=config.get("companion_token"), credential_store=self.device_credential_store,
            guard=CompanionGuard(audit=CompanionAudit()), tls_context=companion_tls_context,
            tls_fingerprint=self.companion_tls_fingerprint,
            pairing_service=self.pairing_service, conversation_state=self.skill_registry.conversation_state,
            on_pairing_requested=self._on_pairing_requested,
        )
        if bool(config.get("companion_server_enabled", False)):
            self.companion_server.start()
            self.logger.info(
                "Server companion in ascolto su %s:%d%s", companion_host, self.companion_server.port,
                " (TLS)" if companion_tls_context is not None else "",
            )

        # Skill/plugin installabili (v2.0): un file .py in plugins/ con una funzione
        # register(registry) diventa una capacita' di Jake senza toccare il core.
        self.loaded_plugins = load_plugins(self.skill_registry, logger=self.logger)

        # Comprensione (v3.0): normalizzazione del parlato, esempi, recupero semantico.
        self.normalizer = TranscriptNormalizer(app_names_provider=self.skill_registry.app_names)
        self.example_store = ExampleStore()
        embedding_model = config.get("embedding_model", "nomic-embed-text")
        self.retriever = CapabilityRetriever(
            self.skill_registry, self.example_store,
            embedder=lambda texts: self.ollama.embed(embedding_model, texts, timeout=120),
            model_name=embedding_model,
        )
        self.learning = LearningManager(self.example_store, self.retriever, self.normalizer, logger=self.logger)
        self.session_hooks = SessionHooks()

        # Agente a passi (v3.1): per le richieste composte o non capite dal classificatore,
        # invece di eseguire un piano fisso scritto in anticipo, Jake pensa un passo alla
        # volta e guarda il risultato vero prima di decidere il successivo (vedi core/agent.py).
        self.agent = TaskAgent(
            self.skill_registry, self.retriever, self.ollama, model_provider=lambda: self.model,
            format_result=lambda intent, result: format_skill_result(intent, result, self.skill_registry),
            logger=self.logger,
            context_provider=lambda: self._agent_context(),
            # F1.3.4: action_id letto da core.request_context.current_action_id(), impostato da
            # TaskAgent.run() solo intorno a questa chiamata - vedi il docstring di
            # _resolve_and_execute e quello del contextvar per il perche'.
            executor=lambda intent, parameters: self._resolve_and_execute(
                Command(intent, parameters), action_id=current_action_id(),
            ),
            session_recorder=self.session_recorder, action_ledger=self.action_ledger, agent_name="general",
            kill_switch=self.kill_switch, undo_store=self.undo_store,
        )
        self.agent.on_step = self._on_agent_step
        # F1.8.4 ("checkpoint... da cui riprendere"): collegato per tutti e tre gli agenti (vedi
        # sotto per coding_agent/research_agent) - AgentOutcome.agent_name (popolato da run()
        # stesso) distingue quale dei tre ha prodotto un dato checkpoint.
        self.agent.on_step_completed = self._on_agent_step_completed

        # Architettura multi-agente (v5.0/5.1/5.2): stesso TaskAgent, configurato con un
        # elenco di strumenti fisso e un prompt diverso per i domini coding/ricerca, invece del
        # recupero semantico generico su tutte le ~200 capacita'. JakeOrchestrator sceglie quale
        # dei tre usare in base alla richiesta (vedi core/orchestrator.py).
        agent_kwargs = {
            "model_provider": lambda: self.model,
            "format_result": lambda intent, result: format_skill_result(intent, result, self.skill_registry),
            "logger": self.logger,
            "context_provider": lambda: self._agent_context(),
            "executor": lambda intent, parameters: self._resolve_and_execute(
                Command(intent, parameters), action_id=current_action_id(),
            ),
            "session_recorder": self.session_recorder, "action_ledger": self.action_ledger,
            "kill_switch": self.kill_switch, "undo_store": self.undo_store,
        }
        self.coding_agent = TaskAgent(
            self.skill_registry, self.retriever, self.ollama,
            fixed_tools=list(orchestrator.CODING_TOOLS), persona_line=orchestrator.CODING_PERSONA,
            agent_name="coding", **agent_kwargs,
        )
        self.coding_agent.on_step = self._on_agent_step
        self.coding_agent.on_step_completed = self._on_agent_step_completed
        self.research_agent = TaskAgent(
            self.skill_registry, self.retriever, self.ollama,
            fixed_tools=list(orchestrator.RESEARCH_TOOLS), persona_line=orchestrator.RESEARCH_PERSONA,
            agent_name="research", **agent_kwargs,
        )
        self.research_agent.on_step = self._on_agent_step
        self.research_agent.on_step_completed = self._on_agent_step_completed
        self.orchestrator = JakeOrchestrator(self.agent, self.coding_agent, self.research_agent)

        self.router = Router(
            skill_registry=self.skill_registry, example_store=self.example_store,
            retriever=self.retriever, client=self.ollama,
        )
        self.conversation_state = self.skill_registry.conversation_state
        self.memory_manager = self.skill_registry.memory_manager
        # F2.7 (adozione, seconda fetta - isolamento vero di memoria per profilo): None per
        # default (opt-in via multi_user_profiles_enabled in config.json), nessun cambio di
        # comportamento per chi non lo attiva - stesso principio "opt-in, zero impatto" gia'
        # seguito per ogni altro meccanismo opzionale in questo progetto. Nessun profilo
        # arruolato esiste finche' l'utente non ne crea uno esplicitamente
        # (ProfileManager.create_profile, oggi raggiungibile solo da codice/test: nessuna
        # skill/CLI di gestione profili esiste ancora, gap dichiarato separatamente).
        self.profile_manager = ProfileManager() if bool(config.get("multi_user_profiles_enabled", False)) else None
        # MemoryManager e ConversationStateManager sono istanze condivise con molte skill. Lo
        # stesso oggetto viene quindi ripuntato per il SOLO turno riconosciuto e ripristinato nel
        # finally; il lock impedisce che una richiesta companion concorrente osservi il profilo
        # vocale di un'altra persona.
        self._profile_turn_lock = threading.RLock()
        self._active_profile_namespace = None
        self.planner_provider = self.skill_registry.planner_provider
        self.plan_executor = self.skill_registry.plan_executor
        self.context_summarizer = ContextSummarizer(model=self.router.primary_provider.model)
        # Autenticazione per le azioni ADMIN (v5.4/5.5, Permissions & Security Kernel +
        # Identity & Authentication): opt-in, vedi core/auth_gate.py. Senza una passphrase ne'
        # Windows Hello configurati (admin_passphrase/windows_hello_enabled in config.json)
        # resta disabilitata e le azioni ADMIN continuano a passare solo dalla conferma si'/no,
        # come prima di questa fase. windows_hello_enabled e' un fattore IN PIU', non un
        # sostituto (F1): "la voce può riconoscere l'utente per comodità, ma non deve essere
        # l'unico fattore di sicurezza", vedi la fase F1 in ROADMAP.md.
        self.auth_gate = AuthGate(
            passphrase=config.get("admin_passphrase"),
            windows_hello_enabled=bool(config.get("windows_hello_enabled", False)),
        )
        # F1 (separazione formale planner/policy engine/executor, core/policy_engine.py): UN
        # oggetto condiviso PER RIFERIMENTO con tutto cio' che deve decidere se un intent puo'
        # eseguire (qui stesso, PlanExecutor, TriggerScheduler, RunWorkflowSkill), invece di
        # blocked_intents/always_confirm_intents/require_auth_intents passati a mano come tre
        # insiemi separati - esattamente la frammentazione che ha causato il bug di
        # RunWorkflowSkill (che ne riceveva solo due su tre, dimenticando il terzo). Chi ha
        # bisogno di leggere/aggiornare uno di quei tre insiemi lo fa ora via
        # self.policy_engine.blocked_intents/always_confirm_intents/require_auth_intents, non
        # piu' via self.blocked_intents/... direttamente su JakeCore.
        self.policy_engine = PolicyEngine(
            auth_gate=self.auth_gate,
            blocked_intents=config.get("blocked_intents", []) or [],
            always_confirm_intents=config.get("always_confirm_intents", []) or [],
            # F1.2.2: vuoto per default (nessuna restrizione, comportamento invariato) - un
            # utente avanzato puo' limitare CREATE_PATH/RENAME_PATH/MOVE_PATH/DELETE_PATH a
            # cartelle esplicite in config.json, come gia' fa con blocked_intents.
            allowed_filesystem_roots=config.get("allowed_filesystem_roots", []) or [],
            # F1.2.3 (prima capability per dispositivo): {device_id: [intent, ...]} in
            # config.json, vuoto per default. Es. {"phone-ospite": ["DELETE_PATH", "RUN_COMMAND"]}
            # limita cosa puo' fare QUEL dispositivo companion, senza toccare la voce locale o
            # altri dispositivi.
            device_blocked_intents=config.get("device_blocked_intents", {}) or {},
            # F1.2.2 (seconda capability: dominio web): vuoto per default - un utente avanzato
            # puo' limitare OPEN_URL a domini espliciti in config.json, come gia' fa con
            # allowed_filesystem_roots.
            allowed_web_domains=config.get("allowed_web_domains", []) or [],
            # F1.2.2 (terza/quarta capability: app e contatto): vuoti per default. Controllano la
            # stringa grezza, non il risultato della risoluzione (AppResolver/ContactBook) - vedi
            # il docstring di core/policy_engine.py per il limite dichiarato apertamente.
            allowed_apps=config.get("allowed_apps", []) or [],
            allowed_contacts=config.get("allowed_contacts", []) or [],
            # F1.2.2 (quinta capability: device Home Assistant): vuoto per default, stesso
            # principio/limite di allowed_apps/allowed_contacts.
            allowed_smart_devices=config.get("allowed_smart_devices", []) or [],
            # F1.2.2 (sesta capability: rete): vuoto per default - un utente avanzato puo'
            # limitare PING_HOST/TRACE_ROUTE/CHECK_WEBSITE_STATUS a host espliciti in
            # config.json, come gia' fa con allowed_web_domains.
            allowed_network_hosts=config.get("allowed_network_hosts", []) or [],
            # F1.4.2 (prima fetta - capability per ACCOUNT WINDOWS): {windows_user:
            # [intent, ...]} in config.json, vuoto per default - stesso principio di
            # device_blocked_intents sopra, ma per current_windows_user() (core/identity.py)
            # invece che per canale companion, utile solo se piu' account Windows condividono
            # questa installazione di Jake.
            windows_user_blocked_intents=config.get("windows_user_blocked_intents", {}) or {},
            # F1.2.3 (intersezione, seconda capability per AGENTE): {agent_name: [intent, ...]}
            # in config.json, vuoto per default - stesso principio di device_blocked_intents, ma
            # per TaskAgent.agent_name ("general"/"coding"/"research") invece che per canale
            # companion. Copre solo i tre agenti a passi, non il percorso diretto ne'
            # l'automazione (vedi il docstring di core/policy_engine.py per il limite dichiarato).
            agent_blocked_intents=config.get("agent_blocked_intents", {}) or {},
            # F1.2.2 (ottava e ultima capability: durata/finestra oraria): {intent:
            # ["HH:MM-HH:MM", ...]} in config.json, vuoto per default - un utente avanzato puo'
            # limitare un intent a certe ore del giorno (es. CONTROL_SMART_DEVICE solo 06:00-23:00),
            # stesso principio "nega per default" delle altre capability. Una finestra scritta
            # male in config.json solleva subito (PolicyEngine.__init__), non un fallimento
            # silenzioso all'avvio.
            time_restricted_intents=config.get("time_restricted_intents", {}) or {},
            # F1.2.3 (intersezione, quinta e ultima capability: SESSIONE): {session_id:
            # [intent, ...]} in config.json, vuoto per default - stesso principio di
            # device_blocked_intents, ma per l'id di una CONNESSIONE companion (core.
            # request_context.current_session_id(), nuovo a ogni DeviceRegistry.claim()) invece
            # che per l'identita' persistente del dispositivo. Utile per un permesso che deve
            # valere solo finche' quella specifica connessione resta viva, non per sempre.
            session_blocked_intents=config.get("session_blocked_intents", {}) or {},
        )
        # F1.2.5: collegato DOPO la creazione (self.agent/coding_agent/research_agent esistono
        # gia', self.policy_engine no, quando i tre TaskAgent vengono costruiti sopra) - senza
        # questo, il rollback di ciascun agente (core/agent.py::TaskAgent._rollback) non saprebbe
        # mai quali intent l'utente ha bloccato in config.json (vedi core/execution_safety.py::
        # rollback_effect). Stesso principio del commento qui sopra su RunWorkflowSkill: un
        # riferimento condiviso, assegnato esplicitamente a ognuno invece di sperare che il
        # costruttore lo ricevesse gia'.
        self.agent.policy_engine = self.policy_engine
        self.coding_agent.policy_engine = self.policy_engine
        self.research_agent.policy_engine = self.policy_engine

        # Jake proattivo (v1.2): di default stampa i promemoria scaduti; chi lancia Jake
        # (CLI, voce, tray, HUD) puo' sostituire questo callback per parlarli o mostrarli.
        self.reminder_manager = self.skill_registry.reminder_manager
        self.scheduler = ReminderScheduler(self.reminder_manager, on_due=self._default_on_reminder_due, interval_seconds=5)
        self.scheduler.start()

        # Contestualizzazione leggera del desktop (v2.0): il classificatore e il planner
        # ricevono le finestre/app usate di recente per capire richieste ambigue.
        self.desktop_context = DesktopContextTracker()
        self.desktop_context.start()
        self.router.primary_provider.context_provider = self.desktop_context.context_summary
        self.planner_provider.context_provider = self.desktop_context.context_summary
        # Cronologia recente nel classificatore (v4.0, Conversational Intelligence): stesso
        # buffer breve gia' condiviso con l'agente a passi (self.conversation_state), cosi' un
        # "e a Milano?" dopo "che tempo fa a Roma?" si puo' risolvere gia' in fase di
        # classificazione, senza dover ricadere sull'agente.
        if hasattr(self.router.primary_provider, "history_provider"):
            self.router.primary_provider.history_provider = self.conversation_state.get_short_term_history

        # F6 (Proactive Intelligence & Autonomy, core/autonomy_budget.py): limite condiviso al
        # numero di automazioni che possono partire da sole in una finestra di tempo - creato
        # qui (non dentro TriggerScheduler) cosi' reset_kill_switch() puo' azzerarlo insieme al
        # kill switch vero e proprio, vedi sotto.
        self.autonomy_budget = AutonomyBudget()

        # Jake proattivo (v3.0): un'automazione salvata puo' far partire se stessa.
        self.trigger_scheduler = TriggerScheduler(
            self.skill_registry.trigger_manager,
            self.skill_registry.workflow_manager,
            self.plan_executor,
            self.desktop_context,
            on_trigger=self._default_on_trigger_fired,
            policy_engine=self.policy_engine,
            autonomy_budget=self.autonomy_budget,
        )
        self.trigger_scheduler.start()

        # Jake proattivo (v3.2): nota da solo batteria scarica e disco quasi pieno, senza
        # che tu debba chiederglielo (vedi core/system_advisor.py). Disattivabile da config
        # per chi lo trova invadente o lavora su un fisso senza batteria.
        self.system_advisor = SystemAdvisor(
            on_advisory=self._default_on_advisory,
            enabled=bool(config.get("system_advisor_enabled", True)),
            todo_manager=self.skill_registry.todo_manager,
            memory_manager=self.memory_manager,
        )
        self.system_advisor.start()

        # Fucina di skill (v3.0): Jake si scrive nuove capacita' da solo.
        self.skill_forge = SkillForge(
            self.skill_registry, client=OllamaClient(timeout=240),
            model_provider=lambda: self.model, logger=self.logger,
            on_skill_installed=self._on_skill_installed,
            coder_model=config.get("coder_model") or None,
        )

        # Skill che hanno bisogno del core (non solo del registry): registrate qui.
        self.skill_registry.register_skill("LIST_MODELS", ListModelsSkill())
        self.skill_registry.register_skill(
            "SET_MODEL",
            SetModelSkill(
                updatable_targets=[self.router.primary_provider, self.planner_provider, self.context_summarizer],
                config=config,
            ),
        )
        for intent, skill in (
            ("LEARN_COMMAND", LearnCommandSkill(self)),
            ("LIST_LEARNED", ListLearnedSkill(self)),
            ("FORGET_LEARNED", ForgetLearnedSkill(self)),
            ("CORRECT_LAST", CorrectLastSkill(self)),
            ("REPEAT_LAST", RepeatLastSkill(self)),
            ("HELP", HelpSkill(self)),
            ("STOP_TALKING", StopTalkingSkill(self)),
            ("PAUSE_LISTENING", PauseListeningSkill(self)),
            ("SET_PRIVATE_MODE", PrivateModeSkill(self)),
            ("RESUME_INTERRUPTED_TASK", ResumeInterruptedTaskSkill(self)),
            ("KILL_SWITCH", KillSwitchSkill(self)),
            ("RESET_KILL_SWITCH", ResetKillSwitchSkill(self)),
            ("START_DICTATION", StartDictationSkill(self)),
            ("STOP_DICTATION", StopDictationSkill(self)),
            ("CREATE_SKILL", CreateSkillSkill(self.skill_forge)),
            ("LIST_CREATED_SKILLS", ListCreatedSkillsSkill(self.skill_forge)),
            ("DELETE_CREATED_SKILL", DeleteCreatedSkillSkill(self.skill_forge, self.learning)),
            ("SET_NOTIFICATION_MODE", SetNotificationModeSkill(self.notification_center)),
            ("GET_NOTIFICATION_MODE", GetNotificationModeSkill(self.notification_center)),
            ("UNDO_LAST_ACTION", UndoLastActionSkill(self)),
            ("APPROVE_PAIRING", ApprovePairingSkill(self.pairing_service, self.sync_keyring)),
        ):
            self.skill_registry.register_skill(intent, skill)

        # Modello di permessi centralizzato (v3.2, F1: core/policy_engine.py): ogni skill
        # DESTRUCTIVE o ADMIN che non gestisce gia' da sola una conferma su misura finisce qui
        # automaticamente, invece di dover essere elencata a mano. self.policy_engine e' lo
        # STESSO oggetto gia' passato per riferimento a trigger_scheduler (costruito sopra,
        # prima che tutte le skill fossero registrate): aggiornarlo qui lo aggiorna anche li'.
        # Se self.auth_gate non e' mai stato attivato, il gradino REQUIRE_AUTH (v5.4/5.5) resta
        # comunque gestito dal CONFIRM ordinario in _resolve_and_execute - l'auth vera scatta
        # solo quando auth_gate.enabled e' vero.
        self.policy_engine.sync_with_registry(self.skill_registry)

        # F1: buco reale trovato e corretto - RUN_WORKFLOW non passava MAI blocked_intents/
        # always_confirm_intents a PlanExecutor.execute() (vedi skills/workflow.py,
        # RunWorkflowSkill), che senza quei due argomenti non applica nessun controllo. Un
        # comando diretto ("esegui l'automazione X") su un'automazione con un passo DESTRUCTIVE/
        # ADMIN non self-confirming eseguiva quel passo senza alcuna conferma. Stesso schema
        # gia' usato per plan_executor.kill_switch/action_ledger sopra: iniettato DOPO la
        # costruzione, perche' SkillRegistry costruisce le skill prima che policy_engine esista.
        # UN riferimento solo (non piu' due insiemi separati): un intent installato piu' tardi
        # dalla Skill Forge (vedi _on_skill_installed) resta visto anche qui.
        run_workflow_skill = self.skill_registry.get_skill("RUN_WORKFLOW")
        if run_workflow_skill is not None:
            run_workflow_skill.policy_engine = self.policy_engine

        # F3.4.3/F3.8.5 (adozione, stesso identico schema di RUN_WORKFLOW appena sopra - vedi il
        # suo commento per il perche' dell'iniezione post-costruzione): senza questo,
        # RUN_COMPUTER_PROCEDURE eseguirebbe ogni passo con `self.policy_engine = None`
        # (il default dichiarato in RunComputerProcedureSkill.__init__) - `ComputerAgent.
        # _check_policy` tratta gia' onestamente un motore assente come "nessun controllo
        # possibile" (F3.4.3, comportamento invariato per chi non lo collega), ma qui il motore
        # ESISTE e va collegato, non lasciato assente per una dimenticanza.
        run_computer_procedure_skill = self.skill_registry.get_skill("RUN_COMPUTER_PROCEDURE")
        if run_computer_procedure_skill is not None:
            run_computer_procedure_skill.policy_engine = self.policy_engine
            run_computer_procedure_skill.event_bus = self.event_bus  # F3.3.6: diagnosi dell'inspector

        # F3.4.3 (adozione nelle skill di input): un click/tasto dichiarato sensibile ("effect":
        # send/submit/upload/delete/purchase, core/computer_use/sensitive_ui.py) consulta la policy
        # PRIMA di muovere mouse/tastiera. Stessa iniezione post-costruzione di sopra.
        for input_intent in ("CLICK_MOUSE", "TYPE_TEXT", "PRESS_KEY", "CLICK_TEXT", "CLICK_ELEMENT"):
            input_skill = self.skill_registry.get_skill(input_intent)
            if input_skill is not None:
                input_skill.policy_engine = self.policy_engine

        # Indici del recupero semantico: costruiti dopo che TUTTE le skill sono registrate.
        self.retriever.refresh()
        self.logger.info(
            "Jake 3.0 pronto: %d capacita', %d esempi (%d imparati), embedding %s",
            len(self.skill_registry.skills), len(self.example_store.all()),
            len(self.example_store.learned()), "attivi" if self.retriever.using_embeddings() else "NON disponibili (fallback lessicale)",
        )

        self.dialogue_runtime = DialogueRuntime()

        self.last_exchange = None  # {"text", "command", "response"}
        self.last_response = None
        self.last_route = None
        # F1.8.4 ("gestire shutdown con drain limitato"): conta quante chiamate ad answer() sono
        # DAVVERO in corso su un ALTRO thread in questo momento (il loop voce e core/companion_
        # server.py, un ThreadingHTTPServer con un thread per richiesta, condividono la stessa
        # istanza di JakeCore) - shutdown() aspetta che scenda a zero, entro un tetto, PRIMA di
        # fermare i componenti/salvare le cache, invece di procedere mentre una richiesta e'
        # ancora a meta' (che potrebbe usare un componente gia' fermato, o scrivere una cache
        # DOPO il salvataggio "finale" di shutdown()).
        self._in_flight_answers = 0
        self._in_flight_lock = threading.Lock()

    # ---- callback di default -------------------------------------------------------------

    def notify(self, kind: str, message: str, *, trace_id: str | None = None) -> str | None:
        """Punto unico da cui passa ogni notifica proattiva (promemoria/avviso/automazione)
        prima di essere presentata, sia in CLI (qui sotto) sia in voce (vedi WakeWordSession,
        core/voice/wake_word_session.py, che chiama questo stesso metodo): applica la modalita'
        di notifica corrente (v4.3). Restituisce il messaggio da presentare subito, o None se
        e' stato solo messo in coda per quando la modalita' tornera' a permetterlo.

        F1.7.2: `trace_id` (opzionale - solo un'automazione ne ha gia' uno reale, vedi
        `_default_on_trigger_fired`) collega l'evento HUD alle ricevute nel ledger che la stessa
        esecuzione ha gia' prodotto - senza, una notifica "Ho eseguito X" non aveva NESSUN modo
        di essere ricollegata a cosa e' successo davvero. Non propagato al percorso in coda
        (`NotificationCenter._queued`/`set_mode()`): una notifica rimandata riemerge oggi solo
        dentro il risultato testuale di `SET_NOTIFICATION_MODE`, mai come un secondo `HudEvent` -
        non c'e' un evento successivo a cui riattaccare il trace_id, dichiarato apertamente."""
        gated = self.notification_center.gate(kind, message)
        if gated is not None:
            self.event_bus.publish(HudEvent(EventType.NOTIFICATION, {"kind": kind, "text": gated}, trace_id=trace_id))
        return gated

    def _default_on_reminder_due(self, reminder: dict) -> None:
        message = self.notify("reminder", self.format_due_reminder(reminder))
        if message is None:
            return
        print(f"\nJake > {message}\nTu > ", end="", flush=True)

    @staticmethod
    def format_due_reminder(reminder: dict) -> str:
        if reminder.get("kind") == "timer":
            label = reminder.get("text") or "timer"
            return "Il timer è scaduto!" if label == "timer" else f"Il timer per {label} è scaduto!"
        return f"Promemoria: {reminder['text']}"

    def _default_on_advisory(self, message: str) -> None:
        gated = self.notify("advisory", message)
        if gated is None:
            return
        print(f"\nJake > {gated}\nTu > ", end="", flush=True)

    def _on_pairing_requested(self, challenge, requested_name: str, sync_public_key: dict | None) -> None:
        """F7.1.2 (Companion Mobile MVP): il lato "conferma sul PC" del pairing - collegato da
        core/companion_server.py::_handle_pairing_start subito dopo aver aperto la challenge.
        Imposta una richiesta di conferma sul canale LOCALE (voce/CLI: current_device_id() e'
        sempre None sul thread che gestisce /pairing/start, mai impostato per quell'endpoint -
        vedi core/companion_guard.py::EndpointClass.PAIRING) con lo STESSO meccanismo gia' usato
        per ogni altra azione ADMIN (_handle_confirmation/_finalize_pending_action): un "si'" (e
        la passphrase, se ne e' stata configurata una - APPROVE_PAIRING e' classificato ADMIN in
        core/risk.py) autorizza per davvero. Nessun dispositivo nasce da solo: se l'utente non e'
        li' a leggere, la challenge scade da sola dopo 5 minuti (core/pairing_service.py) - questo
        metodo non riprova ne' attende, e' chiamato una volta sola per richiesta.

        "text" e' deliberatamente VUOTO (a differenza di ogni altra azione che la popola per
        l'apprendimento, vedi _finalize_pending_action): imparare "pairing di X" come comando
        insegnato riprodurrebbe un challenge_id ormai scaduto/consumato, inutile e fuorviante."""
        label = (requested_name or "").strip() or "sconosciuto"
        self.conversation_state.set_pending_action({
            "intent": "APPROVE_PAIRING",
            "parameters": {
                "challenge_id": challenge.challenge_id, "requested_name": requested_name,
                "sync_public_key": sync_public_key,
            },
            "reason": "confirmation_required",
            "text": "",
        })
        message = self.notify(
            "pairing",
            f"Un nuovo dispositivo '{label}' chiede di collegarsi a Jake. Autorizzi il pairing? (scade tra 5 minuti)",
        )
        if message is None:
            return
        print(f"\nJake > {message}\nTu > ", end="", flush=True)

    def _default_on_trigger_fired(self, trigger: dict, outcome, total_steps: int) -> None:
        # F1.3.8: il percorso automatico (nessun turno di conversazione, nessun utente in
        # ascolto) e' quello che beneficia di piu' da questi eventi - senza, l'unico modo di
        # scoprire cosa un'automazione ha DAVVERO verificato o annullato era rileggere il ledger.
        self._publish_plan_outcome_effect_proof_events(outcome)
        summary = format_plan_outcome(outcome, total_steps, self.skill_registry)
        message = self.notify(
            "trigger", f"Ho eseguito automaticamente '{trigger.get('name')}':\n{summary}",
            trace_id=getattr(outcome, "trace_id", None),
        )
        if message is None:
            return
        print(f"\nJake > {message}\nTu > ", end="", flush=True)

    def _agent_context(self) -> str:
        parts = [part for part in (self.desktop_context.context_summary(), self.conversation_state.entities_summary()) if part]
        return " | ".join(parts)

    def _on_agent_step(self, step_index: int, description: str) -> None:
        self.session_hooks.call("set_state", "working", description)
        self.event_bus.publish(HudEvent(EventType.AGENT_STEP, {"step": step_index, "description": description}))

    def _publish_plan_outcome_effect_proof_events(self, outcome) -> None:
        """Estrae da un `PlanOutcome` (core/plan_executor.py) le stesse due liste che
        `_publish_effect_proof_events` sotto si aspetta, condivisa dai due chiamanti che
        ricevono un PlanOutcome (`_try_plan`, `_default_on_trigger_fired`) invece di ripetere la
        stessa estrazione due volte. `outcome.trace_id` (F1.7.2) e' gia' lo stesso che correla
        ogni passo del piano nel ledger - propagato qui (F4.1.1) cosi' l'evento sul bus porta la
        stessa correlazione, non solo la ricevuta scritta su disco."""
        verified_steps = [
            (step_outcome.step.intent, step_outcome.verified)
            for step_outcome in (*outcome.completed, *([outcome.stopped_step] if outcome.stopped_step else []))
            if step_outcome.verified is not None
        ]
        self._publish_effect_proof_events(
            verified_steps, [step_outcome.step.intent for step_outcome in outcome.rolled_back],
            trace_id=outcome.trace_id,
        )

    def _publish_effect_proof_events(
        self, verified_steps: list[tuple[str, str]], rolled_back_intents: list[str], *, trace_id: str | None = None,
    ) -> None:
        """F1.3.8 ("esporre undo e prove a HUD/companion tramite eventi versionati"): prima di
        questo, un rollback (core/execution_safety.py::rollback_effect) o una verifica
        indipendente dell'effetto (F1.3.3, verify_effect) erano visibili SOLO nel ledger
        (data/jake_ledger.jsonl) - un HUD o un'app companion non aveva modo di saperlo in tempo
        reale, solo rileggendo il ledger dopo. Chiamato sia dal percorso agente
        (core/agent.py::AgentOutcome) sia dal percorso piano (core/plan_executor.py::
        PlanOutcome), che espongono la stessa informazione con forme leggermente diverse -
        l'estrazione resta al chiamante, qui solo la pubblicazione condivisa. Nessun evento
        quando non c'e' nulla da riportare (nessun intent verificabile in questo turno, nessun
        rollback) - non aggiunge rumore al caso comune."""
        for intent in rolled_back_intents:
            self.event_bus.publish(HudEvent(EventType.UNDO, {"intent": intent}, trace_id=trace_id))
        for intent, verified in verified_steps:
            self.event_bus.publish(HudEvent(EventType.VERIFICATION, {"intent": intent, "verified": verified}, trace_id=trace_id))

    def _on_agent_step_completed(self, outcome) -> None:
        """F1.8.4 ("checkpoint... da cui riprendere"): collegato a `on_step_completed` di
        ciascuno dei tre TaskAgent (general/coding/research, vedi __init__) - chiamato dopo OGNI
        passo che l'agente completa (riuscito o fallito), sovrascrive il checkpoint sul disco con
        il progresso aggiornato. `outcome.trace_id`/`.request`/`.agent_name` sono popolati da
        `TaskAgent.run()` stesso (F1.8.4) - `agent_name` conta DAVVERO qui (non solo "general"
        come nella prima fetta): un checkpoint salvato con l'agente sbagliato riprenderebbe il
        compito con la persona/gli strumenti fissi sbagliati (vedi core/orchestrator.py). Solo
        intent/parametri/esito di ogni passo vengono salvati (mai l'intero `SkillResult` - i dati
        grezzi di una skill potrebbero contenere contenuto esterno/sensibile che non ha senso
        duplicare su un secondo file, il ledger e' gia' la fonte di verita' per quello)."""
        if outcome.trace_id is None or outcome.request is None or outcome.agent_name is None:
            return
        checkpoint = AgentCheckpoint(
            trace_id=outcome.trace_id, agent_name=outcome.agent_name, request=outcome.request,
            completed_steps=[
                {
                    "intent": step.intent, "parameters": step.parameters,
                    "success": bool(step.result and step.result.success),
                }
                for step in outcome.steps
            ],
        )
        try:
            self.agent_checkpoints.save(checkpoint)
        except OSError:
            self.logger.exception("Errore salvando il checkpoint del compito in corso")

        # F6.7 (adozione, stesso hook della riga sopra): segue il compito in silenzio (F6.7.1,
        # nessun evento a ogni passo) e salva lo snapshot dei compiti ancora in corso - un
        # monitor RUNNING sopravvive a un'interruzione anomala (F6.7.7), senza alcuna ripresa
        # automatica (vedi il docstring di core/task_notification_bridge.py). Un errore qui
        # (bridge o disco) non deve MAI fermare il compito in corso, stesso principio del
        # checkpoint sopra.
        try:
            self.task_bridge.track_progress(outcome, session_id=current_session_id(), device_id=current_device_id())
            self.task_monitor_store.save(self.task_monitor)
        except Exception:
            self.logger.exception("Errore aggiornando il task monitor del compito in corso")

    def _publish_task_event(self, action) -> None:
        """F6.3/F6.7: avvolge OGNI chiamata al ponte task monitor/notifiche usata da _run_agent (decisione
        richiesta, fine del compito) - un errore qui (bridge, notification_policy, disco) non deve MAI
        impedire a Jake di rispondere, stesso principio gia' applicato a _on_agent_step_completed sopra."""
        try:
            action()
            self.task_monitor_store.save(self.task_monitor)
        except Exception:
            self.logger.exception("Errore nel ponte task monitor / notifiche")

    def _on_skill_installed(self, draft) -> None:
        # F1: always_confirm_intents/require_auth_intents (vedi sopra) sono popolati una sola
        # volta in __init__, leggendo self.skill_registry.skills COM'ERA in quel momento - una
        # skill installata piu' tardi dalla Skill Forge non ci finiva mai dentro. risk_of()
        # ricade su ADMIN per un intent non censito in core/risk.py (vedi il modulo), quindi
        # needs_central_confirmation()/needs_central_auth() sarebbero comunque vere per lei -
        # ma senza questo aggiornamento _resolve_and_execute non lo saprebbe mai ed eseguirebbe
        # la skill appena creata (codice scritto da un modello, non rivisto da un umano) SENZA
        # alcuna conferma ne' autenticazione al primo utilizzo: esattamente il tipo di buco che
        # il censimento del rischio dovrebbe rendere impossibile. Scoperto rileggendo il ciclo
        # di vita di una skill forgiata, non da un test che falliva. Stesso metodo usato per il
        # censimento iniziale in __init__ (core/policy_engine.py, PolicyEngine.sync_with_registry):
        # un solo posto invece di due copie della stessa logica che potrebbero divergere.
        self.policy_engine.register_intent(draft.intent)
        self.retriever.refresh()
        for example in draft.examples:
            try:
                self.learning.teach(self.normalizer.normalize(example), draft.intent, {}, source="forge")
            except Exception:
                self.logger.exception("Errore registrando gli esempi della skill %s", draft.intent)

    # ---- API pubblica --------------------------------------------------------------------

    def answer(self, text: str) -> str:
        return self._run_in_current_profile(lambda: self._answer_in_profile(text or ""))

    def _answer_in_profile(self, raw_text: str) -> str:

        # F2.6.4:
        # una passphrase NON deve attraversare normalizzatore, NLU, memoria,
        # cronologia, EventBus o logger.
        pending = self.conversation_state.get_pending_action()

        if pending is not None and pending.get("reason") == "auth_required":
            return self._answer_auth_turn(raw_text)

        normalized = self.normalizer.normalize(raw_text)

        if not normalized:
            return "Non ho sentito nulla."

        return self._answer_counted(normalized, raw_text)


    def _run_in_current_profile(self, callback):
        """
        Esegue callback nello spazio memoria/conversazione del profilo vocale
        corrente, quando F2.7 multiutente è attivo.
        """
        profile_manager = getattr(self, "profile_manager", None)

        if profile_manager is None:
            return callback()

        with self._profile_turn_lock:
            profile_id = current_speaker_profile_id()

            if profile_id is None:
                return callback()

            try:
                namespace = profile_manager.namespace(profile_id)
            except ProfileError:
                return callback()

            default_db_path = self.memory_manager.db_path

            self.memory_manager.switch_database(namespace.memory_db_path)
            self.conversation_state.swap_state(namespace.conversation)
            self._active_profile_namespace = namespace

            try:
                return callback()
            finally:
                self.conversation_state.swap_state(namespace.conversation)
                self.memory_manager.switch_database(default_db_path)
                self._active_profile_namespace = None


    def _answer_auth_turn(self, raw_secret: str) -> str:
        """
        Percorso speciale per un segreto di autenticazione.

        Il segreto:
        - non viene normalizzato;
        - non finisce in conversation_state;
        - non finisce in MemoryManager;
        - non viene pubblicato come USER_MESSAGE;
        - non viene loggato.
        """
        with self._in_flight_lock:
            self._in_flight_answers += 1

        try:
            action = self.conversation_state.take_pending_action()

            if action is None:
                return "Non c'è più nessuna autenticazione in attesa."

            # Difesa da una race: non trattare mai come password una pending
            # che nel frattempo è diventata qualcos'altro.
            if action.get("reason") != "auth_required":
                self.conversation_state.set_pending_action(action)
                return "La richiesta in attesa è cambiata. Ripeti il comando."

            response = self._handle_auth_secret(raw_secret, action)

            if response and response != self.EXIT_SENTINEL:
                self.last_response = response

                # Pubblica solo la risposta di Jake.
                # MAI il segreto dell'utente.
                if not self.private_mode:
                    self.event_bus.publish(
                        HudEvent(
                            EventType.JAKE_MESSAGE,
                            {"text": response},
                        )
                    )

            return response

        finally:
            with self._in_flight_lock:
                self._in_flight_answers -= 1

    def _answer_counted(self, text: str, raw_text: str) -> str:
        # F1.8.4 ("drain limitato"): conta questa chiamata come "in corso" da qui a return -
        # incrementato PRIMA di qualunque lavoro vero (skill/agente/piano), decrementato in un
        # finally cosi' shutdown() sa sempre quante chiamate stanno ancora usando i componenti
        # che sta per fermare, anche se questo turno solleva un'eccezione imprevista.
        with self._in_flight_lock:
            self._in_flight_answers += 1
        try:
            return self._answer_inner(text, raw_text)
        finally:
            with self._in_flight_lock:
                self._in_flight_answers -= 1

    def _answer_inner(self, text: str, raw_text: str) -> str:
        text = self._resolve_pronouns(text)
        # F1.5.2: azzerato PRIMA di processare questo turno, cosi' un valore rimasto da un turno
        # precedente (es. un turno che non passa da _execute_command - chitchat, agente,
        # conferma) non finisce per etichettare per errore la risposta di QUESTO turno.
        source_intent_token = set_current_command_source_intent(None)
        try:
            response = self._process(text)
        except TurnCancelled:
            reset_current_command_source_intent(source_intent_token)
            raise
        except Exception:
            # Nessuna eccezione imprevista deve mai far crashare Jake: viene registrata nel log
            # e riportata all'utente con un messaggio comprensibile invece di terminare il processo.
            self.logger.exception("Errore imprevisto elaborando: %s", text)
            response = "Mi dispiace, si è verificato un errore imprevisto. L'ho registrato nel log."
            self.event_bus.publish(HudEvent(EventType.ERROR, {"detail": "errore imprevisto"}))
        if current_turn_cancelled():
            # "Jake, basta" arrivato mentre il turno lavorava: la sua risposta e' vecchia. Non entra
            # in cronologia/memoria/HUD (il prossimo turno non deve riferirsi a qualcosa che l'utente
            # non ha mai sentito) e non parte il riassunto della cronologia (una chiamata al modello).
            reset_current_command_source_intent(source_intent_token)
            self.logger.info("Turno annullato dall'utente: risposta scartata (%s)", text)
            raise TurnCancelled()

        if response is None:
            response = ""
        # La cronologia in RAM (self.conversation_state) resta attiva anche in modalita' privata
        # (v5.6, Privacy Engine): serve alla sessione corrente per pronomi/riferimenti e sparisce
        # comunque al riavvio. Cio' che la modalita' privata sospende e' la scrittura su DISCO E
        # la trasmissione (v4.9.1: chi e' iscritto a self.event_bus, es. un HUD o un'app
        # companion, non deve vedere in diretta uno scambio marcato come privato): uno scambio in
        # modalita' privata non deve lasciare traccia da nessuna parte, ne' su disco ne' altrove.
        self.conversation_state.add_turn("user", text)
        if response != self.EXIT_SENTINEL:
            # F1.5.2: la versione salvata in cronologia porta il marcatore strutturale quando
            # questa risposta viene da un comando diretto che ha restituito contenuto esterno
            # (wrap_external_content e' un no-op se current_command_source_intent() e' None o
            # non e' un intent censito - vedi core/taint.py). Il valore RESTITUITO all'utente
            # (`response`, sotto e ovunque altro venga usato) resta invece SENZA marcatore: serve
            # al modello in un prompt futuro, mai alla persona che legge/ascolta ora.
            self.conversation_state.add_turn("jake", wrap_external_content(current_command_source_intent(), response))
            if response:
                self.last_response = response
        reset_current_command_source_intent(source_intent_token)
        if self.private_mode:
            self.logger.info("Scambio in modalità privata: non registrato.")
        else:
            self.event_bus.publish(HudEvent(EventType.USER_MESSAGE, {"text": text}))
            if response and response != self.EXIT_SENTINEL:
                self.event_bus.publish(HudEvent(EventType.JAKE_MESSAGE, {"text": response}))
            self.memory_manager.log_turn("user", text)
            if response != self.EXIT_SENTINEL:
                self.memory_manager.log_turn("jake", response)
            if raw_text.strip().lower() != text:
                self.logger.info("Tu: %s (normalizzato da: %s) | Jake: %s", text, raw_text.strip(), response)
            else:
                self.logger.info("Tu: %s | Jake: %s", text, response)
            # No-op finche' la cronologia resta sotto soglia: il riassunto scatta solo occasionalmente.
            self.memory_manager.summarize_old_history(self.context_summarizer)
        return response

    def resolve_command(self, text: str) -> Command:
        """Classifica un testo senza eseguirlo (usato dalle skill di apprendimento)."""
        return self.router.detect_intent(self.normalizer.normalize(text))

    def describe_command(self, command: Command) -> str:
        """Descrizione parlabile di un comando: 'apro spotify', 'eseguo l'automazione X'."""
        intent = command.intent
        parameters = command.parameters or {}
        if intent == "RUN_WORKFLOW":
            return f"eseguo l'automazione «{parameters.get('name', '')}»"
        skill = self.skill_registry.get_skill(intent)
        description = (getattr(skill, "metadata", {}) or {}).get("description", "") or intent
        description = description.split(". ")[0].split(" Usalo")[0].rstrip(".")
        values = ", ".join(f"{k}: {v}" for k, v in parameters.items() if v not in (None, "", False))
        return f"{description[0].lower() + description[1:]}" + (f" ({values})" if values else "")

    def _get_dialogue_runtime(self) -> DialogueRuntime:
        """Keep dialogue state available on cores constructed without __init__."""
        runtime = getattr(self, "dialogue_runtime", None)
        if runtime is None:
            runtime = DialogueRuntime()
            self.dialogue_runtime = runtime
        return runtime

    def _dialogue_scope(self) -> str:
        """
        Scope conversazionale per F2.6.

        Un profilo vocale riconosciuto ha priorità sul dispositivo.
        """
        profile_id = current_speaker_profile_id()

        if profile_id:
            return f"profile:{profile_id}"

        device_id = current_device_id()

        if device_id:
            return f"device:{device_id}"

        return "local"


    def _set_dialogue_outcome(
        self,
        *,
        action_id: str,
        text: str,
        command: Command,
        status: str,
        reversible: bool = False,
        scope: str | None = None,
    ) -> None:
        # CORRECT_LAST è un meta-comando.
        # Il vero turno corretto viene registrato separatamente.
        if command.intent == "CORRECT_LAST":
            return

        effective_scope = scope or self._dialogue_scope()

        existing = self._get_dialogue_runtime().turn(action_id)

        if existing is None:
            self._get_dialogue_runtime().record_turn(
                scope=effective_scope,
                action_id=action_id,
                heard=text,
                intent=command.intent,
                parameters=command.parameters or {},
                risk=risk_of(command.intent),
                status=status,
                reversible=reversible,
            )
            return

        self._get_dialogue_runtime().update_turn(
            action_id,
            status=status,
            reversible=reversible,
        )


    def _cancel_dialogue_action(self, action: dict) -> None:
        action_id = action.get("action_id")

        if not action_id:
            return

        self._get_dialogue_runtime().update_turn(
            action_id,
            status="cancelled",
        )

        self._finish_correction_learning(
            action_id,
            verified=False,
        )


    def _finish_correction_learning(
        self,
        action_id: str,
        *,
        verified: bool,
    ) -> None:
        source_turn, learnable = (
            self._get_dialogue_runtime().finish_correction_learning(
                action_id,
                verified=verified,
            )
        )

        if source_turn is None or learnable is None:
            return

        previous_command = Command(
            source_turn.intent,
            dict(source_turn.parameters),
        )

        corrected_command = Command(
            learnable.intent,
            dict(learnable.parameters),
        )

        self.learning.correct(
            learnable.heard,
            previous_command,
            corrected_command,
        )

        self.logger.info(
            "Correzione verificata: %r -> %s %s",
            learnable.heard,
            corrected_command.intent,
            corrected_command.parameters,
        )


    def _execute_corrected_command(
        self,
        source_action_id: str,
        corrected_text: str,
        command: Command,
    ) -> str:
        corrected_action_id = new_action_id()

        source_turn = self._get_dialogue_runtime().turn(source_action_id)

        # Conserva la protezione già esistente:
        # una frase totalmente diversa non deve insegnare una falsa associazione.
        related = False

        if source_turn is not None:
            related = (
                lexical_similarity(
                    source_turn.heard,
                    corrected_text,
                ) >= 0.25
                or source_turn.intent
                in ("UNKNOWN", "CHITCHAT", "ASK_QUESTION")
            )

        if related:
            self._get_dialogue_runtime().begin_correction_learning(
                source_action_id=source_action_id,
                execution_action_id=corrected_action_id,
                corrected_text=corrected_text,
                intent=command.intent,
                parameters=command.parameters or {},
            )

        return self._execute_command(
            corrected_text,
            command,
            learn=False,
            action_id=corrected_action_id,
        )


    def _what_did_you_hear(self) -> str:
        turn = self._get_dialogue_runtime().last_turn(
            self._dialogue_scope()
        )

        if turn is None:
            return "Non ho ancora un comando precedente da riportarti."

        return f'Ho sentito: "{turn.heard}".'

    def _try_ordinal_reference(self, text: str) -> str | None:
        """
        F2.6.1:
        'apri il primo e il terzo' sui risultati dell'ultima ricerca.
        """
        first_word = (
            text.split(maxsplit=1)[0]
            if text.strip()
            else ""
        )

        if first_word not in {
            "apri",
            "aprimi",
            "mostra",
            "mostrami",
        }:
            return None

        results = self.conversation_state.get_last_search_results()

        if not results:
            return None

        indexes = resolve_ordinals(
            text,
            len(results),
        )

        if indexes is None:
            return None

        responses: list[str] = []

        for index in indexes:
            response = self._execute_command(
                text,
                Command(
                    "OPEN_SEARCH_RESULT",
                    {"index": index + 1},
                ),
                learn=False,
            )

            responses.append(response)

        return "\n".join(
            response
            for response in responses
            if response
        )

    def apply_correction(self, request: str) -> str:
        """
        F2.6.3/F2.6.7.

        La correzione NON viene più eseguita alla cieca.

        Possibili casi:
        - niente eseguito -> rerun;
        - READ_ONLY -> rerun;
        - locale reversibile -> undo + rerun;
        - esterna/distruttiva/admin/non reversibile -> chiedi.
        """
        scope = self._dialogue_scope()
        runtime = self._get_dialogue_runtime()

        # Compatibilità con sessioni/test creati prima dell'introduzione
        # del DialogueRuntime: importa lazy l'ultimo exchange se il runtime
        # non ne conosce ancora nessuno.
        if runtime.last_turn(scope) is None:
            previous = getattr(self, "last_exchange", None)

            if previous and previous.get("text"):
                previous_command = previous.get("command")

                if previous_command is None:
                    previous_command = Command("UNKNOWN", {})

                previous_action_id = (
                    previous.get("action_id")
                    or new_action_id()
                )

                previous_intent = previous_command.intent

                # Un vecchio UNKNOWN non rappresenta un side effect già avvenuto:
                # Jake non aveva capito/eseguito il comando, quindi una correzione
                # può essere eseguita normalmente.
                if previous_intent == "UNKNOWN":
                    previous_status = "failed"
                else:
                    previous_status = "executed"

                runtime.record_turn(
                    scope=scope,
                    action_id=previous_action_id,
                    heard=previous["text"],
                    intent=previous_intent,
                    parameters=previous_command.parameters or {},
                    risk=risk_of(previous_intent),
                    status=previous_status,
                    reversible=False,
)

                # Migra anche last_exchange alla nuova forma.
                previous["action_id"] = previous_action_id

        (
            source_action_id,
            plan,
            source_turn,
        ) = runtime.plan_correction(scope)

        if source_action_id is not None and plan.action == "unknown":
            return (
                "Non ho un comando precedente sicuro da "
                "correggere. Ripeti direttamente la richiesta."
            )

        if source_turn is not None and plan.action == "ask":
            return (
                "Il comando precedente è già stato eseguito "
                f"ed era un'azione {source_turn.risk.value}. "
                "Non la ripeto e non provo ad annullarla "
                "automaticamente. Dimmi esplicitamente cosa "
                "vuoi fare adesso."
            )

        corrected_text = self.normalizer.normalize(
            request
        )

        corrected_text = self._resolve_pronouns(
            corrected_text
        )

        if not corrected_text:
            return "Dimmi cosa intendevi."

        command = self.router.detect_intent(
            corrected_text
        )

        if command.intent == "UNKNOWN":
            # Anche il fallback agente puo' eseguire azioni: non deve saltare l'undo.
            if source_turn is not None and plan.action != "rerun":
                return "Non ho capito nemmeno la correzione: prova a dirlo in un altro modo."
            agent_response = self._run_agent(
                corrected_text
            )

            if agent_response != self.NO_PLAN:
                return agent_response

            return (
                "Non ho capito nemmeno la correzione: "
                "prova a dirlo in un altro modo."
            )

        if (
            source_action_id is None
            or source_turn is None
            or plan.action == "unknown"
        ):
            corrected_text = self.normalizer.normalize(request)
            corrected_text = self._resolve_pronouns(corrected_text)

            command = self.router.detect_intent(corrected_text)

            if command.intent == "UNKNOWN":
                agent_response = self._run_agent(corrected_text)

                if agent_response != self.NO_PLAN:
                    return agent_response

                return (
                    "Non ho capito nemmeno la correzione: "
                    "prova a dirlo in un altro modo."
                )

            # Non c'è un turno precedente da correggere:
            # esegui normalmente, ma ovviamente non imparare
            # alcuna associazione vecchio -> nuovo.
            return self._execute_command(
                corrected_text,
                command,
                learn=False,
            )

        if plan.action == "rerun":
            return self._execute_corrected_command(
                source_action_id,
                corrected_text,
                command,
            )

        if plan.action == "undo_then_rerun":
            descriptor = self.undo_store.get(
                source_action_id
            )

            if descriptor is None:
                return (
                    "L'azione precedente risulta reversibile, "
                    "ma il suo undo non è più disponibile. "
                    "Non eseguo automaticamente la correzione."
                )

            undo_action_id = new_action_id()

            self.conversation_state.set_pending_action(
                {
                    "intent": (
                        descriptor.compensating_intent
                    ),
                    "parameters": dict(
                        descriptor.compensating_parameters
                    ),
                    "reason": "correction_undo",

                    "text": source_turn.heard,

                    "trace_id": new_trace_id(),
                    "action_id": undo_action_id,
                    "dialogue_scope": scope,

                    "undo_source_action_id": (
                        source_action_id
                    ),

                    "correction_after": {
                        "source_action_id": (
                            source_action_id
                        ),
                        "corrected_text": (
                            corrected_text
                        ),
                        "intent": command.intent,
                        "parameters": dict(
                            command.parameters or {}
                        ),
                    },
                }
            )

            return (
                "Ho già eseguito il comando precedente. "
                "Posso annullarlo e, solo se l'annullamento "
                "riesce, eseguire la versione corretta. "
                "Confermi?"
            )

        # ask
        return (
            "Il comando precedente è già stato eseguito "
            f"ed era un'azione {source_turn.risk.value}. "
            "Non la ripeto e non provo ad annullarla "
            "automaticamente. Dimmi esplicitamente cosa "
            "vuoi fare adesso."
        )

    # ---- pipeline ------------------------------------------------------------------------

    def _process(self, text: str) -> str:
        pending_action = self.conversation_state.take_pending_action()

        if pending_action is not None:
            return self._handle_confirmation(
                text,
                pending_action,
            )

        if intent_patterns.is_exit(text):
            return self.EXIT_SENTINEL

        # F2.6.2
        if intent_patterns.is_heard_query(text):
            return self._what_did_you_hear()

        meta = self._match_meta_command(text)

        if meta is not None:
            self.logger.info(
                "Meta-comando: %s %s",
                meta.intent,
                meta.parameters,
            )

            return self._execute_command(
                text,
                meta,
                learn=False,
            )

        # F2.6.1 — ellissi
        last_turn = self._get_dialogue_runtime().last_turn(
            self._dialogue_scope()
        )

        if (
            last_turn is not None
            and last_turn.status == "executed"
        ):
            ellipsis = resolve_ellipsis(
                text,
                last_turn.intent,
                last_turn.parameters,
            )

            if ellipsis is not None:
                return self._execute_command(
                    text,
                    Command(
                        ellipsis.intent,
                        ellipsis.parameters,
                    ),
                )

        # F2.6.1 — ordinali sui risultati recenti
        ordinal_response = self._try_ordinal_reference(text)

        if ordinal_response is not None:
            return ordinal_response

        taught = self.example_store.find_exact(text)

        if (
            taught is not None
            and taught.source in ("taught", "corrected")
        ):
            self.last_route = "exact"

            self.logger.info(
                "Comando insegnato: %s %s",
                taught.intent,
                taught.parameters,
            )

            return self._execute_command(
                text,
                Command(
                    taught.intent,
                    dict(taught.parameters),
                ),
                learn=False,
            )

        if len(text.split()) <= 4:
            quick = chitchat.reply(text)

            if quick is not None:
                self.learning.commit_pending()

                self._remember_exchange(
                    text,
                    Command(
                        "CHITCHAT",
                        {"text": text},
                    ),
                    quick,
                )

                return quick

        if intent_patterns.is_multi_step_request(text):
            agent_response = self._run_agent(text)

            if agent_response != self.NO_PLAN:
                return agent_response

        command = self.router.detect_intent(text)
        self.last_route = self.router.last_route

        self.logger.info(
            "Instradamento: %s -> %s %s",
            self.last_route,
            command.intent,
            command.parameters,
        )

        if command.intent == "UNKNOWN":
            return self._handle_unknown(text)

        return self._execute_command(
            text,
            command,
        )

    def _resolve_pronouns(self, text: str) -> str:
        return intent_patterns.resolve_pronouns(text, self.conversation_state.get_entities())

    def _authorize_command(self, resolved: Command) -> tuple[Command, SkillResult | None, str]:
        """Gate condiviso da comando diretto, agente e ripresa dopo il consenso (F1.2.5).

        Non riscrive l'intent e non esegue skill: restituisce un comando autorizzato oppure
        l'esito BLOCK/CONFIRM/REQUIRE_AUTH. Una conferma precedente non congela la policy.
        """
        # F1.1.6 (pilota di adozione del contratto, F1.1.2): ActionProposal.for_intent() ricava il
        # rischio da risk_of() (la stessa fonte gia' usata da PolicyEngine/SkillRegistry, vedi il
        # docstring di ActionProposal) e proposal.parameters e' una COPIA di resolved.parameters -
        # decide_interactive() qui sotto legge valori identici a prima, nessun cambio di
        # comportamento. resolved.parameters (l'originale, non la copia) resta quello davvero
        # eseguito piu' sotto: il proposal descrive l'intenzione, non sostituisce l'esecuzione.
        proposal = ActionProposal.for_intent(resolved.intent, resolved.parameters, "user")
        validate_action_proposal(proposal)
        # F2.7.4: la voce seleziona uno spazio dei nomi, non concede privilegi. Il tetto del
        # profilo può soltanto restringere la policy globale e viene controllato prima di
        # qualunque prompt o esecuzione. L'attributo esiste soltanto durante il turno profilato,
        # protetto da _profile_turn_lock; i JakeCore minimali dei test e le installazioni senza
        # multiutente continuano a non avere alcun comportamento aggiuntivo.
        profile_namespace = getattr(self, "_active_profile_namespace", None)
        if profile_namespace is not None and not profile_namespace.permits(risk_of(resolved.intent)):
            return (
                resolved,
                SkillResult(success=False, data={}, error="POLICY_BLOCKED"),
                "profile_risk_ceiling",
            )
        decision, policy_reason = self.policy_engine.decide_interactive_with_reason(proposal.intent, proposal.parameters)
        if decision == PolicyDecision.BLOCK:
            return resolved, SkillResult(success=False, data={}, error="POLICY_BLOCKED"), policy_reason
        if decision == PolicyDecision.REQUIRE_AUTH:
            # F1: Windows Hello tentato PRIMA della passphrase quando e' attivo - un fattore che
            # non passa dalla voce (vedi core/auth_gate.py) e non richiede un secondo turno di
            # conversazione. Se verifica, l'azione prosegue SUBITO (stesso turno): niente
            # AUTH_REQUIRED, niente "ripeti la passphrase". confirmed=True insieme ad
            # authenticated=True: un intent ADMIN e' quasi sempre anche in always_confirm_intents
            # (needs_central_confirmation, vedi core/risk.py) e senza questo il passo successivo
            # chiederebbe comunque un "confermi?" spoglio - ma verificare la propria impronta/
            # volto/PIN specificamente per QUESTA azione (reason e' la descrizione dell'azione,
            # non un messaggio generico) e' gia' di per se' un consenso esplicito, non solo una
            # prova di identita': chiederne un altro sarebbe ridondante, non piu' sicuro. Se
            # Windows Hello e' spento, non disponibile, o l'utente annulla il prompt, si ripiega
            # sul flusso passphrase gia' esistente, invariato.
            if self.auth_gate.verify_with_windows_hello(self.describe_command(resolved)):
                resolved = Command(resolved.intent, {
                    **(resolved.parameters or {}), "authenticated": True, "authenticated_via": "windows_hello", "confirmed": True,
                })
                # Il prompt nativo puo' durare: controlla anche una revoca intervenuta mentre
                # era aperto, usando gli stessi segnali appena verificati (nessun nuovo prompt).
                return self._authorize_command(resolved)
            return resolved, SkillResult(
                success=False,
                data={
                    "message": f"Serve l'autenticazione: {self.describe_command(resolved)}. Di' la passphrase per confermare.",
                    "confirm_parameters": {**(resolved.parameters or {}), "authenticated": True, "authenticated_via": "passphrase"},
                    "confirm_intent": resolved.intent,
                },
                error="AUTH_REQUIRED",
            ), policy_reason
        if decision == PolicyDecision.CONFIRM:
            return resolved, SkillResult(
                success=False,
                data={
                    "message": f"Confermi: {self.describe_command(resolved)}?",
                    "confirm_parameters": {**(resolved.parameters or {}), "confirmed": True},
                    "confirm_intent": resolved.intent,
                },
                error="CONFIRMATION_REQUIRED",
            ), policy_reason
        # F2.6.6 ("chiedere conferma dipende dall'impatto E dalla certezza del riconoscimento"):
        # la policy da sola non sa se questo comando e' arrivato per voce ne' quanto Jake fosse
        # sicuro di averlo capito bene (deliberato: PolicyEngine resta cieco alla voce, vedi il
        # docstring di POLICY_REASON_LOW_RECOGNITION_CONFIDENCE) - qui, DOPO che la policy ha gia'
        # detto "nessuna conferma necessaria", si aggiunge (mai si toglie) una conferma quando la
        # trascrizione vocale di QUESTO turno era poco sicura per il rischio dell'intent
        # (core.voice.dialogue.needs_confirmation, stessa soglia gia' provata a livello di
        # libreria in F2.6). `current_stt_confidence()` e' None per ogni comando non vocale (o un
        # provider senza confidenza) - nessun cambio di comportamento in quel caso, il caso di
        # ogni test/chiamante esistente prima di questo incremento.
        confidence = current_stt_confidence()
        if confidence is not None and needs_confirmation(confidence, risk_of(resolved.intent)).required:
            return resolved, SkillResult(
                success=False,
                data={
                    "message": f"Non sono sicuro di aver capito bene: {self.describe_command(resolved)}?",
                    "confirm_parameters": {**(resolved.parameters or {}), "confirmed": True},
                    "confirm_intent": resolved.intent,
                },
                error="CONFIRMATION_REQUIRED",
            ), POLICY_REASON_LOW_RECOGNITION_CONFIDENCE
        return resolved, None, policy_reason

    def _resolve_and_execute(self, command: Command, action_id: str | None = None) -> ActionExecution:
        """Riscrive, autorizza ed esegue per comando diretto e agente.

        Restituisce comando eseguito, risultato, nota e motivazione per-azione, non stato globale.
        I fallback hanno un gate proprio;
        la ripresa di un consenso usa invece _authorize_command senza cambiare il bersaglio.

        F1.3.4 (adozione, percorso a comando diretto): action_id opzionale, passato a
        SkillRegistry.execute() cosi' un DELETE_PATH puo' avere uno snapshot del contenuto
        catturato prima della cancellazione vera (vedi core/action_snapshot.py). None (il
        default) preserva il comportamento di sempre per il percorso dell'agente, che passa da
        qui (vedi executor di TaskAgent/PlanExecutor in __init__) senza ancora passarne uno -
        prossima fetta dichiarata, non fatta in questo incremento.
        """
        resolved = fallbacks.pre_execution_rewrite(command, self.skill_registry)
        resolved, policy_result, policy_reason = self._authorize_command(resolved)
        if policy_result is not None:
            return ActionExecution(resolved, policy_result, policy_reason=policy_reason)
        result = self.skill_registry.execute(
            resolved.intent, resolved.parameters, policy_engine=self.policy_engine,
            action_id=action_id, private=self.private_mode,
        )
        if result is not None and not result.success and result.error != "CONFIRMATION_REQUIRED":
            alt_command, note = fallbacks.alternative_for(resolved, result, self.skill_registry)
            if alt_command is not None:
                # F1 (difesa in profondita', non un buco gia' sfruttabile oggi): core/fallbacks.py
                # ::alternative_for restituisce oggi solo alternative fisse a basso rischio
                # (OPEN_URL/OPEN_APP/CLICK_ELEMENT/CLOSE_WINDOW, tutte LOCAL_REVERSIBLE), ma
                # eseguirla saltando decide_interactive() e' un punto cieco strutturale - se in
                # futuro un'alternativa mappasse verso un intent DESTRUCTIVE/ADMIN, partirebbe
                # senza conferma. Un'alternativa che la policy fermerebbe viene semplicemente
                # scartata (si ripiega sul fallimento originale) invece di aprire una SECONDA
                # richiesta di conferma per qualcosa che l'utente non ha chiesto direttamente.
                alt_decision, alt_reason = self.policy_engine.decide_interactive_with_reason(alt_command.intent, alt_command.parameters)
                if alt_decision == PolicyDecision.ALLOW:
                    alt_result = self.skill_registry.execute(
                        alt_command.intent, alt_command.parameters, policy_engine=self.policy_engine,
                        private=self.private_mode,
                    )
                    if alt_result is not None and alt_result.success:
                        return ActionExecution(alt_command, alt_result, note, alt_reason)
            else:
                offer = fallbacks.offer_after_failure(resolved, result)
                if offer is not None:
                    result = SkillResult(
                        success=False,
                        data={
                            "message": offer["message"], "confirm_parameters": offer["parameters"],
                            "confirm_intent": offer["intent"],
                        },
                        error="CONFIRMATION_REQUIRED",
                    )
        return ActionExecution(resolved, result, policy_reason=policy_reason)

    def _run_agent(self, request: str, remember_text: str | None = None) -> str:
        """Richiesta composta o non riconosciuta: l'orchestratore (v5.0, core/orchestrator.py)
        sceglie l'agente generico o uno specializzato (coding/ricerca), che pensa un passo alla
        volta e guarda i risultati veri prima di decidere il successivo (core/agent.py), invece
        di eseguire un piano fisso scritto in anticipo. Se il modello non e' raggiungibile o non
        conclude nulla, ripiega sul vecchio planner a piano fisso; se fallisce anche quello, NO_PLAN."""
        remember_text = remember_text if remember_text is not None else request
        trace_id = new_trace_id()
        try:
            outcome = self.orchestrator.run(
                request, history=self.conversation_state.get_short_term_history(),
                trace_id=trace_id, private=self.private_mode,
            )
        except Exception:
            self.logger.exception("Errore nell'agente per: %s", request)
            outcome = None

        if outcome is None or (outcome.error is not None and not outcome.did_something):
            return self._try_plan(request)

        self._publish_effect_proof_events(
            [(step.intent, step.verified) for step in outcome.steps if step.verified is not None],
            [step.intent for step in outcome.rolled_back],
            trace_id=trace_id,
        )

        if outcome.pending_confirmation is not None:
            reason = "auth_required" if outcome.pending_confirmation.get("kind") == "AUTH_REQUIRED" else "confirmation_required"
            # F1.5.4 ("mostrare all'utente la sorgente che ha suggerito un'azione sensibile"):
            # None quando il passo precedente non ha restituito contenuto esterno (il caso
            # comune, vedi core/agent.py::TaskAgent.run()) - non aggiunto alla busta di conferma
            # ne' al messaggio in quel caso, per non introdurre rumore su ogni conferma ordinaria.
            external_source = outcome.pending_confirmation.get("suggested_by_external_content")
            self.conversation_state.set_pending_action({
                "intent": outcome.pending_confirmation["intent"],
                "parameters": outcome.pending_confirmation["parameters"],
                "reason": reason,
                "text": remember_text,
                # F1: ripreso da _finalize_pending_action per far comparire la ricevuta
                # dell'esecuzione vera, dopo la conferma, correlata alla stessa richiesta invece
                # di un trace_id scollegato - vedi TaskAgent._log_step per il trace_id dei passi
                # dell'agente che hanno gia' portato a questa richiesta di conferma.
                "trace_id": trace_id,
                "policy_reason": outcome.pending_confirmation.get("policy_reason"),
                "suggested_by_external_content": external_source,
            })
            message = outcome.pending_confirmation["message"]
            if external_source is not None:
                message = f"{message} (Attenzione: suggerito da contenuto esterno - {external_source})"
            # F6.3/F6.7: l'evento IMPORTANTE che questo incremento collega - una conferma/
            # autenticazione richiesta a meta' di un compito composto. Il ponte valuta urgenza e
            # contesto (rischio dell'intent, modalita' corrente, quiet hours) e pubblica su
            # event_bus task/session/device id, le azioni GIA' eseguite e questa stessa decisione
            # - la sua Decision non cambia il messaggio testuale (gia' deciso sopra), solo se/come
            # Jake segnala l'evento su un canale esterno (HUD/companion).
            pending = outcome.pending_confirmation
            self._publish_task_event(lambda: self.task_bridge.decision_required(
                outcome, message=message, intent=pending["intent"], parameters=pending["parameters"],
                policy_reason=pending.get("policy_reason"),
                session_id=current_session_id(), device_id=current_device_id(),
            ))
            self._remember_exchange(remember_text, Command("AGENT", {"request": request}), message)
            return message

        if outcome.question is not None:
            # F1.8.4 ("checkpoint"): il compito NON e' interrotto anomalamente - l'agente ha
            # chiesto qualcosa e _continue_agent() (sotto) ripartira' da capo con la risposta,
            # costruendo un checkpoint nuovo se necessario. Il checkpoint di QUESTO tentativo non
            # serve piu'.
            self.agent_checkpoints.clear()
            self.conversation_state.set_pending_action({
                "intent": "AGENT_CONTINUE",
                "parameters": {"request": request, "question": outcome.question},
                "reason": "agent_question",
                "text": remember_text,
                # F6.3/F6.7: cosi' _continue_agent puo' chiudere il compito che il ponte stava
                # seguendo quando l'utente risponde (vedi resolve_decision li' sotto) - nessun
                # altro codice esistente leggeva "trace_id" per un'azione "agent_question" prima
                # di questo incremento, aggiungerlo non cambia alcun comportamento gia' esistente.
                "trace_id": outcome.trace_id,
            })
            # F6.3/F6.7: anche una domanda di chiarimento e' una decisione richiesta all'utente a
            # meta' di un compito (intent=None: nessun rischio da un'azione specifica da
            # valutare, mai critica per default) - stessa valutazione/stesso evento contestuale
            # della conferma sopra, non un percorso separato.
            self._publish_task_event(lambda: self.task_bridge.decision_required(
                outcome, message=outcome.question, session_id=current_session_id(), device_id=current_device_id(),
            ))
            self._remember_exchange(remember_text, Command("AGENT", {"request": request}), outcome.question)
            return outcome.question

        # F1.8.4 ("checkpoint"): il compito e' CONCLUSO (risposta finale o nessun piano) - un
        # checkpoint serve solo per un'interruzione ANOMALA a meta', mai per il normale "e'
        # finito" (altrimenti una futura RESUME_INTERRUPTED_TASK crederebbe che ci sia ancora
        # qualcosa da riprendere quando in realta' il compito precedente e' semplicemente finito).
        self.agent_checkpoints.clear()
        response = outcome.final_answer or self.NO_PLAN
        # F6.7.2: chiude il compito (nessun effetto se il ponte non lo aveva mai aperto - un
        # turno che non ha eseguito alcun passo dell'agente, il caso comune di una risposta
        # breve). `outcome.error` distingue un compito finito con un errore interno da uno
        # concluso normalmente, senza alcuna nuova logica: il campo esiste gia'.
        self._publish_task_event(lambda: self.task_bridge.finish_task(
            outcome, message=response, success=outcome.error is None,
            session_id=current_session_id(), device_id=current_device_id(),
        ))
        self._remember_exchange(remember_text, Command("AGENT", {"request": request}), response)
        return response

    def _continue_agent(self, action: dict, answer_text: str) -> str:
        """L'utente ha risposto alla domanda di chiarimento posta dall'agente: si riprende il
        compito con la richiesta originale piu' la risposta appena data.

        F6.3/F6.7: il compito che il ponte stava seguendo (decision_required per la domanda
        stessa, vedi _run_agent) si chiude QUI - la risposta lo risolve, anche se il compito
        VERO continua sotto un trace_id nuovo (_run_agent ne genera sempre uno fresco): sono due
        esecuzioni distinte per il ledger/il checkpoint (mai state la stessa), lo sono anche per
        il monitor."""
        self._publish_task_event(lambda: self.task_bridge.resolve_decision(action.get("trace_id"), message=answer_text, success=True))
        request = action["parameters"]["request"]
        question = action["parameters"].get("question", "")
        combined = f"{request}\n(L'utente ha risposto alla domanda \"{question}\" con: {answer_text})"
        return self._run_agent(combined, remember_text=answer_text)

    def _resume_interrupted_task(self, checkpoint: AgentCheckpoint) -> str:
        """F1.8.4 ("checkpoint... da cui riprendere"): non serve modificare TaskAgent.run() per
        "riprendere davvero" un compito - il modello vede gia' cosa e' stato fatto (riassunto
        dentro la richiesta stessa) e decide da solo il prossimo passo, esattamente come farebbe
        per qualunque altra richiesta. Se `_run_agent()` sceglie di nuovo l'agente "general", un
        checkpoint NUOVO sostituisce naturalmente questo (stesso meccanismo di on_step_completed,
        vedi __init__) - nessuna pulizia esplicita necessaria qui."""
        steps_summary = "; ".join(
            f"{step['intent']}({step['parameters']}) -> {'riuscito' if step['success'] else 'fallito'}"
            for step in checkpoint.completed_steps
        ) or "nessun passo ancora completato"
        resume_request = (
            f"{checkpoint.request}\n\n(Questo compito era gia' iniziato e poi interrotto prima di "
            f"finire, senza colpa dell'utente. Passi gia' completati: {steps_summary}. Continua da "
            f"dove eri rimasto, senza ripetere questi passi se non e' necessario.)"
        )
        return self._run_agent(resume_request, remember_text="riprendi il compito interrotto")

    def _match_meta_command(self, text: str) -> Command | None:
        return intent_patterns.match_meta_command(text, has_last_exchange=self.last_exchange is not None)

    def _execute_command(
        self,
        text: str,
        command: Command,
        learn: bool = True,
        *,
        action_id: str | None = None,
    ) -> str:
        intent = command.intent
        action_id = action_id or new_action_id()

        trace_id = new_trace_id()
        started = time.monotonic()

        scope = self._dialogue_scope()

        decision, policy_reason = (
            self.policy_engine.decide_interactive_with_reason(
                intent,
                command.parameters,
            )
        )

        if decision == PolicyDecision.BLOCK:
            self.logger.warning(
                "Azione bloccata da policy: %s",
                intent,
            )

            self._set_dialogue_outcome(
                action_id=action_id,
                text=text,
                command=command,
                status="failed",
                scope=scope,
            )

            self._finish_correction_learning(
                action_id,
                verified=False,
            )

            self._log_action_outcome(
                trace_id,
                started,
                intent,
                command.parameters,
                result="blocked_by_policy",
                policy_reason=policy_reason,
            )

            return (
                f"L'azione {intent} è disabilitata "
                "nella configurazione."
            )

        skill = self.skill_registry.get_skill(intent)

        if skill is None:
            self._set_dialogue_outcome(
                action_id=action_id,
                text=text,
                command=command,
                status="failed",
                scope=scope,
            )

            self._finish_correction_learning(
                action_id,
                verified=False,
            )

            self._log_action_outcome(
                trace_id,
                started,
                intent,
                command.parameters,
                result="skill_not_found",
            )

            return f"Skill non trovata per {intent}"

        execution = self._resolve_and_execute(
            command,
            action_id=action_id,
        )

        resolved = execution.command
        result = execution.result
        note = execution.note

        if (
            result is not None
            and result.error
            in ("CONFIRMATION_REQUIRED", "AUTH_REQUIRED")
        ):
            reason = (
                "auth_required"
                if result.error == "AUTH_REQUIRED"
                else "confirmation_required"
            )

            envelope = self._safe_confirm_envelope(
                resolved.intent,
                resolved.parameters,
                result,
                reason,
            )

            self.conversation_state.set_pending_action(
                {
                    "intent": envelope.get(
                        "confirm_intent",
                        resolved.intent,
                    ),
                    "parameters": envelope.get(
                        "confirm_parameters",
                        resolved.parameters,
                    ),
                    "reason": reason,
                    "text": text,
                    "trace_id": trace_id,
                    "policy_reason": (
                        execution.policy_reason
                        if envelope.get(
                            "confirm_intent",
                            resolved.intent,
                        )
                        == resolved.intent
                        else None
                    ),

                    # F2.6
                    "action_id": action_id,
                    "dialogue_scope": scope,
                }
            )

            self._set_dialogue_outcome(
                action_id=action_id,
                text=text,
                command=resolved,
                status="pending",
                reversible=False,
                scope=scope,
            )

            if resolved.intent != "CORRECT_LAST":
                self._remember_exchange(
                    text,
                    resolved,
                    envelope.get("message", ""),
                    action_id=action_id,
                )

            self._log_action_outcome(
                trace_id,
                started,
                resolved.intent,
                resolved.parameters,
                result=reason,
                policy_reason=execution.policy_reason,
            )

            return envelope.get(
                "message",
                "Confermi questa azione?",
            )

        response = format_skill_result(
            resolved.intent,
            result,
            self.skill_registry,
        )

        if note:
            response = f"{note} {response}"

        undo_descriptor = None
        correlated_action_id = None

        success = bool(
            result is not None
            and result.success
        )

        if success and result is not None:
            self.conversation_state.remember_entities(
                resolved.intent,
                resolved.parameters,
                result.data or {},
            )

            set_current_command_source_intent(
                resolved.intent
            )

            undo_descriptor = generate_undo_descriptor(
                action_id,
                resolved.intent,
                result.data or {},
            )

            if undo_descriptor is not None:
                self.undo_store.save(
                    undo_descriptor
                )

            correlated_action_id = action_id

        status = (
            "executed"
            if success
            else "failed"
        )

        self._set_dialogue_outcome(
            action_id=action_id,
            text=text,
            command=resolved,
            status=status,
            reversible=undo_descriptor is not None,
            scope=scope,
        )

        if learn:
            self.learning.observe(
                text,
                resolved,
                result,
                route=self.router.last_route,
            )

        # CORRECT_LAST contiene internamente il vero comando corretto:
        # non deve sovrascrivere last_exchange dopo che quel comando
        # ha appena scritto il proprio stato.
        if resolved.intent != "CORRECT_LAST":
            self._remember_exchange(
                text,
                resolved,
                response,
                action_id=action_id,
            )

        # Se questa era l'esecuzione prodotta da una correzione,
        # il learning viene risolto SOLO ORA.
        self._finish_correction_learning(
            action_id,
            verified=success,
        )

        outcome = (
            "success"
            if success
            else (
                f"error:{result.error}"
                if result is not None
                else "no_result"
            )
        )

        self._log_action_outcome(
            trace_id,
            started,
            resolved.intent,
            resolved.parameters,
            result=outcome,
            policy_reason=execution.policy_reason,
            action_id=correlated_action_id,
        )

        return response

    def _safe_confirm_envelope(self, intent: str, parameters: dict | None, result: SkillResult, reason: str) -> dict:
        """Valida la busta CONFIRMATION_REQUIRED/AUTH_REQUIRED di una skill (F1, vedi
        core/schema_validation.py) prima di fidarsene: un campo mancante o del tipo sbagliato in
        una skill scritta male (o auto-generata dalla fucina, mai rivista da un umano prima di
        essere confermata) non deve rompere in modo subdolo il ciclo conferma/esecuzione - un
        "confermi?" senza messaggio vero, o peggio dei confirm_parameters malformati che
        farebbero rieseguire l'azione con i parametri sbagliati dopo il si'. Se malformata, logga
        un avviso e ripiega su un default sicuro (i parametri gia' noti, con il marcatore di
        conferma/autenticazione forzato) invece di propagare qualcosa di inaffidabile."""
        problems = validate_confirm_envelope(result.data)
        if not problems:
            return result.data
        self.logger.warning(
            "Busta di conferma malformata per %s: %s. Ripiego su un default sicuro.",
            intent, "; ".join(problems),
        )
        marker = "authenticated" if reason == "auth_required" else "confirmed"
        return {
            "confirm_intent": intent,
            "confirm_parameters": {**(parameters or {}), marker: True},
            "message": f"Confermi: {self.describe_command(Command(intent, parameters))}?",
        }

    def _log_action_outcome(
        self, trace_id: str, started: float, intent: str, parameters: dict | None, *, result: str,
        policy_reason: str | None = None, action_id: str | None = None,
    ) -> None:
        """Punto unico da cui _execute_command scrive in jake_actions.jsonl (F0: log strutturati
        con trace_id, durata, modello, skill, decisione di rischio, risultato). verified resta
        assente (vedi log_action): questo percorso a comando singolo non verifica ancora
        l'effetto dell'azione (a differenza dell'agente a passi, core/agent.py/execution_safety.
        py), quindi dichiara onestamente "non verificato" invece di inventare una prova.

        Se il risultato e' un vero fallimento, la stessa chiamata alimenta anche core/session_
        recorder.py (disattivato per default: vedi session_recording_enabled/_verbatim in
        config/settings.json) con intent e parametri, cosi' tools/replay_session.py puo' farlo
        ripartire davvero per verificare un fix - log_action da solo non basta, non porta i
        parametri. Alimenta anche core/action_ledger.py (F1): una ricevuta con action_id,
        richiedente ("user": e' sempre un comando diretto dell'utente su questo percorso) e
        autorizzazione derivata da authorization_of().

        action_id e' iniettabile (F1.3.5): _execute_command lo genera PRIMA di chiamare questo
        metodo quando l'azione e' riuscita, cosi' lo stesso identificatore correla la ricevuta nel
        ledger con l'eventuale UndoDescriptor salvato in self.undo_store - None (il default)
        preserva il comportamento di sempre per gli altri percorsi (bloccato/non trovato/richiede
        conferma), che non hanno alcun undo da correlare."""
        duration_ms = (time.monotonic() - started) * 1000
        risk = risk_of(intent).value
        log_action(
            trace_id, private=self.private_mode, duration_ms=duration_ms, model=self.model,
            skill=intent, risk_decision=risk, result=result,
        )
        # F1.1.6 (pilota di adozione del contratto, un intent per livello di rischio - vedi
        # tests/test_jake_core_action_contracts.py): ActionError.from_result() sostituisce qui la
        # chiamata diretta a error_category_of() (F1.1.4) con l'oggetto tipizzato che la
        # racchiude - stesso valore per receipt.error_category, nessun cambio di comportamento,
        # ma ora e' il tipo condiviso (core/action_contracts.py, F1.1.2) a passare per davvero da
        # QUESTO chokepoint reale, non solo da test isolati.
        action_error = ActionError.from_result(result)
        validate_action_error(action_error)
        self.action_ledger.record(
            ActionReceipt(
                action_id=action_id or new_action_id(), trace_id=trace_id, ts=time.time(), intent=intent,
                requested_by="user", risk_decision=risk, authorization=authorization_of(result, parameters),
                result=result, idempotency_key=idempotency_key_of(intent, parameters),
                error_category=action_error.category, policy_reason=policy_reason, duration_ms=duration_ms, model=self.model,
                device_id=current_device_id(), windows_user=current_windows_user(),
            ),
            private=self.private_mode,
        )
        if result not in self._NOT_A_FAILURE:
            self.session_recorder.record_failure(
                trace_id, intent=intent, parameters=parameters, error=result,
                risk_decision=risk, private=self.private_mode,
            )

    def _handle_unknown(self, text: str) -> str:
        agent_response = self._run_agent(text)
        if agent_response != self.NO_PLAN:
            return agent_response

        if intent_patterns.is_question(text):
            return self._execute_command(text, Command("ASK_QUESTION", {"question": text}), learn=False)

        self.learning.commit_pending()
        self._remember_exchange(text, Command("UNKNOWN", {}), self.NO_PLAN)
        if self.skill_forge.is_available():
            self.conversation_state.set_pending_action({
                "intent": "CREATE_SKILL", "parameters": {"request": text}, "reason": "offer_learn", "text": text,
                "trace_id": new_trace_id(),
            })
            return "Non so ancora fare questa cosa. Vuoi che provi a impararla da solo, scrivendomi una nuova capacità?"
        return self.NO_PLAN

    def _try_plan(self, text: str) -> str:
        plan = self.planner_provider.build_plan(text)
        if plan is None or len(plan.steps) < 2:
            return self.NO_PLAN
        outcome = self.plan_executor.execute(
            plan, policy_engine=self.policy_engine,
            trace_id=new_trace_id(), private=self.private_mode, model=self.model,
        )
        self._publish_plan_outcome_effect_proof_events(outcome)
        response = format_plan_outcome(outcome, len(plan.steps), self.skill_registry)
        self._remember_exchange(text, Command("PLAN", {"steps": len(plan.steps)}), response)
        return response

    def _handle_confirmation(
        self,
        text: str,
        action: dict | None = None,
    ) -> str:
        if action is None:
            action = (
                self.conversation_state.take_pending_action()
            )

        if action is None:
            return self._process(text)

        reason = action.get("reason")

        # F2.6.4 — chiarimento dell'agente
        if reason == "agent_question":
            reply = classify_reply(
                text,
                DialogueContext(
                    awaiting_clarification=True,
                ),
            )

            if (
                reply.kind
                == ReplyKind.CLARIFICATION_ANSWER
            ):
                return self._continue_agent(
                    action,
                    reply.text,
                )

            # L'utente ha cambiato intenzione.
            return self._process(
                reply.text or text
            )

        # Difesa per chiamate dirette/test.
        # Il percorso normale passa da answer() col testo GREZZO.
        if reason == "auth_required":
            return self._handle_auth_secret(
                text,
                action,
            )

        reply = classify_reply(
            text,
            DialogueContext(
                pending_confirmation=True,
            ),
        )

        if reply.kind == ReplyKind.CONFIRM_YES:
            return self._finalize_pending_action(
                action,
                reply.text,
            )

        if reply.kind == ReplyKind.CONFIRM_NO:
            if action.get("intent") == "APPROVE_PAIRING":
                challenge_id = (
                    action.get("parameters") or {}
                ).get("challenge_id")

                if challenge_id:
                    self.pairing_service.reject(
                        challenge_id
                    )

            self._cancel_dialogue_action(action)

            self._log_denied_action(
                action,
                result="denied_confirmation",
            )

            self._publish_task_event(
                lambda: self.task_bridge.resolve_decision(
                    action.get("trace_id"),
                    message="annullato",
                    success=False,
                )
            )

            return "Va bene, annullato."

        # Qualunque frase non sia un sì/no INTERO
        # è un nuovo comando.
        #
        # "sì ma prima apri Spotify" arriva qui.
        self._cancel_dialogue_action(action)

        return self._process(
            reply.text or text
        )

    def _handle_auth_secret(
        self,
        secret: str,
        action: dict,
    ) -> str:
        """
        Gestisce un segreto di autenticazione senza loggarlo
        o mandarlo al resto della pipeline.
        """
        if self.auth_gate.is_locked_out():
            self._cancel_dialogue_action(action)

            self._log_denied_action(
                action,
                result="denied_auth",
            )

            return self._lockout_message()

        if self.auth_gate.check(secret):
            # NON passare il secret come fallback_text.
            return self._finalize_pending_action(
                action,
                "[autenticazione]",
            )

        self._cancel_dialogue_action(action)

        self._log_denied_action(
            action,
            result="denied_auth",
        )

        if self.auth_gate.is_locked_out():
            return self._lockout_message()

        return "Passphrase errata: azione annullata."

    def _lockout_message(self) -> str:
        remaining = int(self.auth_gate.lockout_remaining_seconds()) + 1
        return f"Troppi tentativi falliti: riprova tra {remaining} secondi."

    def _log_denied_action(self, action: dict, *, result: str) -> None:
        """F1: una passphrase sbagliata o un "no" a una richiesta di conferma non fanno mai
        partire l'azione, ma sono comunque un evento di sicurezza degno di una ricevuta nel
        ledger (vedi ROADMAP.md, F1: "un diniego e' comunque un evento di sicurezza degno di
        una ricevuta"), distinto da AUTHORIZATION_PENDING (che invece aspetta ancora una
        risposta). trace_id viene dall'azione in sospeso, come per _finalize_pending_action,
        cosi' il diniego si correla alla richiesta di conferma originale nel ledger.

        Non alimenta core/logger.log_action ne' core/session_recorder.py: quei due esistono per
        il debug/replay di comandi falliti per un bug (vedi _log_action_outcome), non per una
        scelta legittima e volontaria dell'utente - un "no" non e' un fallimento da riprodurre."""
        trace_id = action.get("trace_id") or new_trace_id()
        intent = action["intent"]
        parameters = action["parameters"]
        policy_reason = action.get("policy_reason")
        if not isinstance(policy_reason, str) or policy_reason not in POLICY_REASONS:
            policy_reason = None  # vecchi pending o metadati malformati: non inventare/esporre valori
        # F1.1.6: stesso principio di _log_action_outcome sopra - ActionError.from_result() al
        # posto della chiamata diretta a error_category_of().
        action_error = ActionError.from_result(result)
        validate_action_error(action_error)
        self.action_ledger.record(
            ActionReceipt(
                action_id=new_action_id(), trace_id=trace_id, ts=time.time(), intent=intent,
                requested_by="user", risk_decision=risk_of(intent).value,
                authorization=authorization_of(result, parameters), result=result,
                idempotency_key=idempotency_key_of(intent, parameters),
                error_category=action_error.category, policy_reason=policy_reason,
                device_id=current_device_id(), windows_user=current_windows_user(),
            ),
            private=self.private_mode,
        )

    def _finalize_pending_action(
        self,
        action: dict,
        fallback_text: str,
    ) -> str:
        trace_id = (
            action.get("trace_id")
            or new_trace_id()
        )

        action_id = (
            action.get("action_id")
            or new_action_id()
        )

        scope = (
            action.get("dialogue_scope")
            or self._dialogue_scope()
        )

        started = time.monotonic()

        if action.get("correction_after") is not None:
            descriptor = self.undo_store.get(action.get("undo_source_action_id"))
            if descriptor is None:
                self._cancel_dialogue_action(action)
                return "L'undo non è più disponibile. Non eseguo la versione corretta."

        parameters = {
            **strip_authorization_signals(
                action["parameters"]
            ),
            "confirmed": True,
        }

        if action.get("reason") == "auth_required":
            parameters.update(
                authenticated=True,
                authenticated_via="passphrase",
            )

        command, result, policy_reason = (
            self._authorize_command(
                Command(
                    action["intent"],
                    parameters,
                )
            )
        )

        if result is None:
            result = self.skill_registry.execute(
                command.intent,
                command.parameters,
                policy_engine=self.policy_engine,
                action_id=action_id,
                private=self.private_mode,
            )

        # Può servire un secondo gradino di conferma/auth.
        if (
            result is not None
            and result.error
            in ("CONFIRMATION_REQUIRED", "AUTH_REQUIRED")
        ):
            reason = (
                "auth_required"
                if result.error == "AUTH_REQUIRED"
                else "confirmation_required"
            )

            envelope = self._safe_confirm_envelope(
                command.intent,
                command.parameters,
                result,
                reason,
            )

            pending = {
                "intent": envelope.get(
                    "confirm_intent",
                    command.intent,
                ),
                "parameters": envelope.get(
                    "confirm_parameters",
                    command.parameters,
                ),
                "reason": reason,
                "text": action.get("text", ""),
                "trace_id": trace_id,
                "policy_reason": (
                    policy_reason
                    if envelope.get(
                        "confirm_intent",
                        command.intent,
                    )
                    == command.intent
                    else None
                ),

                # conserva identità logica
                "action_id": action_id,
                "dialogue_scope": scope,
            }

            # Se siamo nel mezzo di:
            # undo -> correzione,
            # NON perdere il continuation.
            for key in (
                "correction_after",
                "undo_source_action_id",
            ):
                if key in action:
                    pending[key] = action[key]

            self.conversation_state.set_pending_action(
                pending
            )

            if action.get("correction_after") is None:
                self._set_dialogue_outcome(
                    action_id=action_id,
                    text=action.get(
                        "text",
                        fallback_text,
                    ),
                    command=command,
                    status="pending",
                    scope=scope,
                )

            self._log_action_outcome(
                trace_id,
                started,
                command.intent,
                command.parameters,
                result=reason,
                policy_reason=policy_reason,
            )

            return envelope.get(
                "message",
                "Confermi questa azione?",
            )

        success = bool(
            result is not None
            and result.success
        )

        outcome = (
            "success"
            if success
            else (
                f"error:{result.error}"
                if result is not None
                else "no_result"
            )
        )

        self._log_action_outcome(
            trace_id,
            started,
            command.intent,
            command.parameters,
            result=outcome,
            policy_reason=policy_reason,
            action_id=(
                action_id
                if success
                else None
            ),
        )

        response = format_skill_result(
            command.intent,
            result,
            self.skill_registry,
        )

        undo_descriptor = None

        if success:
            self.conversation_state.remember_entities(
                command.intent,
                command.parameters,
                result.data or {},
            )

            # FIX importante:
            # anche il percorso POST-CONFERMA ora genera UndoDescriptor.
            undo_descriptor = generate_undo_descriptor(
                action_id,
                command.intent,
                result.data or {},
            )

            if undo_descriptor is not None:
                self.undo_store.save(
                    undo_descriptor
                )

        # Un undo interno alla correzione non è un nuovo
        # comando pronunciato dall'utente.
        if action.get("correction_after") is None:
            self._set_dialogue_outcome(
                action_id=action_id,
                text=action.get(
                    "text",
                    fallback_text,
                ),
                command=command,
                status=(
                    "executed"
                    if success
                    else "failed"
                ),
                reversible=undo_descriptor is not None,
                scope=scope,
            )

        if (
            action.get("reason")
            in (
                "confirmation_required",
                "auth_required",
            )
            and action.get("text")
        ):
            self.learning.observe(
                action["text"],
                command,
                result,
                route=(
                    "llm"
                    if self.last_route == "llm"
                    else "confirmed"
                ),
            )

        if action.get("correction_after") is None:
            self._remember_exchange(
                action.get(
                    "text",
                    fallback_text,
                ),
                command,
                response,
                action_id=action_id,
            )

        self._finish_correction_learning(
            action_id,
            verified=success,
        )

        self._publish_task_event(
            lambda: self.task_bridge.resolve_decision(
                trace_id,
                message=response,
                success=success,
            )
        )

        correction_after = action.get(
            "correction_after"
        )

        if correction_after is not None:
            if not success:
                return (
                    f"{response} "
                    "Non eseguo la versione corretta perché "
                    "non sono riuscito ad annullare in sicurezza "
                    "l'azione precedente."
                )

            source_action_id = action.get(
                "undo_source_action_id"
            )

            if source_action_id:
                self.undo_store.mark_used(
                    source_action_id
                )

            corrected_command = Command(
                correction_after["intent"],
                dict(
                    correction_after.get(
                        "parameters"
                    )
                    or {}
                ),
            )

            corrected_response = (
                self._execute_corrected_command(
                    correction_after[
                        "source_action_id"
                    ],
                    correction_after[
                        "corrected_text"
                    ],
                    corrected_command,
                )
            )

            return (
                f"{response} "
                f"{corrected_response}"
            ).strip()

        return response

    def _remember_exchange(
        self,
        text: str,
        command: Command,
        response: str,
        *,
        action_id: str | None = None,
    ) -> None:
        if action_id is None:
            # Anche UNKNOWN/chitchat/agente devono sostituire l'ultimo turno:
            # correggere una frase non capita non deve correggere un'azione piu' vecchia.
            action_id = new_action_id()
            self._set_dialogue_outcome(
                action_id=action_id, text=text, command=command,
                status="failed" if command.intent == "UNKNOWN" else "executed",
            )
        self.last_exchange = {
            "text": text,
            "command": command,
            "response": response,
            "action_id": action_id,
        }

    # ---- sospensione della proattivita' ----------------------------------------------------

    @contextlib.contextmanager
    def proactivity_suspended(self, reason: str):
        """Per la durata del blocco Jake non prende iniziative: promemoria, automazioni e avvisi di
        sistema/housekeeping si fermano, e qualunque notifica arrivi comunque va in coda. Solo a
        runtime (nessuna configurazione persistente toccata); all'uscita ripartono SOLO i componenti
        che erano attivi (un kill switch resta rispettato) e si restituiscono i messaggi in coda ora
        ammessi. Gate hardware F2 (26/09/2026): notifiche partite durante la misura della voce."""
        components = [("scheduler", self.scheduler), ("trigger_scheduler", self.trigger_scheduler),
                      ("system_advisor", self.system_advisor)]
        paused = []
        self.notification_center.suspend()
        self.logger.info("Proattivita' sospesa: %s", reason)
        try:
            for name, component in components:
                thread = getattr(component, "_thread", None)
                if thread is not None and thread.is_alive():
                    try:
                        component.stop()
                        paused.append((name, component))
                    except Exception:
                        self.logger.exception("Errore sospendendo %s", name)
            released: list[str] = []
            yield released
        finally:
            for name, component in paused:
                if name != "system_advisor" and self.kill_switch.is_active():
                    continue  # il kill switch li ha voluti fermi: non si riaccendono da soli
                try:
                    component.start()
                except Exception:
                    self.logger.exception("Errore riprendendo %s", name)
            released.extend(self.notification_center.resume())
            self.logger.info("Proattivita' ripresa: %s", reason)

    # ---- kill switch -----------------------------------------------------------------------

    def activate_kill_switch(self) -> None:
        """Ferma subito agenti e automazioni (F1, vedi core/kill_switch.py): il flag condiviso
        interrompe qualunque agente/piano PRIMA del passo successivo (mai a meta' di uno gia' in
        corso, vedi il modulo), e ReminderScheduler/TriggerScheduler vengono fermati per davvero
        (i loro thread terminano, non solo "smettono di fare qualcosa") - non ripartono da soli:
        serve reset_kill_switch() per farli ripartire."""
        self.kill_switch.activate()
        for scheduler in (self.scheduler, self.trigger_scheduler):
            try:
                scheduler.stop()
            except Exception:
                self.logger.exception("Errore fermando uno scheduler durante il kill switch")

    def reset_kill_switch(self) -> None:
        """Disattiva il kill switch e fa ripartire gli scheduler fermati da activate_kill_
        switch() - non riparte da sola: e' una scelta esplicita, cosi' come lo e' stata fermarli.

        F6: azzera anche il budget di autonomia (core/autonomy_budget.py) - se un'automazione
        impazzita aveva esaurito il budget prima o durante lo stop di emergenza, un "riprendi"
        esplicito dell'utente deve dare un budget pieno, non farla ripartire gia' bloccata senza
        che l'utente lo sappia."""
        self.kill_switch.reset()
        self.autonomy_budget.reset()
        for scheduler in (self.scheduler, self.trigger_scheduler):
            try:
                scheduler.start()
            except Exception:
                self.logger.exception("Errore riavviando uno scheduler dopo il kill switch")

    # ---- chiusura ------------------------------------------------------------------------

    _DRAIN_TIMEOUT_SECONDS = 5.0
    _DRAIN_POLL_SECONDS = 0.05

    def _drain_in_flight_answers(self) -> None:
        """F1.8.4 ("gestire shutdown con drain limitato"): aspetta, entro un tetto, che ogni
        answer() gia' in corso su un ALTRO thread (tipicamente una richiesta companion - core/
        companion_server.py e' un ThreadingHTTPServer con un thread per richiesta; il loop voce
        non si sovrappone mai con la propria chiamata a shutdown(), che arriva sempre DOPO che il
        proprio answer() e' gia' tornato) finisca, PRIMA di fermare gli altri componenti/salvare
        le cache che quella chiamata potrebbe ancora star usando - altrimenti una richiesta a
        meta' potrebbe scrivere una cache DOPO il salvataggio "finale" qui sotto, o usare un
        componente gia' fermato. `companion_server.stop()` e' gia' stato chiamato PRIMA di questo
        (smette di accettare richieste NUOVE): qui si aspetta solo quelle GIA' in corso. Non
        blocca per sempre: un tetto di 5s (poi procede comunque, con un avviso nel log) - lo
        stesso principio "chiedi gentilmente, poi procedi" gia' usato per gli scheduler in
        background (F1.8.5) e per il worker sandboxato (F1.6)."""
        deadline = time.monotonic() + self._DRAIN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            with self._in_flight_lock:
                if self._in_flight_answers == 0:
                    return
            time.sleep(self._DRAIN_POLL_SECONDS)
        with self._in_flight_lock:
            remaining = self._in_flight_answers
        if remaining > 0:
            self.logger.warning(
                "Shutdown: %d chiamata/e ad answer() ancora in corso dopo %.1fs, procedo comunque",
                remaining, self._DRAIN_TIMEOUT_SECONDS,
            )

    def shutdown(self) -> None:
        # F1.8.4 ("gestire shutdown con drain limitato, checkpoint e release dei device"): buco
        # reale - un `except Exception: pass` silenzioso per ognuno di questi passi significava
        # che un component.stop() fallito (una connessione companion non chiusa, un hook
        # desktop_context non rimosso...) o un salvataggio di cache fallito (disco pieno,
        # permessi) sparivano senza lasciare TRACCIA in nessun log: un utente che si accorge che
        # NEST "dimentica" la cache dopo un riavvio, o che un socket resta occupato, non avrebbe
        # avuto modo di scoprire perche'. Ogni passo di chiusura logga ora l'eccezione con il
        # nome del componente prima di continuare con gli altri (non ferma lo shutdown: un
        # componente che non si chiude bene non deve impedire agli altri di provarci).
        #
        # companion_server e' fermato PER PRIMO E DA SOLO (non nel loop sotto): smette di
        # accettare richieste NUOVE prima che _drain_in_flight_answers() aspetti quelle GIA' in
        # corso - l'ordine conta, altrimenti una richiesta potrebbe iniziare proprio mentre si
        # aspetta che le altre finiscano, vanificando il senso del drain.
        try:
            self.companion_server.stop()
        except Exception:
            self.logger.exception("Errore chiudendo companion_server durante lo shutdown")
        self._drain_in_flight_answers()
        for name, component in (
            ("scheduler", self.scheduler), ("trigger_scheduler", self.trigger_scheduler),
            ("system_advisor", self.system_advisor), ("desktop_context", self.desktop_context),
        ):
            try:
                component.stop()
            except Exception:
                self.logger.exception("Errore chiudendo %s durante lo shutdown", name)
        try:
            self.retriever.example_index.save_cache()
            self.retriever.capability_index.save_cache()
        except Exception:
            self.logger.exception("Errore salvando la cache degli indici semantici durante lo shutdown")
        # F1.6: nessun worker da fermare se nessuna skill forgiata e' mai stata invocata in
        # questa sessione (stop_sandbox_worker() e' un no-op in quel caso) - se invece il worker
        # sandboxato e' vivo, deve essere chiuso esplicitamente qui: non e' un processo figlio
        # che Windows terminerebbe da solo alla chiusura di Jake.
        try:
            self.skill_registry.stop_sandbox_worker()
        except Exception:
            self.logger.exception("Errore fermando il worker sandboxato per le skill forgiate durante lo shutdown")
        # F1.4.6 (fase 6): chiude la connessione SQLite del registro credenziali - stesso
        # principio degli altri passi sopra, un fallimento qui non deve impedire al resto dello
        # shutdown di proseguire.
        try:
            self.device_credential_store.close()
        except Exception:
            self.logger.exception("Errore chiudendo device_credential_store durante lo shutdown")
