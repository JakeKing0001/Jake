"""Conversazione di cortesia (v3.0). Le frasi standard hanno risposte immediate (core.nlu.chitchat);
per il resto una risposta breve dal modello, con la personalita' di Jake."""
from core.nlu import chitchat
from core.ollama_client import OllamaClient
from core.skill_result import SkillResult


class ChitChatSkill:
    metadata = {
        "intent": "CHITCHAT",
        "description": "Risponde a saluti, ringraziamenti, complimenti, 'come stai', 'chi sei', 'ci sei?' "
        "e commenti senza una richiesta concreta. Usalo SOLO per frasi di cortesia o conversazione "
        "leggera, mai per domande di conoscenza (ASK_QUESTION) o comandi.",
        "parameters": {
            "text": {"type": "string", "required": True, "description": "La frase dell'utente, cosi' com'e'."},
        },
    }

    PERSONA = (
        "Sei Jake, assistente vocale personale su un PC Windows: sveglio, cordiale, un po' ironico ma "
        "sempre pertinente e gentile, mai prolisso. Rispondi in italiano in UNA frase breve (massimo due), "
        "senza markdown, come una battuta di dialogo da leggere ad alta voce. Se la frase non ha senso "
        "o e' un frammento, rispondi semplicemente che ci sei e chiedi cosa serve."
    )

    def __init__(self, client: OllamaClient = None, model: str = "qwen2.5:7b"):
        self.client = client or OllamaClient(timeout=20)
        self.model = model

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        answer = chitchat.reply(text)
        if answer is None and text:
            answer = self.client.chat_text(
                self.model,
                [{"role": "system", "content": self.PERSONA}, {"role": "user", "content": text}],
                options={"temperature": 0.4, "num_predict": 60},
                timeout=20,
            )
        if not answer:
            answer = "Ci sono. Dimmi pure."
        return SkillResult(success=True, data={"reply": answer})
