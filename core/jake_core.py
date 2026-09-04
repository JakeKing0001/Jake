import re

from core.router import Router
from core.skill_registry import SkillRegistry
from core.skill_result import SkillResult
from core.context_summarizer import ContextSummarizer
from core.logger import get_logger
from core.scheduler import ReminderScheduler
from core.desktop_context import DesktopContextTracker
from core.plugin_loader import load_plugins
from skills.model_control import ListModelsSkill, SetModelSkill


class JakeCore:
    EXIT_SENTINEL = "l'utente vuole uscire"
    # Il classificatore a singolo intent non "fallisce" su una richiesta composta: si limita a
    # sceglierne una parte e scarta il resto senza segnalarlo. Questi marcatori intercettano le
    # richieste esplicitamente multi-step PRIMA che accada, instradandole subito al planner.
    # Escluse le richieste che DEFINISCONO un'automazione (SAVE_WORKFLOW): li' l'intera frase
    # composta e' voluta dentro un solo parametro libero, non va spezzata in piu' passi subito.
    MULTI_STEP_PATTERN = re.compile(r"\b(?:e poi|poi|quindi|successivamente|dopodich[eé])\b")
    WORKFLOW_DEFINITION_PATTERN = re.compile(r"\bautomazion\w*\b|\bworkflow\b")

    def __init__(self):
        self.logger = get_logger()
        self.skill_registry = SkillRegistry()

        # Skill/plugin installabili (v2.0): un file .py in plugins/ con una funzione
        # register(registry) diventa una capacita' di Jake senza toccare il core.
        self.loaded_plugins = load_plugins(self.skill_registry, logger=self.logger)

        self.router = Router(skill_registry=self.skill_registry)
        self.conversation_state = self.skill_registry.conversation_state
        self.memory_manager = self.skill_registry.memory_manager
        self.planner_provider = self.skill_registry.planner_provider
        self.plan_executor = self.skill_registry.plan_executor
        self.context_summarizer = ContextSummarizer(model=self.router.primary_provider.model)
        config = self.skill_registry.config
        self.blocked_intents = set(config.get("blocked_intents", []) or [])
        self.always_confirm_intents = set(config.get("always_confirm_intents", []) or [])

        # Jake proattivo (v1.2): di default stampa i promemoria scaduti; chi lancia Jake
        # (CLI, voce, tray) puo' sostituire questo callback per parlarli o mostrarli come toast.
        self.reminder_manager = self.skill_registry.reminder_manager
        self.scheduler = ReminderScheduler(self.reminder_manager, on_due=self._default_on_reminder_due)
        self.scheduler.start()

        # Contestualizzazione leggera del desktop (v2.0): il classificatore e il planner
        # ricevono le finestre/app usate di recente per capire richieste ambigue.
        self.desktop_context = DesktopContextTracker()
        self.desktop_context.start()
        self.router.primary_provider.context_provider = self.desktop_context.context_summary
        self.planner_provider.context_provider = self.desktop_context.context_summary

        # Modelli intercambiabili (v2.0): registrate qui (non in SkillRegistry) perche' devono
        # tenere allineati componenti che vivono in JakeCore/Router, non solo nel registry.
        self.skill_registry.register_skill("LIST_MODELS", ListModelsSkill())
        self.skill_registry.register_skill(
            "SET_MODEL",
            SetModelSkill(
                updatable_targets=[self.router.primary_provider, self.planner_provider, self.context_summarizer],
                config=config,
            ),
        )

    def _default_on_reminder_due(self, reminder: dict) -> None:
        print(f"\nJake > Promemoria: {reminder['text']}\nTu > ", end="", flush=True)

    def answer(self, text: str):
        text = text.lower().strip()
        try:
            response = self._process(text)
        except Exception:
            # Nessuna eccezione imprevista deve mai far crashare Jake: viene registrata nel log
            # e riportata all'utente con un messaggio comprensibile invece di terminare il processo.
            self.logger.exception("Errore imprevisto elaborando: %s", text)
            response = "Mi dispiace, si è verificato un errore imprevisto. L'ho registrato nel log."

        self.conversation_state.add_turn("user", text)
        self.memory_manager.log_turn("user", text)
        if response != self.EXIT_SENTINEL:
            self.conversation_state.add_turn("jake", response)
            self.memory_manager.log_turn("jake", response)
        self.logger.info("Tu: %s | Jake: %s", text, response)

        # No-op finche' la cronologia resta sotto soglia: il riassunto scatta solo occasionalmente.
        self.memory_manager.summarize_old_history(self.context_summarizer)

        return response

    def _process(self, text: str) -> str:
        if self.conversation_state.has_pending_action():
            return self._handle_confirmation(text)

        if text == "esci" or text == "usci" or text.startswith("esci ") or text.startswith("usci "):
            return self.EXIT_SENTINEL

        if self.MULTI_STEP_PATTERN.search(text) and not self.WORKFLOW_DEFINITION_PATTERN.search(text):
            plan_response = self._try_plan(text)
            if plan_response != "Non so ancora fare questa cosa":
                return plan_response
            # la pianificazione non ha prodotto nulla di utile: ripiega sul routing normale

        command = self.router.detect_intent(text)

        # Se l'intent è sconosciuto, prova a scomporre la richiesta in più passi
        # (es. "prepara l'ambiente per lavorare su NEST") prima di arrenderti.
        if command.intent == "UNKNOWN":
            return self._try_plan(text)

        if command.intent in self.blocked_intents:
            self.logger.warning("Azione bloccata da policy: %s", command.intent)
            return f"L'azione {command.intent} è disabilitata nella configurazione."

        # Ottieni la skill dal Registry
        skill = self.skill_registry.get_skill(command.intent)

        if skill is None:
            return f"Skill non trovata per {command.intent}"

        if command.intent in self.always_confirm_intents:
            self.conversation_state.set_pending_action({
                "intent": command.intent,
                "parameters": command.parameters,
                "reason": "policy_confirmation_required",
            })
            return f"La configurazione richiede conferma per {command.intent}. Confermi?"

        result = self.skill_registry.execute(command.intent, command.parameters)
        if result.error == "CONFIRMATION_REQUIRED":
            self.conversation_state.set_pending_action({
                "intent": command.intent,
                "parameters": result.data.get("confirm_parameters", command.parameters),
                "reason": "confirmation_required",
            })
            return result.data.get("message", "Confermi questa azione?")
        return self._format_skill_result(command.intent, result)

    def _try_plan(self, text: str) -> str:
        plan = self.planner_provider.build_plan(text)
        if plan is None or len(plan.steps) < 2:
            return "Non so ancora fare questa cosa"
        outcome = self.plan_executor.execute(plan)
        return self._format_plan_outcome(outcome, len(plan.steps))

    def _format_plan_outcome(self, outcome, total_steps: int) -> str:
        lines = []
        for step_outcome in outcome.completed:
            label = step_outcome.step.description or step_outcome.step.intent
            lines.append(f"- fatto: {label}")

        if outcome.success:
            lines.insert(0, f"Ho completato i {len(outcome.completed)} passi richiesti:")
            return "\n".join(lines)

        stopped = outcome.stopped_step
        label = stopped.step.description or stopped.step.intent
        if stopped.result.error == "CONFIRMATION_REQUIRED":
            lines.append(f"- in pausa: {label}: {stopped.result.data.get('message', 'richiede conferma')}")
            lines.append("Chiedimelo singolarmente per confermare questo passo.")
        else:
            lines.append(f"- fallito: {label}: {self._format_skill_result(stopped.step.intent, stopped.result)}")
            if outcome.rolled_back:
                undone = ", ".join(o.step.description or o.step.intent for o in outcome.rolled_back)
                lines.append(f"Ho annullato i passi precedenti per sicurezza: {undone}.")

        lines.insert(0, f"Piano interrotto dopo {len(outcome.completed)} passi completati su {total_steps}:")
        return "\n".join(lines)

    def _handle_confirmation(self, text: str) -> str:
        positive_answers = {"si", "sì", "yes", "y"}
        negative_answers = {"no", "n", "annulla", "cancel"}

        if text in positive_answers:
            action = self.conversation_state.get_pending_action()
            self.conversation_state.clear_pending_action()
            result = self.skill_registry.execute(action["intent"], action["parameters"])
            return self._format_skill_result(action["intent"], result)
        if text in negative_answers:
            self.conversation_state.clear_pending_action()
            return "Va bene, annullato."
        return "Puoi rispondere sì per confermare oppure no per annullare."

    def _format_skill_result(self, intent: str, result: SkillResult) -> str:
        if not result.success:
            if result.error == "UNSUPPORTED_APP":
                app = result.data.get("app", "")
                return f"Non posso ancora aprire {app}" if app else "Applicazione non specificata"
            if result.error == "LAUNCH_FAILED":
                return f"Errore nell'apertura di {result.data.get('app', '')}"
            if result.error == "CONFIRMATION_REQUIRED":
                return result.data.get("message", "Confermi questa azione?")
            if result.error == "MISSING_PARAMETERS":
                if intent == "REMEMBER":
                    return "Dimmi cosa devo ricordare: mi servono sia il nome che il contenuto."
                if intent == "FORGET":
                    return "Dimmi cosa devo dimenticare."
                return "Mancano delle informazioni per eseguire questa azione."
            if result.error == "NOT_FOUND":
                if intent == "RECALL":
                    return "Non ho trovato nulla su questo argomento."
                if intent == "FORGET":
                    return "Non trovo nulla da dimenticare con quel nome."
                if intent in ("FIND_FILE", "SEARCH_FILES", "SEMANTIC_SEARCH_FILES"):
                    return "Non ho trovato nessun file corrispondente."
                if intent == "WEB_SEARCH":
                    return "Non ho trovato una risposta rapida per questa ricerca."
                if intent == "GET_NEWS":
                    return "Non ho trovato notizie su questo argomento."
                if intent in ("LIST_PROCESSES", "CLOSE_APP"):
                    return f"Non trovo nessun processo con '{result.data.get('name', '')}' nel nome."
                if intent == "RUN_WORKFLOW":
                    return f"Non ho un'automazione salvata chiamata '{result.data.get('name', '')}'."
                if intent == "LIST_REMINDERS":
                    return "Non hai promemoria in programma."
                if intent == "READ_SCREEN":
                    return "Non ho trovato testo leggibile sullo schermo."
                if intent == "GET_ACTIVE_WINDOW":
                    return "Non riesco a determinare la finestra attiva."
                if intent == "RESEARCH":
                    return "Non ho trovato nulla, ne' sul web ne' nei file locali, su questo argomento."
                return "Non ho trovato nulla."
            if result.error == "INVALID_TIME":
                return f"'{result.data.get('at_time', '')}' non è un orario valido (usa HH:MM)."
            if result.error == "OCR_UNAVAILABLE":
                return "Non ho un motore OCR disponibile per la lingua di questo PC."
            if result.error == "VERIFICATION_FAILED":
                return f"L'azione sembrava riuscita ma la verifica successiva non conferma l'effetto su {result.data.get('path', '')}."
            if result.error == "PATH_NOT_FOUND":
                return f"Non trovo il percorso {result.data.get('path', '')}"
            if result.error == "PROTECTED_PATH":
                return f"Non posso modificare {result.data.get('path', '')}: è una cartella protetta."
            if result.error == "ALREADY_EXISTS":
                return f"Esiste già qualcosa in {result.data.get('path', '')}"
            if result.error == "OPERATION_FAILED":
                return f"Non sono riuscito a completare l'operazione su {result.data.get('path', '')}"
            if result.error == "RESULT_NOT_FOUND":
                return "Non so quale risultato aprire: prova prima a fare una ricerca."
            if result.error == "NEST_UNAVAILABLE":
                return "NEST non è disponibile su questo computer."
            if result.error == "NEST_ERROR":
                return f"NEST ha restituito un errore: {result.data.get('message', '')}"
            if result.error == "NETWORK_UNAVAILABLE":
                return "Non ho accesso a internet in questo momento."
            if result.error == "OLLAMA_UNAVAILABLE":
                return "Non riesco a contattare Ollama in questo momento."
            if result.error == "MISSING_API_KEY":
                setting = result.data.get("setting", "")
                return f"Per usarlo devi configurare '{setting}' in config/settings.json (vedi config/settings.example.json)."
            if result.error == "CITY_NOT_FOUND":
                return f"Non trovo la città {result.data.get('city', '')}"
            if result.error == "INVALID_URL":
                return f"'{result.data.get('url', '')}' non è un indirizzo web valido."
            if result.error == "CLIPBOARD_EMPTY":
                return "Gli appunti sono vuoti o non contengono testo."
            if result.error == "WINDOW_NOT_FOUND":
                return f"Non trovo nessuna finestra con '{result.data.get('title', '')}' nel titolo."
            if result.error == "PLAN_FAILED":
                return "Non sono riuscito a scomporre questa richiesta in passi."
            return "Si è verificato un errore durante l'esecuzione"

        if intent == "REMEMBER":
            return f"Ok, ricorderò che {result.data['key']} è {result.data['value']}."
        if intent == "RECALL":
            entries = result.data["results"]
            formatted = "; ".join(f"{entry['key']}: {entry['value']}" for entry in entries)
            return f"Ecco cosa ricordo: {formatted}"
        if intent == "FORGET":
            return f"Ho dimenticato {result.data['key']}."
        if intent == "OPEN_PATH" or intent == "OPEN_SEARCH_RESULT":
            return f"Ho aperto {result.data['path']}"
        if intent == "CREATE_PATH":
            kind = "la cartella" if result.data.get("type") == "folder" else "il file"
            return f"Ho creato {kind} {result.data['path']}"
        if intent == "RENAME_PATH":
            return f"Ho rinominato {result.data['path']} in {result.data['new_path']}"
        if intent == "MOVE_PATH":
            return f"Ho spostato {result.data['path']} in {result.data['new_path']}"
        if intent == "DELETE_PATH":
            return f"Ho eliminato {result.data['path']}"
        if intent == "FIND_FILE":
            files = result.data["results"]
            return f"Ho trovato {len(files)} file: " + "; ".join(files)
        if intent == "SEARCH_FILES":
            results = result.data["results"]
            formatted = "; ".join(f"{i}. {r['path']}" for i, r in enumerate(results, start=1))
            return f"Ecco cosa ho trovato: {formatted}"
        if intent == "WEB_SEARCH":
            source = f" (fonte: {result.data['url']})" if result.data.get("url") else ""
            return f"{result.data['summary']}{source}"
        if intent == "GET_WEATHER":
            temperature = result.data.get("temperature")
            feels_like = result.data.get("feels_like")
            return (
                f"A {result.data['city']}: {result.data['description']}, {temperature}°C "
                f"(percepiti {feels_like}°C)"
            )
        if intent == "GET_NEWS":
            headlines = result.data["headlines"]
            formatted = "; ".join(f"{h['title']} ({h['source']})" for h in headlines)
            return f"Ultime notizie: {formatted}"
        if intent == "OPEN_URL":
            return f"Ho aperto {result.data['url']}"
        if intent == "CLIPBOARD_READ":
            return f"Negli appunti c'è: {result.data['text']}"
        if intent == "CLIPBOARD_WRITE":
            return "Ho copiato il testo negli appunti."
        if intent == "FOCUS_WINDOW":
            return f"Ho attivato la finestra {result.data['title']}"
        if intent == "MINIMIZE_WINDOW":
            return f"Ho minimizzato la finestra {result.data['title']}"
        if intent == "SET_VOLUME":
            labels = {"up": "alzato", "down": "abbassato", "mute": "silenziato"}
            return f"Volume {labels.get(result.data['action'], 'modificato')}."
        if intent == "LIST_PROCESSES":
            processes = result.data["processes"]
            formatted = ", ".join(f"{p['name']} (PID {p['pid']})" for p in processes)
            return f"Processi trovati: {formatted}"
        if intent == "CLOSE_APP":
            return "Ho chiuso: " + ", ".join(result.data["closed"])
        if intent == "SAVE_WORKFLOW":
            return f"Ho salvato l'automazione '{result.data['name']}' con {result.data['step_count']} passi."
        if intent == "RUN_WORKFLOW":
            return self._format_plan_outcome(result.data["outcome"], result.data["total_steps"])
        if intent == "SET_REMINDER":
            return f"Ok, alle {result.data['due_at_local']} ti ricorderò: {result.data['text']}."
        if intent == "LIST_REMINDERS":
            formatted = "; ".join(f"{r['due_at_local']}: {r['text']}" for r in result.data["reminders"])
            return f"Promemoria in programma: {formatted}"
        if intent == "TAKE_SCREENSHOT":
            return f"Screenshot salvato in {result.data['path']}"
        if intent == "READ_SCREEN":
            suffix = " (troncato)" if result.data.get("truncated") else ""
            return f"Sullo schermo leggo{suffix}: {result.data['text']}"
        if intent == "GET_ACTIVE_WINDOW":
            return f"Stai usando: {result.data['title']}"
        if intent == "CLICK_MOUSE":
            return f"Cliccato in ({result.data['x']}, {result.data['y']})"
        if intent == "MOVE_MOUSE":
            return f"Mouse spostato in ({result.data['x']}, {result.data['y']})"
        if intent == "TYPE_TEXT":
            return "Testo digitato."
        if intent == "PRESS_KEY":
            return f"Premuto: {result.data['keys']}"
        if intent == "RESEARCH":
            return result.data["synthesis"]
        if intent == "SEMANTIC_SEARCH_FILES":
            results = result.data["results"]
            formatted = "; ".join(f"{i}. {r['path']}" for i, r in enumerate(results, start=1))
            return f"Ecco cosa ho trovato per significato: {formatted}"
        if intent == "BUILD_SEMANTIC_INDEX":
            return result.data["summary"]
        if intent == "LIST_MODELS":
            return "Modelli installati: " + ", ".join(result.data["models"])
        if intent == "SET_MODEL":
            return f"Ok, ora uso il modello {result.data['model']}."

        if "time" in result.data:
            return f"Attualmente sono le {result.data['time']}"
        if "date" in result.data:
            return f"La data di oggi è {result.data['date']}"
        if "app" in result.data:
            return f"Ho aperto {result.data['app']}"

        # Punto di estensione per plugin (v2.0): una skill di terze parti puo' fornire un
        # format_result(result) proprio, senza dover modificare questo file.
        skill = self.skill_registry.get_skill(intent)
        format_result = getattr(skill, "format_result", None)
        if callable(format_result):
            return format_result(result)

        return str(result.data)