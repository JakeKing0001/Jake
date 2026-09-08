"""Agente a passi (v3.1): Jake capisce COME svolgere un compito, non solo quale skill lanciare.

Il classificatore a intent singolo sceglie una capacita' e basta. Il vecchio planner scriveva
in anticipo una lista fissa di passi, senza vedere i risultati: "trova il file tesi.pdf e
aprilo" fallisce se il secondo passo non sa il percorso trovato dal primo. Qui invece Jake
lavora come un agente: pensa, esegue UN passo, osserva il risultato reale, e decide il
successivo (o conclude, o chiede all'utente il dato che manca). Le skill sono i suoi
strumenti; i risultati dei passi precedenti sono nel contesto del modello.

Usato per le richieste composte ("e poi", "e aprilo"), per quelle che il classificatore non
capisce, e quando una skill ha bisogno di un dato ricavabile da un'altra."""
import json
import time
from dataclasses import dataclass, field

from core.command import Command
from core.execution_safety import execute_with_retry, rollback_effect, verify_effect
from core.ollama_client import OllamaClient, OllamaError
from core.skill_result import SkillResult

# Strumenti sempre offerti all'agente, oltre a quelli pertinenti alla richiesta: sono i
# "sensi" e le "mani" di base con cui si risolve quasi ogni compito composto.
CORE_TOOLS = (
    "FIND_FILE", "OPEN_PATH", "OPEN_APP", "OPEN_URL", "SEARCH_IN_BROWSER", "READ_SCREEN", "DESCRIBE_SCREEN",
    "GET_ACTIVE_WINDOW", "FOCUS_WINDOW", "CLICK_TEXT", "TYPE_TEXT", "PRESS_KEY", "CLIPBOARD_READ",
    "CLIPBOARD_WRITE", "READ_FILE_TEXT", "LIST_OPEN_WINDOWS", "SET_TIMER", "SET_REMINDER",
    "ADD_NOTE", "ADD_TODO", "PLAY_MEDIA", "CLOSE_APP", "SUMMARIZE_TEXT", "TRANSLATE_TEXT",
)
# ASK_QUESTION e' deliberatamente ESCLUSA (bandita qui, non solo assente da CORE_TOOLS: cosi'
# resta fuori anche se il recupero semantico la suggerisse). E' un oracolo di conoscenza
# generale via LLM, non un modo per interpellare l'utente vero: quando era disponibile come
# azione normale, il modello la usava per "farsi una domanda da solo", si autorispondeva con
# un'ipotesi indovinata (es. "il file potrebbe trovarsi in..."), la scambiava per
# un'osservazione reale e continuava a cercare in percorsi inventati finche' non esauriva i
# passi disponibili invece di fermarsi davvero con ask_user. Osservato dal vivo su "trova il
# file X e leggilo" senza indicare la cartella: 6 passi persi tra domande e autorisposte.
NEVER_FOR_AGENT = {
    "UNKNOWN", "CHITCHAT", "ASK_QUESTION", "CORRECT_LAST", "REPEAT_LAST", "HELP", "STOP_TALKING", "PAUSE_LISTENING",
    "START_DICTATION", "STOP_DICTATION", "LEARN_COMMAND", "FORGET_LEARNED", "LIST_LEARNED", "CREATE_SKILL",
    "LIST_CREATED_SKILLS", "DELETE_CREATED_SKILL", "SET_MODEL", "SYSTEM_POWER", "RUN_COMMAND", "SHOW_HUD", "HIDE_HUD",
}
NONE_ACTION = "NONE"


@dataclass
class AgentStep:
    intent: str
    parameters: dict
    thought: str
    result: SkillResult | None = None
    observation: str = ""
    attempts: int = 1  # >1 se e' scattato un retry automatico su un errore transitorio (v3.3)


@dataclass
class AgentOutcome:
    steps: list[AgentStep] = field(default_factory=list)
    final_answer: str | None = None
    question: str | None = None            # chiarimento chiesto all'utente
    pending_confirmation: dict | None = None  # un passo ha chiesto conferma: si ferma qui
    error: str | None = None
    rolled_back: list[AgentStep] = field(default_factory=list)  # (v3.3) passi annullati dopo un errore fatale

    @property
    def did_something(self) -> bool:
        return bool(self.steps) or bool(self.final_answer) or bool(self.question)


