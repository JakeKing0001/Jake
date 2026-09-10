import time

from core import fallbacks
from core import intent_patterns
from core.action_ledger import ActionLedger, ActionReceipt, authorization_of, idempotency_key_of, new_action_id
from core.agent import TaskAgent
from core.auth_gate import AuthGate
from core.autonomy_budget import AutonomyBudget
from core.command import Command
from core.companion_server import CompanionServer
from core.context_summarizer import ContextSummarizer
from core.desktop_context import DesktopContextTracker
from core.event_bus import EventBus
from core.hud_protocol import EventType, HudEvent
from core.kill_switch import KillSwitch
from core.learning_manager import LearningManager
from core.logger import get_logger, log_action, new_trace_id
from core.nlu import chitchat
from core.nlu.examples import ExampleStore
from core.nlu.index import lexical_similarity
from core.nlu.normalizer import TranscriptNormalizer
from core.nlu.retriever import CapabilityRetriever
from core.notification_center import NotificationCenter
from core.ollama_client import OllamaClient
from core import orchestrator
from core.orchestrator import JakeOrchestrator
from core.policy_engine import PolicyDecision, PolicyEngine
from core.plugin_loader import load_plugins
from core.response_formatter import format_plan_outcome, format_skill_result
from core.risk import risk_of
from core.router import Router
from core.scheduler import ReminderScheduler
from core.schema_validation import validate_confirm_envelope
from core.session_hooks import SessionHooks
from core.session_recorder import SessionRecorder
from core.skill_forge import SkillForge
from core.skill_registry import SkillRegistry
from core.skill_result import SkillResult
from core.system_advisor import SystemAdvisor
from core.trigger_scheduler import TriggerScheduler
from skills.kill_switch import KillSwitchSkill, ResetKillSwitchSkill
from skills.learn import CorrectLastSkill, ForgetLearnedSkill, LearnCommandSkill, ListLearnedSkill
from skills.model_control import ListModelsSkill, SetModelSkill
from skills.session_control import (
    HelpSkill, PauseListeningSkill, PrivateModeSkill, RepeatLastSkill, StartDictationSkill, StopDictationSkill,
    StopTalkingSkill,
)
from skills.notification_mode import GetNotificationModeSkill, SetNotificationModeSkill
from skills.skill_forge_skills import CreateSkillSkill, DeleteCreatedSkillSkill, ListCreatedSkillsSkill


