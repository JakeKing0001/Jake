import json
from urllib import error, request

from core.language_guard import keep_reply_language
from core.skill_result import SkillResult
from core.ollama_client import DEFAULT_BASE_URL
from core.network import read_url


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

    def __init__(self, conversation_state=None, model: str = "qwen2.5:7b", base_url: str = None, timeout: float = 40):
        self.conversation_state = conversation_state
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        question = (parameters.get("question") or "").strip()
        if not question:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        answer = self._ask(question)
        if answer is None:
            return SkillResult(success=False, data={}, error="OLLAMA_UNAVAILABLE")
        kept, drifted = keep_reply_language(answer, question)
        if drifted and len(kept) < self.MIN_KEPT_CHARS:
            # la deriva e' arrivata presto: un solo nuovo tentativo, deterministico e con la lingua ribadita
            retry = self._ask(question, temperature=0.0, insist_language=True)
            if retry is not None:
                kept, _ = keep_reply_language(retry, question)
        if drifted and not kept:
            kept = "Scusa, non sono riuscito a formulare bene la risposta. Puoi ripetere la domanda?"
        return SkillResult(success=True, data={"answer": kept})

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
        history = self.conversation_state.get_short_term_history() if self.conversation_state else []
        for turn in history[-6:]:
            role = "assistant" if turn["role"] == "jake" else "user"
            messages.append({"role": role, "content": turn["text"]})
        messages.append({"role": "user", "content": question})

        payload = {"model": self.model, "stream": False,
            "keep_alive": "30m", "options": {"num_ctx": 8192, "temperature": temperature}, "messages": messages}
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            result = json.loads(read_url(http_request, self.timeout).decode("utf-8"))
            return result["message"]["content"].strip() or None
        except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError):
            # F1: buco reale (corretto insieme a core/vision_provider.py in questa sessione) -
            # un corpo JSON valido ma non nella forma attesa ("null", "[]", un numero,
            # {"message": null}) fa sollevare un TypeError da questo indicizzamento, non un
            # KeyError: senza TypeError qui, quella risposta faceva uscire un'eccezione invece
            # di degradare a None come promesso.
            return None
