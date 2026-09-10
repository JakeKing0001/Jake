import json
from urllib import error, request

from core.skill_result import SkillResult
from core.ollama_client import DEFAULT_BASE_URL


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
        return SkillResult(success=True, data={"answer": answer})

    def _ask(self, question: str) -> str | None:
        messages = [
            {
                "role": "system",
                "content": (
                    "Sei Jake, un assistente vocale personale. Rispondi in italiano in modo "
                    "chiaro, utile e conciso: la risposta viene letta ad alta voce, quindi evita "
                    "markdown, elenchi puntati o formattazione, preferendo 2-5 frasi a meno che "
                    "l'utente non chieda esplicitamente piu' dettaglio."
                ),
            }
        ]
        history = self.conversation_state.get_short_term_history() if self.conversation_state else []
        for turn in history[-6:]:
            role = "assistant" if turn["role"] == "jake" else "user"
            messages.append({"role": role, "content": turn["text"]})
        messages.append({"role": "user", "content": question})

        payload = {"model": self.model, "stream": False,
            "keep_alive": "30m", "options": {"num_ctx": 8192, "temperature": 0.4}, "messages": messages}
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            return result["message"]["content"].strip() or None
        except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError):
            # F1: buco reale (corretto insieme a core/vision_provider.py in questa sessione) -
            # un corpo JSON valido ma non nella forma attesa ("null", "[]", un numero,
            # {"message": null}) fa sollevare un TypeError da questo indicizzamento, non un
            # KeyError: senza TypeError qui, quella risposta faceva uscire un'eccezione invece
            # di degradare a None come promesso.
            return None
