import webbrowser
from urllib.parse import urlparse

from core.skill_result import SkillResult


class OpenUrlSkill:
    """Apre un indirizzo web nel browser predefinito (azione browser limitata, non automazione)."""

    metadata = {
        "intent": "OPEN_URL",
        "description": "Apre un indirizzo web nel browser predefinito.",
        "remote": True,
        "parameters": {
            "url": {
                "type": "string",
                "required": True,
                "description": "Indirizzo web da aprire (con o senza https://).",
            },
        },
    }

    ALLOWED_SCHEMES = {"http", "https"}
    # Prefissi di schema pericolosi da rifiutare subito: senza "//" (es. "javascript:alert(1)")
    # non verrebbero riconosciuti come schema da urlparse dopo il prefisso "https://" aggiunto
    # sotto, e passerebbero il controllo come se fossero un semplice nome di dominio non valido.
    DANGEROUS_SCHEME_PREFIXES = ("javascript:", "data:", "file:", "vbscript:")

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_url = (parameters.get("url") or "").strip()
        if not raw_url:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        lowered = raw_url.lower()
        if lowered.startswith(self.DANGEROUS_SCHEME_PREFIXES):
            return SkillResult(success=False, data={"url": raw_url}, error="INVALID_URL")

        url = raw_url if lowered.startswith(("http://", "https://")) else f"https://{raw_url}"
        parsed = urlparse(url)
        if parsed.scheme not in self.ALLOWED_SCHEMES or not parsed.netloc:
            return SkillResult(success=False, data={"url": raw_url}, error="INVALID_URL")

        try:
            opened = webbrowser.open(url)
        except Exception:
            opened = False

        if not opened:
            return SkillResult(success=False, data={"url": url}, error="OPERATION_FAILED")
        return SkillResult(success=True, data={"url": url})