class TaskAgent:
    MAX_STEPS = 6
    OBSERVATION_MAX_CHARS = 700
    # Budget di tempo per l'intero compito (v3.3), non solo per la singola chiamata al modello
    # (quella ha gia' il suo timeout=60 piu' sotto): un compito che continua a ragionare senza
    # concludere non deve poter tenere Jake occupato all'infinito.
    RUN_TIMEOUT_SECONDS = 90

    def __init__(self, registry, retriever, client: OllamaClient, model_provider, format_result,
                 logger=None, context_provider=None, executor=None, fixed_tools: list[str] = None,
                 persona_line: str = None):
        self.registry = registry
        self.retriever = retriever
        self.client = client
        self.model_provider = model_provider
        self.format_result = format_result
        self.logger = logger
        self.context_provider = context_provider
        # executor(intent, parameters) -> SkillResult: di default il registry (con risoluzione
        # dei percorsi); il core puo' passare una versione con policy/ripieghi.
        self.executor = executor or (lambda intent, parameters: registry.execute(intent, parameters))
        self.on_step = None  # callable(step_index, description)
        # Specializzazione (v5.0/5.1, Multi-Agent Architecture): un TaskAgent "di dominio" (es.
        # CodingAgent) usa un elenco di strumenti FISSO invece del recupero semantico generico,
        # e una prima riga di prompt diversa da quella di default. Tutto il resto - il ciclo a
        # passi, retry/verifica/rollback (core/execution_safety.py), il budget di tempo - resta
        # identico: non e' una classe diversa, e' lo stesso agente configurato diversamente.
        self.fixed_tools = fixed_tools
        self.persona_line = persona_line

    # ---- strumenti -----------------------------------------------------------------------

    def _tools(self, request: str) -> list[dict]:
        by_intent = {capability["intent"]: capability for capability in self.registry.list_capabilities()}
        if self.fixed_tools is not None:
            ordered = [intent for intent in self.fixed_tools if intent in by_intent and intent not in NEVER_FOR_AGENT]
            return [by_intent[intent] for intent in ordered[:32]]
        chosen = []
        try:
            retrieval = self.retriever.retrieve(request, max_capabilities=22, max_examples=0)
            chosen = [capability["intent"] for capability in retrieval.capabilities]
        except Exception:
            chosen = []
        ordered = []
        for intent in list(chosen) + list(CORE_TOOLS):
            if intent in by_intent and intent not in NEVER_FOR_AGENT and intent not in ordered:
                ordered.append(intent)
        return [by_intent[intent] for intent in ordered[:32]]

    @staticmethod
    def _tool_lines(tools: list[dict]) -> list[str]:
        lines = []
        for capability in tools:
            description = capability.get("description", "").split(" Usalo")[0].strip()
            lines.append(f"- {capability['intent']}: {description}")
            for name, meta in (capability.get("parameters") or {}).items():
                required = "obbligatorio" if meta.get("required") else "facoltativo"
                lines.append(f"    · {name} ({meta.get('type', 'string')}, {required}): {meta.get('description', '')}")
        return lines

    def _schema(self, tools: list[dict]) -> dict:
        parameter_properties = {}
        for capability in tools:
            for name, meta in (capability.get("parameters") or {}).items():
                parameter_properties.setdefault(name, {"type": meta.get("type", "string")} if meta.get("type") != "array" else {"type": "array", "items": {"type": "string"}})
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["thought", "action", "final_answer", "ask_user"],
            "properties": {
                "thought": {"type": "string"},
                "action": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["intent", "parameters"],
                    "properties": {
                        "intent": {"type": "string", "enum": [capability["intent"] for capability in tools] + [NONE_ACTION]},
                        "parameters": {"type": "object", "additionalProperties": False, "properties": parameter_properties},
                    },
                },
                "final_answer": {"type": "string"},
                "ask_user": {"type": "string"},
            },
        }

    def _system_prompt(self, tools: list[dict]) -> str:
        lines = [
            self.persona_line or "Sei Jake, un agente che controlla un PC Windows per conto dell'utente, in italiano.",
            "Lavori a passi: a ogni turno scegli UNA sola azione tra gli strumenti, oppure concludi.",
            "Rispondi SOLO con JSON: {\"thought\": \"...\", \"action\": {\"intent\": \"...\", \"parameters\": {...}}, \"final_answer\": \"\", \"ask_user\": \"\"}",
            "- thought: una frase breve in italiano su cosa fai e perche' (es. 'Cerco il file tesi.pdf').",
            "- Per agire: intent e parametri. Usa i risultati dei passi precedenti: es. il percorso trovato da "
            "FIND_FILE va in OPEN_PATH; il testo letto con READ_SCREEN o CLIPBOARD_READ puo' andare in SUMMARIZE_TEXT/TRANSLATE_TEXT.",
            "- Per concludere: action.intent = \"NONE\" e final_answer = frase breve, da leggere a voce, che dice cosa hai fatto "
            "e le informazioni utili trovate (numeri, nomi, percorsi). Concludi appena il compito e' completo.",
            "- Se manca un dato indispensabile che solo l'utente conosce (quale file, quale contatto, quale testo), "
            "action.intent = \"NONE\" e ask_user = una domanda breve. Non inventare mai valori.",
            "- Non ripetere un passo identico gia' eseguito. Se un passo fallisce, prova un'alternativa sensata "
            "(es. FIND_FILE in un'altra cartella, CLICK_TEXT con un'altra scritta) oppure concludi spiegando.",
            "- FIND_FILE senza 'path' cerca gia' da solo nelle cartelle piu' comuni (Desktop, Documenti, "
            "Download...): se non sai dove sia un file, prova PRIMA cosi', senza indicare 'path' e senza "
            "chiedere all'utente. Chiedi (ask_user) solo se anche questo non lo trova.",
            "- Massimo 6 passi. Percorsi 'parlati' ammessi: 'desktop', 'download', 'documenti', 'documenti\\\\tesi.pdf'.",
            "Strumenti disponibili:",
        ]
        lines.extend(self._tool_lines(tools))
        context = None
        if self.context_provider is not None:
            try:
                context = self.context_provider()
            except Exception:
                context = None
        if context:
            lines.append(f"Contesto: {context}")
        return "\n".join(lines)

    # ---- esecuzione ----------------------------------------------------------------------

    def _observe(self, intent: str, result: SkillResult | None) -> str:
        if result is None:
            return "Errore: strumento non disponibile."
        try:
            text = self.format_result(intent, result)
        except Exception:
            text = str(result.data)
        if not result.success:
            text = f"FALLITO ({result.error}): {text}"
        else:
            useful = {}
            for key in ("path", "new_path", "results", "url", "title", "text", "app", "file_count", "files", "titles"):
                if key in (result.data or {}):
                    useful[key] = result.data[key]
            if useful:
                try:
                    text += " | dati: " + json.dumps(useful, ensure_ascii=False)
                except (TypeError, ValueError):
                    pass
        if len(text) > self.OBSERVATION_MAX_CHARS:
            text = text[: self.OBSERVATION_MAX_CHARS] + "…"
        return text

    def run(self, request: str, history: list[dict] = None) -> AgentOutcome:
        outcome = AgentOutcome()
        tools = self._tools(request)
        if not tools:
            outcome.error = "NO_TOOLS"
            return outcome
        valid = {capability["intent"]: capability for capability in tools}
        model = self.model_provider()
        messages = [{"role": "system", "content": self._system_prompt(tools)}]
        for turn in (history or [])[-4:]:
            role = "assistant" if turn.get("role") == "jake" else "user"
            messages.append({"role": role, "content": turn.get("text", "")})
        messages.append({"role": "user", "content": f"Richiesta: {request}"})
        seen = set()
        start_time = time.monotonic()

        for step_index in range(1, self.MAX_STEPS + 1):
            if time.monotonic() - start_time > self.RUN_TIMEOUT_SECONDS:
                outcome.error = "TIMEOUT"
                if self.logger:
                    self.logger.warning("Agente: budget di tempo esaurito dopo %d passi per: %s", len(outcome.steps), request)
                break
            try:
                response = self.client.chat(
                    model, messages, format=self._schema(tools),
                    options={"temperature": 0, "num_predict": 300}, timeout=60,
                )
                content = response["message"]["content"]
                payload = json.loads(content) if isinstance(content, str) else content
            except (OllamaError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
                outcome.error = f"MODEL_ERROR: {type(exc).__name__}"
                if self.logger:
                    self.logger.warning("Agente: risposta non valida (%s)", exc)
                break
            if not isinstance(payload, dict):
                outcome.error = "MODEL_ERROR"
                break

            thought = str(payload.get("thought") or "").strip()
            action = payload.get("action") or {}
            intent = str(action.get("intent") or NONE_ACTION)
            parameters = action.get("parameters") if isinstance(action.get("parameters"), dict) else {}
            final_answer = str(payload.get("final_answer") or "").strip()
            ask_user = str(payload.get("ask_user") or "").strip()

            if intent == NONE_ACTION or intent not in valid:
                if ask_user:
                    outcome.question = ask_user
                elif final_answer:
                    outcome.final_answer = final_answer
                elif outcome.steps:
                    outcome.final_answer = self._summarize_steps(outcome)
                break

            # parametri: solo quelli della capacita', senza vuoti
            metadata = valid[intent].get("parameters") or {}
            parameters = {name: value for name, value in parameters.items() if name in metadata and value not in (None, "")}
            signature = (intent, json.dumps(parameters, sort_keys=True, ensure_ascii=False))
            if signature in seen:
                outcome.final_answer = final_answer or self._summarize_steps(outcome) or "Non riesco ad andare oltre."
                break
            seen.add(signature)

            missing = [name for name, meta in metadata.items() if meta.get("required") and name not in parameters]
            if missing:
                observation = f"FALLITO: mancano i parametri obbligatori {missing}. Chiedi all'utente (ask_user) se non li puoi ricavare."
                step = AgentStep(intent=intent, parameters=parameters, thought=thought, result=None, observation=observation)
            else:
                if self.on_step is not None:
                    try:
                        self.on_step(step_index, thought or valid[intent].get("description", intent).split(".")[0])
                    except Exception:
                        pass
                result, attempts = execute_with_retry(self.executor, intent, parameters)
                if result is not None and result.success and not verify_effect(intent, result.data or {}):
                    # La skill dice di aver avuto successo, ma il controllo indipendente (v1.5,
                    # oggi solo per il filesystem: vedi core/execution_safety.py) non conferma
                    # l'effetto: meglio trattarlo come fallito che riportare all'utente qualcosa
                    # che in realta' non e' successo.
                    result = SkillResult(success=False, data=result.data, error="VERIFICATION_FAILED")
                step = AgentStep(intent=intent, parameters=parameters, thought=thought, result=result, attempts=attempts)
                if result is not None and result.error == "CONFIRMATION_REQUIRED":
                    outcome.steps.append(step)
                    outcome.pending_confirmation = {
                        "intent": intent,
                        "parameters": result.data.get("confirm_parameters", parameters),
                        "message": result.data.get("message", "Confermi questa azione?"),
                    }
                    return outcome
                step.observation = self._observe(intent, result)
            outcome.steps.append(step)
            if self.logger:
                self.logger.info("Agente passo %d: %s %s -> %s", step_index, intent, parameters, step.observation[:160])

            messages.append({"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)})
            messages.append({"role": "user", "content": f"Risultato del passo {step_index} ({intent}): {step.observation}\nProssima azione, domanda all'utente, oppure conclusione con final_answer."})
        else:
            outcome.final_answer = self._summarize_steps(outcome) or "Ho fatto quello che potevo, ma non ho completato tutto."

        if outcome.error is not None and outcome.steps:
            # Il compito non e' arrivato in fondo (errore del modello, timeout...). Prima questo
            # ramo lasciava final_answer vuoto anche quando dei passi erano gia' riusciti: chi
            # chiama (JakeCore._run_agent) degradava allora a un generico "non so fare questa
            # cosa", buttando via il lavoro reale gia' fatto. Ora si riassume comunque quello che
            # e' successo, e gli effetti collaterali reversibili (vedi ROLLBACK_HANDLERS in
            # core/execution_safety.py) vengono annullati invece di restare a meta', stessa
            # filosofia gia' usata da PlanExecutor per il vecchio piano fisso.
            if not outcome.final_answer:
                outcome.final_answer = self._summarize_steps(outcome)
            outcome.rolled_back = self._rollback(outcome.steps)
            if outcome.rolled_back:
                undone = ", ".join((step.thought or step.intent.replace("_", " ").lower()) for step in outcome.rolled_back)
                prefix = f"{outcome.final_answer} " if outcome.final_answer else ""
                outcome.final_answer = f"{prefix}Ho annullato per sicurezza: {undone}."

        return outcome

    def _rollback(self, steps: list[AgentStep]) -> list[AgentStep]:
        rolled_back = []
        for step in reversed(steps):
            if step.result is None or not step.result.success:
                continue
            if rollback_effect(self.registry, step.intent, step.result.data or {}):
                rolled_back.append(step)
        return rolled_back

    def _summarize_steps(self, outcome: AgentOutcome) -> str:
        done = [step for step in outcome.steps if step.result is not None and step.result.success]
        failed = [step for step in outcome.steps if step.result is None or not step.result.success]
        parts = []
        if done:
            parts.append("Fatto: " + "; ".join((step.thought or step.intent.replace("_", " ").lower()) for step in done) + ".")
        if failed:
            parts.append("Non riuscito: " + "; ".join((step.thought or step.intent.replace("_", " ").lower()) for step in failed) + ".")
        return " ".join(parts)