class JakeCore:
    EXIT_SENTINEL = "l'utente vuole uscire"
    NO_PLAN = "Non so ancora fare questa cosa"

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

        # Kill switch globale (F1): un solo interruttore condiviso da tutti gli agenti e le
        # automazioni - vedi core/kill_switch.py, skills/kill_switch.py, activate_kill_switch()/
        # reset_kill_switch() piu' sotto. Azionabile da voce/testo (skill KILL_SWITCH) e, quando
        # il resto dell'infrastruttura lo aggancera' (hotkey globale, tray - non fatto in questa
        # sessione), da li' allo stesso oggetto.
        self.kill_switch = KillSwitch()
        self.skill_registry.plan_executor.kill_switch = self.kill_switch

        # Protocollo eventi + server companion (v4.9.1 HUD IPC transport, v5.8 Mobile
        # Companion, v5.9 Ambient Computing): qualsiasi presentazione esterna (HUD nativo,
        # app companion su un altro dispositivo) puo' iscriversi a self.event_bus senza essere
        # un processo Python nello stesso interprete (vedi core/hud_protocol.py, core/
        # companion_server.py). Il server e' costruito sempre ma NON avviato per default:
        # parte solo se companion_server_enabled e' esplicitamente vero in config.json, stesso
        # pattern gia' usato da system_advisor_enabled.
        self.event_bus = EventBus()
        # F1 (Identity & Authentication, "capability token... per dispositivo" - vedi
        # ROADMAP.md): opt-in - se companion_token non e' mai stato impostato in config.json,
        # il server resta come prima di questa fase (nessuna autenticazione), per non cambiare
        # comportamento a chi ha gia' un uso locale/fidato. Cifrato a riposo via DPAPI come
        # admin_passphrase (core/config.py, SECRET_KEYS).
        self.companion_server = CompanionServer(
            event_bus=self.event_bus, command_handler=self.answer,
            port=int(config.get("companion_server_port", 8765) or 8765),
            token=config.get("companion_token"),
        )
        if bool(config.get("companion_server_enabled", False)):
            self.companion_server.start()
            self.logger.info("Server companion in ascolto su 127.0.0.1:%d", self.companion_server.port)

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
            executor=lambda intent, parameters: self._resolve_and_execute(Command(intent, parameters))[1],
            session_recorder=self.session_recorder, action_ledger=self.action_ledger, agent_name="general",
            kill_switch=self.kill_switch,
        )
        self.agent.on_step = self._on_agent_step

        # Architettura multi-agente (v5.0/5.1/5.2): stesso TaskAgent, configurato con un
        # elenco di strumenti fisso e un prompt diverso per i domini coding/ricerca, invece del
        # recupero semantico generico su tutte le ~200 capacita'. JakeOrchestrator sceglie quale
        # dei tre usare in base alla richiesta (vedi core/orchestrator.py).
        agent_kwargs = dict(
            model_provider=lambda: self.model,
            format_result=lambda intent, result: format_skill_result(intent, result, self.skill_registry),
            logger=self.logger,
            context_provider=lambda: self._agent_context(),
            executor=lambda intent, parameters: self._resolve_and_execute(Command(intent, parameters))[1],
            session_recorder=self.session_recorder, action_ledger=self.action_ledger,
            kill_switch=self.kill_switch,
        )
        self.coding_agent = TaskAgent(
            self.skill_registry, self.retriever, self.ollama,
            fixed_tools=list(orchestrator.CODING_TOOLS), persona_line=orchestrator.CODING_PERSONA,
            agent_name="coding", **agent_kwargs,
        )
        self.coding_agent.on_step = self._on_agent_step
        self.research_agent = TaskAgent(
            self.skill_registry, self.retriever, self.ollama,
            fixed_tools=list(orchestrator.RESEARCH_TOOLS), persona_line=orchestrator.RESEARCH_PERSONA,
            agent_name="research", **agent_kwargs,
        )
        self.research_agent.on_step = self._on_agent_step
        self.orchestrator = JakeOrchestrator(self.agent, self.coding_agent, self.research_agent)

        self.router = Router(
            skill_registry=self.skill_registry, example_store=self.example_store,
            retriever=self.retriever, client=self.ollama,
        )
        self.conversation_state = self.skill_registry.conversation_state
        self.memory_manager = self.skill_registry.memory_manager
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
        )

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
            ("KILL_SWITCH", KillSwitchSkill(self)),
            ("RESET_KILL_SWITCH", ResetKillSwitchSkill(self)),
            ("START_DICTATION", StartDictationSkill(self)),
            ("STOP_DICTATION", StopDictationSkill(self)),
            ("CREATE_SKILL", CreateSkillSkill(self.skill_forge)),
            ("LIST_CREATED_SKILLS", ListCreatedSkillsSkill(self.skill_forge)),
            ("DELETE_CREATED_SKILL", DeleteCreatedSkillSkill(self.skill_forge, self.learning)),
            ("SET_NOTIFICATION_MODE", SetNotificationModeSkill(self.notification_center)),
            ("GET_NOTIFICATION_MODE", GetNotificationModeSkill(self.notification_center)),
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

        # Indici del recupero semantico: costruiti dopo che TUTTE le skill sono registrate.
        self.retriever.refresh()
        self.logger.info(
            "Jake 3.0 pronto: %d capacita', %d esempi (%d imparati), embedding %s",
            len(self.skill_registry.skills), len(self.example_store.all()),
            len(self.example_store.learned()), "attivi" if self.retriever.using_embeddings() else "NON disponibili (fallback lessicale)",
        )

        self.last_exchange = None  # {"text", "command", "response"}
        self.last_response = None
        self.last_route = None

    # ---- callback di default -------------------------------------------------------------

    def notify(self, kind: str, message: str) -> str | None:
        """Punto unico da cui passa ogni notifica proattiva (promemoria/avviso/automazione)
        prima di essere presentata, sia in CLI (qui sotto) sia in voce (vedi WakeWordSession,
        core/voice/wake_word_session.py, che chiama questo stesso metodo): applica la modalita'
        di notifica corrente (v4.3). Restituisce il messaggio da presentare subito, o None se
        e' stato solo messo in coda per quando la modalita' tornera' a permetterlo."""
        gated = self.notification_center.gate(kind, message)
        if gated is not None:
            self.event_bus.publish(HudEvent(EventType.NOTIFICATION, {"kind": kind, "text": gated}))
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
        message = self.notify("advisory", message)
        if message is None:
            return
        print(f"\nJake > {message}\nTu > ", end="", flush=True)

    def _default_on_trigger_fired(self, trigger: dict, outcome, total_steps: int) -> None:
        summary = format_plan_outcome(outcome, total_steps, self.skill_registry)
        message = self.notify("trigger", f"Ho eseguito automaticamente '{trigger.get('name')}':\n{summary}")
        if message is None:
            return
        print(f"\nJake > {message}\nTu > ", end="", flush=True)

    def _agent_context(self) -> str:
        parts = [part for part in (self.desktop_context.context_summary(), self.conversation_state.entities_summary()) if part]
        return " | ".join(parts)

    def _on_agent_step(self, step_index: int, description: str) -> None:
        self.session_hooks.call("set_state", "working", description)
        self.event_bus.publish(HudEvent(EventType.AGENT_STEP, {"step": step_index, "description": description}))

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
        raw_text = text or ""
        text = self.normalizer.normalize(raw_text)
        if not text:
            return "Non ho sentito nulla."
        text = self._resolve_pronouns(text)
        try:
            response = self._process(text)
        except Exception:
            # Nessuna eccezione imprevista deve mai far crashare Jake: viene registrata nel log
            # e riportata all'utente con un messaggio comprensibile invece di terminare il processo.
            self.logger.exception("Errore imprevisto elaborando: %s", text)
            response = "Mi dispiace, si è verificato un errore imprevisto. L'ho registrato nel log."
            self.event_bus.publish(HudEvent(EventType.ERROR, {"detail": "errore imprevisto"}))

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
            self.conversation_state.add_turn("jake", response)
            if response:
                self.last_response = response
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

    def apply_correction(self, request: str) -> str:
        """'No, intendevo X': esegue X e impara ad associare la frase precedente a X."""
        previous = self.last_exchange
        text = self.normalizer.normalize(request)
        text = self._resolve_pronouns(text)
        command = self.router.detect_intent(text)
        if command.intent == "UNKNOWN":
            agent_response = self._run_agent(text)
            if agent_response != self.NO_PLAN:
                return agent_response
            return "Non ho capito nemmeno la correzione: prova a dirlo in un altro modo."
        response = self._execute_command(text, command, learn=False)
        if previous and previous.get("text") and previous["text"] != text:
            previous_command = previous.get("command")
            previous_intent = previous_command.intent if previous_command is not None else "UNKNOWN"
            # Impara solo se la correzione riguarda davvero la frase precedente: stesse parole
            # chiave, oppure Jake non aveva capito nulla. "apri X" seguito da "no, intendevo che
            # ore sono" non deve insegnare che "apri X" significa chiedere l'ora.
            related = lexical_similarity(previous["text"], text) >= 0.25 or previous_intent in ("UNKNOWN", "CHITCHAT", "ASK_QUESTION")
            if related:
                self.learning.correct(previous["text"], previous_command, command)
                self.logger.info("Correzione: %r -> %s %s", previous["text"], command.intent, command.parameters)
        return response

    # ---- pipeline ------------------------------------------------------------------------

    def _process(self, text: str) -> str:
        if self.conversation_state.has_pending_action():
            return self._handle_confirmation(text)

        if intent_patterns.is_exit(text):
            return self.EXIT_SENTINEL

        meta = self._match_meta_command(text)
        if meta is not None:
            self.logger.info("Meta-comando: %s %s", meta.intent, meta.parameters)
            return self._execute_command(text, meta, learn=False)

        # Un comando insegnato o corretto dall'utente vince su tutto (anche sulle frasi di
        # cortesia: "prova jake" potrebbe sembrare un saluto, ma se l'utente lo ha insegnato...).
        taught = self.example_store.find_exact(text)
        if taught is not None and taught.source in ("taught", "corrected"):
            self.last_route = "exact"
            self.logger.info("Comando insegnato: %s %s", taught.intent, taught.parameters)
            return self._execute_command(text, Command(taught.intent, dict(taught.parameters)), learn=False)

        # Frasi di cortesia brevi: risposta immediata, nessuna chiamata al modello.
        if len(text.split()) <= 4:
            quick = chitchat.reply(text)
            if quick is not None:
                self.learning.commit_pending()
                self._remember_exchange(text, Command("CHITCHAT", {"text": text}), quick)
                return quick

        if intent_patterns.is_multi_step_request(text):
            agent_response = self._run_agent(text)
            if agent_response != self.NO_PLAN:
                return agent_response
            # l'agente non e' riuscito a fare nulla di utile: ripiega sul routing normale

        command = self.router.detect_intent(text)
        self.last_route = self.router.last_route
        self.logger.info("Instradamento: %s -> %s %s", self.last_route, command.intent, command.parameters)

        if command.intent == "UNKNOWN":
            return self._handle_unknown(text)
        return self._execute_command(text, command)

    def _resolve_pronouns(self, text: str) -> str:
        return intent_patterns.resolve_pronouns(text, self.conversation_state.get_entities())

    def _resolve_and_execute(self, command: Command) -> tuple[Command, SkillResult | None, str | None]:
        """Esegue un comando applicando i ripieghi (v3.1, vedi core/fallbacks.py): riscrittura
        prima dell'esecuzione (es. OPEN_URL su un nome di app installata -> OPEN_APP), e se
        fallisce prova un'alternativa sensata o propone un'azione da confermare, invece di
        fermarsi al primo 'non trovato'. Restituisce (comando davvero eseguito, risultato, nota
        da anteporre alla risposta o None). Questo e' anche il punto in cui entra il motore di
        policy centralizzato (v3.2, F1: core/policy_engine.py, decide_interactive()) - un intent
        in always_confirm_intents (config manuale + classificazione del rischio, vedi
        core/risk.py) chiede conferma qui, PRIMA di eseguire davvero, invece che solo nel
        percorso a comando singolo di JakeCore._execute_command. Questo copre anche l'agente a
        passi (core/agent.py), che esegue le skill passando da qui e non da _execute_command -
        F1: e' anche il motivo per cui blocked_intents va ricontrollato qui, non solo a monte in
        _execute_command: un intent disabilitato dall'utente in config.json restava altrimenti
        eseguibile da un compito composto, che non passa mai da li'. Il gradino REQUIRE_AUTH
        (v5.4/5.5) viene controllato PRIMA di quello CONFIRM: un'azione ADMIN, quando
        l'autenticazione e' attiva, chiede la passphrase invece della semplice conferma si'/no."""
        resolved = fallbacks.pre_execution_rewrite(command, self.skill_registry)
        decision = self.policy_engine.decide_interactive(resolved.intent, resolved.parameters)
        if decision == PolicyDecision.BLOCK:
            return resolved, SkillResult(success=False, data={}, error="POLICY_BLOCKED"), None
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
                decision = PolicyDecision.ALLOW
            else:
                return resolved, SkillResult(
                    success=False,
                    data={
                        "message": f"Serve l'autenticazione: {self.describe_command(resolved)}. Di' la passphrase per confermare.",
                        "confirm_parameters": {**(resolved.parameters or {}), "authenticated": True, "authenticated_via": "passphrase"},
                        "confirm_intent": resolved.intent,
                    },
                    error="AUTH_REQUIRED",
                ), None
        if decision == PolicyDecision.CONFIRM:
            return resolved, SkillResult(
                success=False,
                data={
                    "message": f"Confermi: {self.describe_command(resolved)}?",
                    "confirm_parameters": {**(resolved.parameters or {}), "confirmed": True},
                    "confirm_intent": resolved.intent,
                },
                error="CONFIRMATION_REQUIRED",
            ), None
        result = self.skill_registry.execute(resolved.intent, resolved.parameters)
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
                alt_decision = self.policy_engine.decide_interactive(alt_command.intent, alt_command.parameters)
                if alt_decision == PolicyDecision.ALLOW:
                    alt_result = self.skill_registry.execute(alt_command.intent, alt_command.parameters)
                    if alt_result is not None and alt_result.success:
                        return alt_command, alt_result, note
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
        return resolved, result, None

    def _run_agent(self, request: str, remember_text: str = None) -> str:
        """Richiesta composta o non riconosciuta: l'orchestratore (v5.0, core/orchestrator.py)
        sceglie l'agente generico o uno specializzato (coding/ricerca), che pensa un passo alla
        volta e guarda i risultati veri prima di decidere il successivo (core/agent.py), invece
        di eseguire un piano fisso scritto in anticipo. Se il modello non e' raggiungibile o non
        conclude nulla, ripiega sul vecchio planner a piano fisso; se fallisce anche quello, NO_PLAN."""
        remember_text = remember_text if remember_text is not None else request
        try:
            outcome = self.orchestrator.run(
                request, history=self.conversation_state.get_short_term_history(),
                trace_id=new_trace_id(), private=self.private_mode,
            )
        except Exception:
            self.logger.exception("Errore nell'agente per: %s", request)
            outcome = None

        if outcome is None or (outcome.error is not None and not outcome.did_something):
            return self._try_plan(request)

        if outcome.pending_confirmation is not None:
            reason = "auth_required" if outcome.pending_confirmation.get("kind") == "AUTH_REQUIRED" else "confirmation_required"
            self.conversation_state.set_pending_action({
                "intent": outcome.pending_confirmation["intent"],
                "parameters": outcome.pending_confirmation["parameters"],
                "reason": reason,
                "text": remember_text,
                # F1: ripreso da _finalize_pending_action per far comparire la ricevuta
                # dell'esecuzione vera, dopo la conferma, correlata alla stessa richiesta invece
                # di un trace_id scollegato - vedi TaskAgent._log_step per il trace_id dei passi
                # dell'agente che hanno gia' portato a questa richiesta di conferma.
                "trace_id": new_trace_id(),
            })
            message = outcome.pending_confirmation["message"]
            self._remember_exchange(remember_text, Command("AGENT", {"request": request}), message)
            return message

        if outcome.question is not None:
            self.conversation_state.set_pending_action({
                "intent": "AGENT_CONTINUE",
                "parameters": {"request": request, "question": outcome.question},
                "reason": "agent_question",
                "text": remember_text,
            })
            self._remember_exchange(remember_text, Command("AGENT", {"request": request}), outcome.question)
            return outcome.question

        response = outcome.final_answer or self.NO_PLAN
        self._remember_exchange(remember_text, Command("AGENT", {"request": request}), response)
        return response

    def _continue_agent(self, action: dict, answer_text: str) -> str:
        """L'utente ha risposto alla domanda di chiarimento posta dall'agente: si riprende il
        compito con la richiesta originale piu' la risposta appena data."""
        request = action["parameters"]["request"]
        question = action["parameters"].get("question", "")
        combined = f"{request}\n(L'utente ha risposto alla domanda \"{question}\" con: {answer_text})"
        return self._run_agent(combined, remember_text=answer_text)

    def _match_meta_command(self, text: str) -> Command | None:
        return intent_patterns.match_meta_command(text, has_last_exchange=self.last_exchange is not None)

    def _execute_command(self, text: str, command: Command, learn: bool = True) -> str:
        intent = command.intent
        trace_id = new_trace_id()
        started = time.monotonic()
        if intent in self.policy_engine.blocked_intents:
            self.logger.warning("Azione bloccata da policy: %s", intent)
            self._log_action_outcome(trace_id, started, intent, command.parameters, result="blocked_by_policy")
            return f"L'azione {intent} è disabilitata nella configurazione."

        skill = self.skill_registry.get_skill(intent)
        if skill is None:
            self._log_action_outcome(trace_id, started, intent, command.parameters, result="skill_not_found")
            return f"Skill non trovata per {intent}"

        resolved, result, note = self._resolve_and_execute(command)
        if result is not None and result.error in ("CONFIRMATION_REQUIRED", "AUTH_REQUIRED"):
            reason = "auth_required" if result.error == "AUTH_REQUIRED" else "confirmation_required"
            envelope = self._safe_confirm_envelope(resolved, result, reason)
            self.conversation_state.set_pending_action({
                "intent": envelope.get("confirm_intent", resolved.intent),
                "parameters": envelope.get("confirm_parameters", resolved.parameters),
                "reason": reason,
                "text": text,
                "trace_id": trace_id,  # F1: la ricevuta della conferma si correla a questa
            })
            self._remember_exchange(text, resolved, envelope.get("message", ""))
            self._log_action_outcome(trace_id, started, resolved.intent, resolved.parameters, result=reason)
            return envelope.get("message", "Confermi questa azione?")

        response = format_skill_result(resolved.intent, result, self.skill_registry)
        if note:
            response = f"{note} {response}"
        if result is not None and result.success:
            self.conversation_state.remember_entities(resolved.intent, resolved.parameters, result.data or {})
        if learn:
            self.learning.observe(text, resolved, result, route=self.router.last_route)
        self._remember_exchange(text, resolved, response)
        outcome = "success" if (result is not None and result.success) else f"error:{result.error}" if result is not None else "no_result"
        self._log_action_outcome(trace_id, started, resolved.intent, resolved.parameters, result=outcome)
        return response

    # Un esito che non e' un vero fallimento da poter far ripartire (una conferma in attesa non
    # e' un bug), ne' un successo: session_recorder.record_failure() li ignora entrambi.
    _NOT_A_FAILURE = {"success", "confirmation_required", "auth_required"}

    def _safe_confirm_envelope(self, intent: str, parameters: dict, result: SkillResult, reason: str) -> dict:
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

    def _log_action_outcome(self, trace_id: str, started: float, intent: str, parameters: dict, *, result: str) -> None:
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
        autorizzazione derivata da authorization_of()."""
        duration_ms = (time.monotonic() - started) * 1000
        risk = risk_of(intent).value
        log_action(
            trace_id, private=self.private_mode, duration_ms=duration_ms, model=self.model,
            skill=intent, risk_decision=risk, result=result,
        )
        self.action_ledger.record(
            ActionReceipt(
                action_id=new_action_id(), trace_id=trace_id, ts=time.time(), intent=intent,
                requested_by="user", risk_decision=risk, authorization=authorization_of(result, parameters),
                result=result, idempotency_key=idempotency_key_of(intent, parameters),
                duration_ms=duration_ms, model=self.model,
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
        response = format_plan_outcome(outcome, len(plan.steps), self.skill_registry)
        self._remember_exchange(text, Command("PLAN", {"steps": len(plan.steps)}), response)
        return response

    def _handle_confirmation(self, text: str) -> str:
        action = self.conversation_state.get_pending_action()
        # Una domanda di chiarimento dell'agente non e' un si'/no: qualunque risposta la
        # prosegue (anche "si"/"no" sono risposte legittime, es. "hai salvato le modifiche?").
        if action.get("reason") == "agent_question":
            self.conversation_state.clear_pending_action()
            return self._continue_agent(action, text)

        # v5.4/5.5: un'azione ADMIN con l'autenticazione attiva aspetta la passphrase, non un
        # si'/no. Un solo tentativo per turno (come per le conferme normali, che si annullano
        # su qualunque risposta che non sia si'/no): niente tentativi ripetuti in loop.
        if action.get("reason") == "auth_required":
            self.conversation_state.clear_pending_action()
            if self.auth_gate.check(text):
                return self._finalize_pending_action(action, text)
            self._log_denied_action(action, result="denied_auth")
            return "Passphrase errata: azione annullata."

        if intent_patterns.is_positive_answer(text):
            self.conversation_state.clear_pending_action()
            return self._finalize_pending_action(action, text)
        if intent_patterns.is_negative_answer(text):
            self.conversation_state.clear_pending_action()
            self._log_denied_action(action, result="denied_confirmation")
            return "Va bene, annullato."
        # Ne' si' ne' no: l'utente e' passato ad altro. Annulla l'azione in sospeso e vai avanti.
        self.conversation_state.clear_pending_action()
        return self._process(text)

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
        self.action_ledger.record(
            ActionReceipt(
                action_id=new_action_id(), trace_id=trace_id, ts=time.time(), intent=intent,
                requested_by="user", risk_decision=risk_of(intent).value,
                authorization=authorization_of(result, parameters), result=result,
                idempotency_key=idempotency_key_of(intent, parameters),
            ),
            private=self.private_mode,
        )

    def _finalize_pending_action(self, action: dict, fallback_text: str) -> str:
        """Esegue davvero un'azione in sospeso ormai confermata/autenticata (skill_registry.
        execute diretto: il gate di _resolve_and_execute non deve scattare una seconda volta
        su qualcosa che l'utente ha appena approvato).

        F1: fino a questa correzione, l'azione VERA - quella confermata, spesso la piu'
        rischiosa (DESTRUCTIVE/ADMIN, altrimenti non avrebbe mai chiesto conferma) - non
        produceva ne' un record in jake_actions.jsonl (F0) ne' una ricevuta nel ledger (F1):
        solo la richiesta di conferma iniziale veniva registrata, non l'esecuzione dopo il si'/
        la passphrase. Scoperto verificando davvero un DELETE_PATH confermato end-to-end (non
        leggendo il codice), non un'ipotesi. trace_id viene dall'azione in sospeso (impostato da
        chi ha chiesto la conferma, vedi _execute_command/_run_agent/_handle_unknown) cosi' la
        ricevuta della conferma si correla a quella della richiesta originale nel ledger."""
        trace_id = action.get("trace_id") or new_trace_id()
        started = time.monotonic()
        result = self.skill_registry.execute(action["intent"], action["parameters"])
        # Una conferma puo' chiederne un'altra (CREATE_SKILL: "provo a imparare?" -> codice
        # scritto -> "lo attivo?"): stessa gestione del percorso normale.
        if result is not None and result.error in ("CONFIRMATION_REQUIRED", "AUTH_REQUIRED"):
            reason = "auth_required" if result.error == "AUTH_REQUIRED" else "confirmation_required"
            envelope = self._safe_confirm_envelope(action["intent"], action["parameters"], result, reason)
            self.conversation_state.set_pending_action({
                "intent": envelope.get("confirm_intent", action["intent"]),
                "parameters": envelope.get("confirm_parameters", action["parameters"]),
                "reason": reason,
                "text": action.get("text", ""),
                "trace_id": trace_id,
            })
            self._log_action_outcome(trace_id, started, action["intent"], action["parameters"], result=reason)
            return envelope.get("message", "Confermi questa azione?")
        outcome = "success" if (result is not None and result.success) else f"error:{result.error}" if result is not None else "no_result"
        self._log_action_outcome(trace_id, started, action["intent"], action["parameters"], result=outcome)
        response = format_skill_result(action["intent"], result, self.skill_registry)
        command = Command(action["intent"], action["parameters"])
        if result is not None and result.success:
            self.conversation_state.remember_entities(action["intent"], action["parameters"], result.data or {})
        if action.get("reason") in ("confirmation_required", "auth_required") and action.get("text"):
            self.learning.observe(action["text"], command, result, route="llm" if self.last_route == "llm" else "confirmed")
        self._remember_exchange(action.get("text", fallback_text), command, response)
        return response

    def _remember_exchange(self, text: str, command: Command, response: str) -> None:
        self.last_exchange = {"text": text, "command": command, "response": response}

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

    def shutdown(self) -> None:
        for component in (
            self.scheduler, self.trigger_scheduler, self.system_advisor, self.desktop_context, self.companion_server,
        ):
            try:
                component.stop()
            except Exception:
                pass
        try:
            self.retriever.example_index.save_cache()
            self.retriever.capability_index.save_cache()
        except Exception:
            pass
