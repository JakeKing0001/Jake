import re

from core import fallbacks
from core.agent import TaskAgent
from core.command import Command
from core.context_summarizer import ContextSummarizer
from core.desktop_context import DesktopContextTracker
from core.learning_manager import LearningManager
from core.logger import get_logger
from core.nlu import chitchat
from core.nlu.examples import ExampleStore
from core.nlu.index import lexical_similarity
from core.nlu.normalizer import TranscriptNormalizer
from core.nlu.retriever import CapabilityRetriever
from core.ollama_client import OllamaClient
from core.plugin_loader import load_plugins
from core.response_formatter import format_plan_outcome, format_skill_result
from core.router import Router
from core.scheduler import ReminderScheduler
from core.session_hooks import SessionHooks
from core.skill_forge import SkillForge
from core.skill_registry import SkillRegistry
from core.skill_result import SkillResult
from core.system_advisor import SystemAdvisor
from core.trigger_scheduler import TriggerScheduler
from skills.learn import CorrectLastSkill, ForgetLearnedSkill, LearnCommandSkill, ListLearnedSkill
from skills.model_control import ListModelsSkill, SetModelSkill
from skills.session_control import (
    HelpSkill, PauseListeningSkill, RepeatLastSkill, StartDictationSkill, StopDictationSkill, StopTalkingSkill,
)
from skills.skill_forge_skills import CreateSkillSkill, DeleteCreatedSkillSkill, ListCreatedSkillsSkill


