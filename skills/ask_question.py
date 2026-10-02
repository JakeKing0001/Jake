import json
from urllib import error, request

from core.language_guard import keep_reply_language
from core.skill_result import SkillResult
from core.ollama_client import DEFAULT_BASE_URL
from core.network import read_url
from core.ollama_client import keep_alive_for


class AskQuestionSkill:
    """Conversazione libera e domande di conoscenza generale ('modalita' Jarvis'): quando nessuna
    capacita' piu' specifica risponde alla richiesta, Jake usa Ollama in chat libera invece di
    rispondere 'non so ancora fare questa cosa'. E' l'unica capacita' pensata per essere scelta
    dal classificatore come ripiego generale (vedi metadata['description'])."""

    metadata = {
        "intent": "ASK_QUESTION",
        "description": (
            "Risponde a domande di conoscenza generale, spiegazioni, richieste di aiuto per "
            "scrivere/riformulare un testo, o conversazione libera che non corrisponde a nessuna "
            "capacita' piu' specifica elencata qui. Usala come ripiego generale al posto di "
            "UNKNOWN quando la richiesta e' comunque una domanda o richiesta di aiuto legittima "
            "(es. 'qual e' la capitale della Francia', 'spiegami la fotosintesi', 'aiutami a "
            "scrivere una mail di scuse per un ritardo')."
        ),
        "remote": True,
        "parameters": {
            "question": {
                "type": "string",
                "required": True,
                "description": "La domanda o richiesta dell'utente, con le stesse parole usate.",
            },
        },
    }

    def __init__(self, conversation_state=None, model: str = "qwen2.5:7b", base_url: str = None, timeout: float = 40,
                 memory_manager=None, embedding_provider=None, dashboard=None):
        self.conversation_state = conversation_state
        self.model = model
        # F5.5/F5.6: le risposte libere usano i ricordi pertinenti (con fonte), non solo il comando RECALL.
        self.memory_manager = memory_manager
        self.embedding_provider = embedding_provider
        self.dashboard = dashboard
        self.private_mode_provider = lambda: False
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    _failure = None

    def _model_failure_result(self) -> SkillResult:
        """Il motivo vero, non sempre "non riesco a contattare Ollama" (prova reale del 27/09/2026: Ollama rispondeva,
        era la GPU piena a renderlo lentissimo)."""
        failed = self._failure
        if failed is None or failed.kind == "server_error":
            return SkillResult(success=False, data={}, error="MODEL_ERROR")
        if failed.kind == "timeout":
            return SkillResult(success=False, data={"seconds": self.timeout, "hint": failed.hint}, error="MODEL_TIMEOUT")
        if failed.kind == "model_missing":
            return SkillResult(success=False, data={"model": self.model}, error="MODEL_MISSING")
        return SkillResult(success=False, data={}, error="OLLAMA_UNAVAILABLE")

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        question = (parameters.get("question") or "").strip()
        if not question:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        memories = self._relevant_memories(question)
        self._memories = memories
        self._failure = None
        answer = self._ask(question)
        if answer is None:
            return self._model_failure_result()
        answer, cited = self._cite(answer, memories, question)
        kept, drifted = keep_reply_language(answer, question)
        if drifted and len(kept) < self.MIN_KEPT_CHARS:
            # la deriva e' arrivata presto: un solo nuovo tentativo, deterministico e con la lingua ribadita
            retry = self._ask(question, temperature=0.0, insist_language=True)
            if retry is not None:
                kept, _ = keep_reply_language(retry, question)
                kept, cited = self._cite(kept, memories, question)
        if drifted and not kept:
            kept = "Scusa, non sono riuscito a formulare bene la risposta. Puoi ripetere la domanda?"
        if cited and kept:
            from core.response_formatter import memory_citation

            self._record_use(cited)
            kept = f"{kept}\n{memory_citation(cited)}"
        return SkillResult(success=True, data={"answer": kept, "memories_used": [m["key"] for m in cited]})

    MAX_MEMORY_CHARS = 700

    def _relevant_memories(self, question: str) -> list[dict]:
        if self.memory_manager is None or not hasattr(self.memory_manager, "relevant_for"):
            return []
        try:
            memories = self.memory_manager.relevant_for(question, budget_chars=self.MAX_MEMORY_CHARS)
            if not memories and self.embedding_provider is not None:
                embedding = self.embedding_provider.embed(question)
                if embedding is not None:
                    memories = self.memory_manager.relevant_for(question, query_embedding=embedding,
                                                                budget_chars=self.MAX_MEMORY_CHARS)
            return memories
        except Exception:
            return []  # la memoria non deve mai impedire di rispondere

    @staticmethod
    def _cite(answer: str, memories: list[dict], question: str = "") -> tuple[str, list[dict]]:
        """Quali ricordi la risposta ha usato davvero: il valore del ricordo citato alla lettera, oppure il marcatore
        [M#] chiesto al modello PIU' almeno una parola propria del ricordo nella risposta (una che non sia gia' nella
        domanda: un modello piccolo scrive [M1] anche quando non usa nulla). I marcatori si tolgono dal testo (la
        risposta viene letta ad alta voce)."""
        import re

        def content_words(text: str) -> set[str]:
            return {word for word in re.findall(r"\w+", text.lower()) if len(word) >= 4}

        cited_indexes = {int(n) for n in re.findall(r"\[M(\d+)\]", answer)}
        clean = re.sub(r"\s*\[M\d+\]", "", answer).strip()
        answer_words, question_words = content_words(clean), content_words(question)
        cited = []
        for index, memory in enumerate(memories, start=1):
            value = str(memory.get("value") or "").strip().lower()
            literal = len(value) >= 4 and value in clean.lower()
            supported = bool((content_words(value) - question_words) & answer_words)
            if literal or (index in cited_indexes and supported):
                cited.append(memory)
        return clean, cited

    def _record_use(self, cited: list[dict]) -> None:
        if self.dashboard is None or self.private_mode_provider():
            return
        for memory in cited:
            try:
                self.dashboard.record_use(memory["key"], memory.get("category", "fact"), actor="jake", context="answer")
            except Exception:
                pass

    # Sotto questa lunghezza la parte italiana rimasta dopo una deriva di lingua non basta come risposta.
    MIN_KEPT_CHARS = 80

    def _ask(self, question: str, temperature: float = 0.4, insist_language: bool = False) -> str | None:
        messages = [
            {
                "role": "system",
                "content": (
                    "Sei Jake, un assistente vocale personale. Rispondi in italiano in modo "
                    "chiaro, utile e conciso: la risposta viene letta ad alta voce, quindi evita "
                    "markdown, elenchi puntati o formattazione, preferendo 2-5 frasi a meno che "
                    "l'utente non chieda esplicitamente piu' dettaglio. Scrivi tutta la risposta in "
                    "italiano, dall'inizio alla fine, salvo che l'utente chieda esplicitamente "
                    "un'altra lingua; non usare mai caratteri cinesi, giapponesi o coreani."
                    + (" Attenzione: la risposta precedente e' passata a un'altra lingua. Resta in "
                       "italiano per tutta la risposta." if insist_language else "")
                ),
            }
        ]
        memories = getattr(self, "_memories", None) or []
        if memories:
            from core.response_formatter import memory_provenance

            lines = "\n".join(f"[M{i}] {m['key']}: {m['value']}{memory_provenance(m)}" for i, m in enumerate(memories, start=1))
            messages.append({"role": "system", "content": (
                "Ricordi dell'utente che potrebbero servire (usali SOLO se pertinenti, non inventare altro sull'utente; "
                "se ne usi uno scrivi il suo marcatore, per esempio [M1], subito dopo l'informazione):\n" + lines)})
        history = self.conversation_state.get_short_term_history() if self.conversation_state else []
        for turn in history[-6:]:
            role = "assistant" if turn["role"] == "jake" else "user"
            messages.append({"role": role, "content": turn["text"]})
        messages.append({"role": "user", "content": question})

        payload = {"model": self.model, "stream": False,
            "keep_alive": keep_alive_for(self.model), "options": {"num_ctx": 8192, "temperature": temperature}, "messages": messages}
        from core import model_health

        failed = model_health.failure()
        if failed is not None:  # stesso turno, stesso problema: nessuna nuova attesa
            self._failure = failed
            return None
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with model_health.calling():
                result = json.loads(read_url(http_request, self.timeout).decode("utf-8"))
            return result["message"]["content"].strip() or None
        except (error.URLError, TimeoutError, OSError) as exc:
            kind = model_health.classify(exc)
            self._failure = (model_health.ModelFailure(kind) if kind == model_health.MODEL_MISSING
                             else model_health.diagnose(self.base_url, kind))
            model_health.record(self._failure)
            return None
        except (json.JSONDecodeError, KeyError, TypeError):
            # F1: buco reale (corretto insieme a core/vision_provider.py in questa sessione) -
            # un corpo JSON valido ma non nella forma attesa ("null", "[]", un numero,
            # {"message": null}) fa sollevare un TypeError da questo indicizzamento, non un
            # KeyError: senza TypeError qui, quella risposta faceva uscire un'eccezione invece
            # di degradare a None come promesso.
            return None
