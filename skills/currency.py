import json
from urllib import error

from core.network import is_online, read_url
from core.skill_result import SkillResult


class ConvertCurrencySkill:
    """Usa open.er-api.com, un servizio di cambio gratuito senza chiave richiesta (aggiornato
    quotidianamente): nessuna configurazione aggiuntiva necessaria."""

    metadata = {
        "intent": "CONVERT_CURRENCY",
        "description": "Converte un importo da una valuta a un'altra al cambio attuale.",
        "remote": True,
        "parameters": {
            "amount": {"type": "number", "required": True, "description": "L'importo da convertire."},
            "from_currency": {"type": "string", "required": True, "description": "Codice valuta di partenza, es. 'EUR', 'USD'."},
            "to_currency": {"type": "string", "required": True, "description": "Codice valuta di destinazione, es. 'USD', 'GBP'."},
        },
    }

    def __init__(self, timeout: float = 8):
        self.timeout = timeout

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        amount = parameters.get("amount")
        from_currency = (parameters.get("from_currency") or "").strip().upper()
        to_currency = (parameters.get("to_currency") or "").strip().upper()

        if not isinstance(amount, (int, float)) or not from_currency or not to_currency:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        if not is_online():
            return SkillResult(success=False, data={}, error="NETWORK_UNAVAILABLE")

        url = f"https://open.er-api.com/v6/latest/{from_currency}"
        try:
            payload = json.loads(read_url(url, self.timeout).decode("utf-8"))
        except (error.URLError, TimeoutError, json.JSONDecodeError):
            return SkillResult(success=False, data={}, error="NETWORK_UNAVAILABLE")

        # F1: stesso buco sistemico corretto in questa sessione per altri consumatori diretti di
        # API esterne - un corpo JSON valido ma non un dizionario farebbe sollevare AttributeError
        # da payload.get(...), mai catturato prima.
        if not isinstance(payload, dict):
            return SkillResult(success=False, data={"from_currency": from_currency, "to_currency": to_currency}, error="CURRENCY_NOT_FOUND")

        rates = payload.get("rates")
        rate = rates.get(to_currency) if isinstance(rates, dict) else None
        if payload.get("result") != "success" or rate is None:
            return SkillResult(success=False, data={"from_currency": from_currency, "to_currency": to_currency}, error="CURRENCY_NOT_FOUND")

        return SkillResult(success=True, data={
            "amount": amount, "from_currency": from_currency, "to_currency": to_currency,
            "result": round(amount * rate, 2),
        })