class JakeCore:
    EXIT_SENTINEL = "l'utente vuole uscire"
    NO_PLAN = "Non so ancora fare questa cosa"
    # Il classificatore a singolo intent non "fallisce" su una richiesta composta: si limita a
    # sceglierne una parte e scarta il resto senza segnalarlo. Questi marcatori intercettano le
    # richieste esplicitamente multi-step PRIMA che accada, instradandole subito al planner.
    # Escluse le richieste che DEFINISCONO un'automazione o un comando (SAVE_WORKFLOW,
    # LEARN_COMMAND): li' l'intera frase composta e' voluta dentro un solo parametro libero.
    # La sola "e" (congiunzione) conta come multi-step solo se seguita da un altro verbo
    # d'azione noto (es. "apri opera E cerca gatti"): senza questo vincolo qualunque "e" dentro
    # al contenuto di un comando singolo (es. "cerca ricette pasta e ceci") verrebbe deviata
    # inutilmente sul planner invece di restare un unico intent.
    _ACTION_VERBS = (
        "apri|aprimi|avvia|avviami|cerca|vai|crea|elimina|cancella|trova|chiudi|termina|"
        "spegni|riavvia|blocca|sospendi|alza|abbassa|aumenta|diminuisci|silenzia|muta|scrivi|"
        "ricordami|ricorda|ricordati|memorizza|dimentica|scorda|manda|invia|esegui|metti|togli|"
        "leggi|mostra|elenca|fai|cattura|scatta|salva|copia|sposta|rinomina|dimmi|dammi|clicca|premi"
    )
    MULTI_STEP_PATTERN = re.compile(
        rf"\b(?:e poi|poi|quindi|successivamente|dopodich[eé])\b|"
        rf"\be(?=\s+(?:{_ACTION_VERBS})\b)"
    )
    WORKFLOW_DEFINITION_PATTERN = re.compile(
        r"\bautomazion\w*\b|\bworkflow\b|\bquando dico\b|\bse dico\b|\bimpara\b|\bd'ora in poi\b|\bogni volta che dico\b"
    )
    # "apri youtube e cerca gatti": la seconda azione E' la prima (ricerca nel browser). Il planner
    # farebbe due passi (apri sito + cerca): meglio un solo intent, che il classificatore gestisce.
    BROWSER_COMBO_PATTERN = re.compile(
        r"^(?:apri|vai su)\s+(?:youtube|google|opera|chrome|edge|il browser|firefox|amazon|spotify)\s+e\s+(?:cerca|metti|riproduci|fammi sentire)\b"
    )
    QUESTION_PATTERN = re.compile(
        r"^(?:chi|cosa|che cosa|come|quando|dove|perch[eé]|quanto|quanti|quante|quale|quali|cos'|com'|qual|"
        r"spiegami|dimmi|raccontami|consigliami|suggeriscimi|aiutami|scrivimi|inventa|descrivi|riassumi|"
        r"sai|sapresti|potresti|puoi dirmi|mi dici|mi spieghi|secondo te)\b|\?$"
    )
    _REQUEST_VERBS = (
        "apri|aprimi|aprire|avvia|avviare|lancia|lanciare|cerca|cercare|vai|crea|creare|elimina|cancella|"
        "trova|chiudi|chiudere|termina|spegni|spegnere|riavvia|blocca|sospendi|alza|alzare|abbassa|abbassare|"
        "aumenta|diminuisci|silenzia|muta|scrivi|scrivere|ricordami|ricorda|memorizza|dimentica|manda|mandare|"
        "invia|inviare|esegui|eseguire|metti|mettere|togli|leggi|leggere|mostra|mostrami|elenca|fai|fare|cattura|"
        "scatta|salva|salvare|copia|sposta|rinomina|dimmi|dammi|dirmi|darmi|clicca|cliccare|premi|premere|imposta|"
        "impostare|attiva|attivare|disattiva|disattivare|riproduci|suona|fammi|porta|passa|minimizza|massimizza|"
        "descrivi|traduci|calcola|converti|controlla|verifica|pulisci|svuota|prendi|segna|appunta|cambia|usa|rispondi"
    )
    # "quando dico X fai Y": insegnamento deterministico, senza passare dal modello (che tende a
    # eseguire Y subito invece di imparare l'associazione).
    LEARN_PATTERNS = [
        re.compile(
            r"^(?:impara(?: che)?|ricordati che|ricorda che|d'ora in poi|da ora in poi|da adesso|da oggi|"
            r"ogni volta che|tutte le volte che)?[\s,]*(?:se|quando)\s+(?:ti\s+)?dico\s+(?P<phrase>.+?)"
            r"[\s,:]*(?:devi|dovrai|dovresti|allora|tu|fai|fa'|esegui|vuol dire che|significa che|intendo che|voglio che)?[\s,]*"
            rf"(?P<request>(?:{_REQUEST_VERBS})\b.+)$"
        ),
        re.compile(r"^impara\s*:?\s*(?P<phrase>.+?)\s*(?:=|->|=>|significa|vuol dire)\s*(?P<request>.+)$"),
    ]
    CORRECTION_PATTERNS = [
        re.compile(
            r"^(?:no|nope|sbagliato|errato|non intendevo(?: quello)?|non era quello|non volevo quello)[\s,.!]*"
            r"(?:intendevo dire|intendevo|volevo dire|volevo|dovevi|devi|era|dicevo|ho detto)\s+(?P<request>.+)$"
        ),
        re.compile(r"^(?:intendevo dire|intendevo|volevo dire|dovevi)\s+(?P<request>.+)$"),
    ]
    POSITIVE_ANSWERS = {"si", "sì", "yes", "y", "ok", "okay", "va bene", "certo", "confermo", "procedi", "vai", "esatto", "sisi", "si si", "sì sì", "conferma", "fallo", "assolutamente"}
    NEGATIVE_ANSWERS = {"no", "n", "annulla", "cancel", "lascia stare", "non farlo", "no grazie", "nope", "negativo", "ferma", "stop"}

    # Riferimenti ("aprilo", "chiudilo"): risolti col riferimento piu' recente adatto (v3.1).
    # Ancorati a tutta la frase (^...$) apposta: non devono scattare su un "quello" dentro una
    # frase piu' lunga, solo su un comando pronominale secco.
    _PRONOUN_OPTIONAL = r"\s*(?:lo|la|li|le|quello|quella|questo|questa)?$"
    _PRONOUN_OPEN = re.compile(r"^(?:apri|aprilo|aprila|aprimelo|aprimela)" + _PRONOUN_OPTIONAL)
    _PRONOUN_CLOSE = re.compile(r"^(?:chiudi|chiudilo|chiudila)" + _PRONOUN_OPTIONAL)
    _PRONOUN_READ = re.compile(r"^(?:leggi|leggilo|leggila|leggimelo|leggimela)" + _PRONOUN_OPTIONAL)
    _PRONOUN_DELETE = re.compile(r"^(?:elimina|eliminalo|eliminala|cancella|cancellalo|cancellala)" + _PRONOUN_OPTIONAL)

    def __init__(self):
        self.logger = get_logger()
        self.skill_registry = SkillRegistry()
        config = self.skill_registry.config
        self.config = config
        self.ollama = self.skill_registry.ollama_client
        self.model = config.get("ollama_model", "qwen2.5:7b")

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
        )
        self.agent.on_step = self._on_agent_step

        self.router = Router(
            skill_registry=self.skill_registry, example_store=self.example_store,
            retriever=self.retriever, client=self.ollama,
        )
        self.conversation_state = self.skill_registry.conversation_state
        self.memory_manager = self.skill_registry.memory_manager
        self.planner_provider = self.skill_registry.planner_provider
        self.plan_executor = self.skill_registry.plan_executor
        self.context_summarizer = ContextSummarizer(model=self.router.primary_provider.model)
        self.blocked_intents = set(config.get("blocked_intents", []) or [])
        self.always_confirm_intents = set(config.get("always_confirm_intents", []) or [])

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

        # Jake proattivo (v3.0): un'automazione salvata puo' far partire se stessa.
        self.trigger_scheduler = TriggerScheduler(
            self.skill_registry.trigger_manager,
            self.skill_registry.workflow_manager,
            self.plan_executor,
            self.desktop_context,
            on_trigger=self._default_on_trigger_fired,
            blocked_intents=self.blocked_intents,
            always_confirm_intents=self.always_confirm_intents,
        )
        self.trigger_scheduler.start()

        # Jake proattivo (v3.2): nota da solo batteria scarica e disco quasi pieno, senza
        # che tu debba chiederglielo (vedi core/system_advisor.py). Disattivabile da config
        # per chi lo trova invadente o lavora su un fisso senza batteria.
        self.system_advisor = SystemAdvisor(
            on_advisory=self._default_on_advisory,
            enabled=bool(config.get("system_advisor_enabled", True)),
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
            ("START_DICTATION", StartDictationSkill(self)),
            ("STOP_DICTATION", StopDictationSkill(self)),
            ("CREATE_SKILL", CreateSkillSkill(self.skill_forge)),
            ("LIST_CREATED_SKILLS", ListCreatedSkillsSkill(self.skill_forge)),
            ("DELETE_CREATED_SKILL", DeleteCreatedSkillSkill(self.skill_forge, self.learning)),
        ):
            self.skill_registry.register_skill(intent, skill)

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

    def _default_on_reminder_due(self, reminder: dict) -> None:
        print(f"\nJake > {self.format_due_reminder(reminder)}\nTu > ", end="", flush=True)

    @staticmethod
    def format_due_reminder(reminder: dict) -> str:
        if reminder.get("kind") == "timer":
            label = reminder.get("text") or "timer"
            return "Il timer è scaduto!" if label == "timer" else f"Il timer per {label} è scaduto!"
        return f"Promemoria: {reminder['text']}"

    def _default_on_advisory(self, message: str) -> None:
        print(f"\nJake > {message}\nTu > ", end="", flush=True)

    def _default_on_trigger_fired(self, trigger: dict, outcome, total_steps: int) -> None:
        summary = format_plan_outcome(outcome, total_steps, self.skill_registry)
        print(f"\nJake > Ho eseguito automaticamente '{trigger.get('name')}':\n{summary}\nTu > ", end="", flush=True)

    def _agent_context(self) -> str:
        parts = [part for part in (self.desktop_context.context_summary(), self.conversation_state.entities_summary()) if part]
        return " | ".join(parts)

    def _on_agent_step(self, step_index: int, description: str) -> None:
        self.session_hooks.call("set_state", "working", description)

    def _on_skill_installed(self, draft) -> None:
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

        if response is None:
            response = ""
        self.conversation_state.add_turn("user", text)
        self.memory_manager.log_turn("user", text)
        if response != self.EXIT_SENTINEL:
            self.conversation_state.add_turn("jake", response)
            self.memory_manager.log_turn("jake", response)
            if response:
                self.last_response = response
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

        if text in ("esci", "usci", "chiudi jake", "spegniti", "jake spegniti") or text.startswith(("esci ", "usci ")):
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

        if (
            self.MULTI_STEP_PATTERN.search(text)
            and not self.WORKFLOW_DEFINITION_PATTERN.search(text)
            and not self.BROWSER_COMBO_PATTERN.search(text)
        ):
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
        """'aprilo', 'chiudilo', 'leggilo', 'eliminalo': sostituisce il riferimento generico
        con l'ultima entita' pertinente (file, app, finestra...) ricordata da conversation_state.
        Se non c'e' nulla di adatto in memoria, lascia il testo com'era: meglio UNKNOWN (o una
        domanda dell'agente) che un valore inventato."""
        entities = self.conversation_state.get_entities()
        if not entities:
            return text
        if self._PRONOUN_OPEN.match(text):
            target = entities.get("path") or entities.get("app") or entities.get("url")
            if target:
                return f"apri {target}"
        elif self._PRONOUN_CLOSE.match(text):
            target = entities.get("app") or entities.get("title")
            if target:
                return f"chiudi {target}"
        elif self._PRONOUN_READ.match(text):
            target = entities.get("path")
            if target:
                return f"leggi il file {target}"
        elif self._PRONOUN_DELETE.match(text):
            target = entities.get("path")
            if target:
                return f"elimina {target}"
        return text

    def _resolve_and_execute(self, command: Command) -> tuple[Command, SkillResult | None, str | None]:
        """Esegue un comando applicando i ripieghi (v3.1, vedi core/fallbacks.py): riscrittura
        prima dell'esecuzione (es. OPEN_URL su un nome di app installata -> OPEN_APP), e se
        fallisce prova un'alternativa sensata o propone un'azione da confermare, invece di
        fermarsi al primo 'non trovato'. Restituisce (comando davvero eseguito, risultato, nota
        da anteporre alla risposta o None)."""
        resolved = fallbacks.pre_execution_rewrite(command, self.skill_registry)
        result = self.skill_registry.execute(resolved.intent, resolved.parameters)
        if result is not None and not result.success and result.error != "CONFIRMATION_REQUIRED":
            alt_command, note = fallbacks.alternative_for(resolved, result, self.skill_registry)
            if alt_command is not None:
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
        """Richiesta composta o non riconosciuta: l'agente pensa un passo alla volta e guarda
        i risultati veri prima di decidere il successivo (core/agent.py), invece di eseguire un
        piano fisso scritto in anticipo. Se il modello non e' raggiungibile o non conclude
        nulla, ripiega sul vecchio planner a piano fisso; se fallisce anche quello, NO_PLAN."""
        remember_text = remember_text if remember_text is not None else request
        try:
            outcome = self.agent.run(request, history=self.conversation_state.get_short_term_history())
        except Exception:
            self.logger.exception("Errore nell'agente per: %s", request)
            outcome = None

        if outcome is None or (outcome.error is not None and not outcome.did_something):
            return self._try_plan(request)

        if outcome.pending_confirmation is not None:
            self.conversation_state.set_pending_action({
                "intent": outcome.pending_confirmation["intent"],
                "parameters": outcome.pending_confirmation["parameters"],
                "reason": "confirmation_required",
                "text": remember_text,
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
        """Comandi su Jake stesso riconosciuti da regole precise (insegnare, correggere): troppo
        importanti per lasciarli al modello, che a volte esegue invece di imparare."""
        for pattern in self.LEARN_PATTERNS:
            match = pattern.match(text)
            if match:
                phrase = match.group("phrase").strip(" ,:")
                request = match.group("request").strip()
                if phrase and request:
                    return Command("LEARN_COMMAND", {"phrase": phrase, "request": request})
        for pattern in self.CORRECTION_PATTERNS:
            match = pattern.match(text)
            if match and self.last_exchange is not None:
                return Command("CORRECT_LAST", {"request": match.group("request").strip()})
        return None

    def _execute_command(self, text: str, command: Command, learn: bool = True) -> str:
        intent = command.intent
        if intent in self.blocked_intents:
            self.logger.warning("Azione bloccata da policy: %s", intent)
            return f"L'azione {intent} è disabilitata nella configurazione."

        skill = self.skill_registry.get_skill(intent)
        if skill is None:
            return f"Skill non trovata per {intent}"

        if intent in self.always_confirm_intents:
            self.conversation_state.set_pending_action({
                "intent": intent, "parameters": command.parameters, "reason": "policy_confirmation_required",
                "text": text,
            })
            return f"La configurazione richiede conferma per {intent}. Confermi?"

        resolved, result, note = self._resolve_and_execute(command)
        if result is not None and result.error == "CONFIRMATION_REQUIRED":
            self.conversation_state.set_pending_action({
                "intent": result.data.get("confirm_intent", resolved.intent),
                "parameters": result.data.get("confirm_parameters", resolved.parameters),
                "reason": "confirmation_required",
                "text": text,
            })
            self._remember_exchange(text, resolved, result.data.get("message", ""))
            return result.data.get("message", "Confermi questa azione?")

        response = format_skill_result(resolved.intent, result, self.skill_registry)
        if note:
            response = f"{note} {response}"
        if result is not None and result.success:
            self.conversation_state.remember_entities(resolved.intent, resolved.parameters, result.data or {})
        if learn:
            self.learning.observe(text, resolved, result, route=self.router.last_route)
        self._remember_exchange(text, resolved, response)
        return response

    def _handle_unknown(self, text: str) -> str:
        agent_response = self._run_agent(text)
        if agent_response != self.NO_PLAN:
            return agent_response

        if self.QUESTION_PATTERN.search(text):
            return self._execute_command(text, Command("ASK_QUESTION", {"question": text}), learn=False)

        self.learning.commit_pending()
        self._remember_exchange(text, Command("UNKNOWN", {}), self.NO_PLAN)
        if self.skill_forge.is_available():
            self.conversation_state.set_pending_action({
                "intent": "CREATE_SKILL", "parameters": {"request": text}, "reason": "offer_learn", "text": text,
            })
            return "Non so ancora fare questa cosa. Vuoi che provi a impararla da solo, scrivendomi una nuova capacità?"
        return self.NO_PLAN

    def _try_plan(self, text: str) -> str:
        plan = self.planner_provider.build_plan(text)
        if plan is None or len(plan.steps) < 2:
            return self.NO_PLAN
        outcome = self.plan_executor.execute(plan)
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

        if text in self.POSITIVE_ANSWERS:
            self.conversation_state.clear_pending_action()
            result = self.skill_registry.execute(action["intent"], action["parameters"])
            # Una conferma puo' chiederne un'altra (CREATE_SKILL: "provo a imparare?" -> codice
            # scritto -> "lo attivo?"): stessa gestione del percorso normale.
            if result is not None and result.error == "CONFIRMATION_REQUIRED":
                self.conversation_state.set_pending_action({
                    "intent": result.data.get("confirm_intent", action["intent"]),
                    "parameters": result.data.get("confirm_parameters", action["parameters"]),
                    "reason": "confirmation_required",
                    "text": action.get("text", ""),
                })
                return result.data.get("message", "Confermi questa azione?")
            response = format_skill_result(action["intent"], result, self.skill_registry)
            command = Command(action["intent"], action["parameters"])
            if result is not None and result.success:
                self.conversation_state.remember_entities(action["intent"], action["parameters"], result.data or {})
            if action.get("reason") == "confirmation_required" and action.get("text"):
                self.learning.observe(action["text"], command, result, route="llm" if self.last_route == "llm" else "confirmed")
            self._remember_exchange(action.get("text", text), command, response)
            return response
        if text in self.NEGATIVE_ANSWERS:
            self.conversation_state.clear_pending_action()
            return "Va bene, annullato."
        # Ne' si' ne' no: l'utente e' passato ad altro. Annulla l'azione in sospeso e vai avanti.
        self.conversation_state.clear_pending_action()
        return self._process(text)

    def _remember_exchange(self, text: str, command: Command, response: str) -> None:
        self.last_exchange = {"text": text, "command": command, "response": response}

    # ---- chiusura ------------------------------------------------------------------------

    def shutdown(self) -> None:
        for component in (self.scheduler, self.trigger_scheduler, self.system_advisor, self.desktop_context):
            try:
                component.stop()
            except Exception:
                pass
        try:
            self.retriever.example_index.save_cache()
            self.retriever.capability_index.save_cache()
        except Exception:
            pass
