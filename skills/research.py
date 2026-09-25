import json
from urllib import error, request

from core.skill_result import SkillResult
from core.ollama_client import DEFAULT_BASE_URL
from core.network import read_url


class ResearchSkill:
    """Research agent (v1.5): combina ricerca web e ricerca locale (NEST) e sintetizza una
    risposta unica, invece di limitarsi a incatenare gli strumenti come fa il Planner generico."""

    metadata = {
        "intent": "RESEARCH",
        "description": (
            "Fa una ricerca approfondita su un argomento, combinando web e file locali, "
            "e sintetizza una risposta unica invece di un elenco di risultati grezzi."
        ),
        "remote": True,
        "parameters": {
            "topic": {
                "type": "string",
                "required": True,
                "description": "Argomento su cui fare ricerca.",
            },
        },
    }

    def __init__(self, web_search_skill, search_files_skill, model: str, base_url: str = None, timeout: float = 40):
        self.web_search_skill = web_search_skill
        self.search_files_skill = search_files_skill
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        topic = (parameters.get("topic") or "").strip()
        if not topic:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        sources = []

        web_result = self.web_search_skill.execute({"query": topic})
        if web_result.success:
            sources.append(f"Dal web: {web_result.data['summary']}")

        file_result = self.search_files_skill.execute({"query": topic})
        if file_result.success:
            paths = ", ".join(entry["path"] for entry in file_result.data["results"][:5])
            sources.append(f"File locali rilevanti trovati con NEST: {paths}")

        if not sources:
            return SkillResult(success=False, data={"topic": topic}, error="NOT_FOUND")

        synthesis = self._synthesize(topic, sources)
        return SkillResult(success=True, data={"topic": topic, "synthesis": synthesis or "; ".join(sources)})

    def _synthesize(self, topic: str, sources: list) -> str | None:
        payload = {
            "model": self.model,
            "stream": False,
            "keep_alive": "30m",
            "options": {"num_ctx": 8192, "temperature": 0.3},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Sintetizza in italiano, in massimo 4 frasi, una risposta alla domanda "
                        "dell'utente usando solo le fonti fornite. Se le fonti non bastano a "
                        "rispondere con sicurezza, dillo chiaramente invece di inventare."
                    ),
                },
                {"role": "user", "content": f"Argomento: {topic}\n\nFonti:\n" + "\n".join(sources)},
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            result = json.loads(read_url(http_request, self.timeout).decode("utf-8"))
            return result["message"]["content"].strip() or None
        except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError):
            # F1: stesso buco corretto in core/vision_provider.py/skills/ask_question.py in
            # questa sessione - un corpo JSON valido ma non nella forma attesa fa sollevare un
            # TypeError da questo indicizzamento, non un KeyError.
            return None
