import json
import re
import secrets
import string
from urllib import error, request

from core.skill_result import SkillResult
from core.ollama_client import DEFAULT_BASE_URL

_ROMAN_VALUES = [
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
    (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
]
_ROMAN_TO_VALUE = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

_MORSE_TABLE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.",
    "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.",
    "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-", "U": "..-",
    "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.", " ": "/",
}
_MORSE_TO_LETTER = {value: key for key, value in _MORSE_TABLE.items()}

_UNITS = ["", "uno", "due", "tre", "quattro", "cinque", "sei", "sette", "otto", "nove"]
_TEENS = ["dieci", "undici", "dodici", "tredici", "quattordici", "quindici", "sedici", "diciassette", "diciotto", "diciannove"]
_TENS = ["", "", "venti", "trenta", "quaranta", "cinquanta", "sessanta", "settanta", "ottanta", "novanta"]


def _number_to_italian_words(n: int) -> str:
    """Copre 0-999999: oltre non serve per un assistente vocale (nessuno detta 'ventitremila
    ...' per un numero a caso), e complicare oltre non aggiungerebbe valore reale."""
    if n == 0:
        return "zero"
    if n < 0:
        return "meno " + _number_to_italian_words(-n)

    def under_hundred(value: int) -> str:
        if value < 10:
            return _UNITS[value]
        if value < 20:
            return _TEENS[value - 10]
        tens, unit = divmod(value, 10)
        word = _TENS[tens]
        if unit in (1, 8):
            word = word[:-1]
        return word + _UNITS[unit] if unit else word

    def under_thousand(value: int) -> str:
        if value < 100:
            return under_hundred(value)
        hundreds, rest = divmod(value, 100)
        prefix = "cento" if hundreds == 1 else _UNITS[hundreds] + "cento"
        return prefix + (under_hundred(rest) if rest else "")

    if n < 1000:
        return under_thousand(n)
    thousands, rest = divmod(n, 1000)
    prefix = "mille" if thousands == 1 else under_thousand(thousands) + "mila"
    return prefix + (under_thousand(rest) if rest else "")


class CountWordsSkill:
    metadata = {
        "intent": "COUNT_WORDS",
        "description": "Conta parole e caratteri in un testo dato direttamente (non un file). Diverso da COUNT_WORDS_IN_FILE.",
        "parameters": {
            "text": {"type": "string", "required": True, "description": "Il testo da analizzare."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = parameters.get("text") or ""
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        return SkillResult(success=True, data={"words": len(text.split()), "characters": len(text)})


class ConvertCaseSkill:
    metadata = {
        "intent": "CONVERT_CASE",
        "description": "Converte un testo in maiuscolo, minuscolo o con iniziali maiuscole.",
        "parameters": {
            "text": {"type": "string", "required": True, "description": "Il testo da convertire."},
            "style": {"type": "string", "required": True, "description": "Uno tra: 'upper', 'lower', 'title'."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = parameters.get("text") or ""
        style = (parameters.get("style") or "").strip().lower()
        if not text or style not in ("upper", "lower", "title"):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        converted = {"upper": text.upper(), "lower": text.lower(), "title": text.title()}[style]
        return SkillResult(success=True, data={"text": converted})


class GeneratePasswordSkill:
    metadata = {
        "intent": "GENERATE_PASSWORD",
        "description": "Genera una password casuale sicura.",
        "parameters": {
            "length": {"type": "integer", "required": False, "description": "Lunghezza della password (default 16)."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        length = max(8, min(64, int(parameters.get("length") or 16)))
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        return SkillResult(success=True, data={"password": password})


class _OllamaTextSkill:
    """Base comune per le skill di testo che delegano a Ollama in chat singola: stesso schema
    di chiamata di ASK_QUESTION/TRANSLATE_TEXT, senza duplicarlo in ogni sottoclasse."""

    def __init__(self, model: str = "qwen2.5:7b", base_url: str = None, timeout: float = 30):
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def _complete(self, system_prompt: str, user_text: str) -> str | None:
        payload = {
            "model": self.model,
            "stream": False,
            "keep_alive": "30m",
            "options": {"num_ctx": 8192, "temperature": 0.2},
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}],
        }
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            return result["message"]["content"].strip() or None
        except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError):
            # F1: stesso buco corretto in core/vision_provider.py/skills/ask_question.py in
            # questa sessione - un corpo JSON valido ma non nella forma attesa fa sollevare un
            # TypeError da questo indicizzamento, non un KeyError.
            return None


class ProofreadTextSkill(_OllamaTextSkill):
    metadata = {
        "intent": "PROOFREAD_TEXT",
        "description": "Corregge grammatica e ortografia di un testo, mantenendone il significato.",
        "remote": True,
        "parameters": {
            "text": {"type": "string", "required": True, "description": "Il testo da correggere."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        corrected = self._complete(
            "Correggi grammatica, ortografia e punteggiatura del testo dell'utente, mantenendo il "
            "significato e lo stile. Rispondi SOLO col testo corretto, senza spiegazioni.",
            text,
        )
        if corrected is None:
            return SkillResult(success=False, data={}, error="OLLAMA_UNAVAILABLE")
        return SkillResult(success=True, data={"text": corrected})


class SummarizeTextSkill(_OllamaTextSkill):
    metadata = {
        "intent": "SUMMARIZE_TEXT",
        "description": "Riassume un testo dato direttamente dall'utente. Diverso da SUMMARIZE_CLIPBOARD, che legge dagli appunti.",
        "remote": True,
        "parameters": {
            "text": {"type": "string", "required": True, "description": "Il testo da riassumere."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        summary = self._complete(
            "Riassumi in italiano, in massimo 4 frasi, il testo fornito. Rispondi solo col riassunto.",
            text,
        )
        if summary is None:
            return SkillResult(success=False, data={}, error="OLLAMA_UNAVAILABLE")
        return SkillResult(success=True, data={"summary": summary})


class DetectLanguageSkill(_OllamaTextSkill):
    metadata = {
        "intent": "DETECT_LANGUAGE",
        "description": "Identifica in che lingua e' scritto un testo.",
        "remote": True,
        "parameters": {
            "text": {"type": "string", "required": True, "description": "Il testo di cui identificare la lingua."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        language = self._complete(
            "Rispondi SOLO col nome della lingua in cui e' scritto il testo dell'utente, in "
            "italiano e in una sola parola (es. 'inglese', 'francese').",
            text,
        )
        if language is None:
            return SkillResult(success=False, data={}, error="OLLAMA_UNAVAILABLE")
        return SkillResult(success=True, data={"language": language})


class ExtractUrlsFromTextSkill:
    metadata = {
        "intent": "EXTRACT_URLS_FROM_TEXT",
        "description": "Estrae tutti i link (URL) presenti in un testo.",
        "parameters": {
            "text": {"type": "string", "required": True, "description": "Il testo da cui estrarre i link."},
        },
    }

    URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = parameters.get("text") or ""
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        urls = self.URL_PATTERN.findall(text)
        if not urls:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"urls": urls})


class ConvertNumberToWordsSkill:
    metadata = {
        "intent": "CONVERT_NUMBER_TO_WORDS",
        "description": "Scrive un numero in lettere (es. 42 -> 'quarantadue').",
        "parameters": {
            "number": {"type": "integer", "required": True, "description": "Il numero da scrivere in lettere."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        number = parameters.get("number")
        if not isinstance(number, int) or abs(number) > 999999:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        return SkillResult(success=True, data={"number": number, "words": _number_to_italian_words(number)})


class ConvertRomanNumeralSkill:
    metadata = {
        "intent": "CONVERT_ROMAN_NUMERAL",
        "description": "Converte un numero in numero romano, o un numero romano nel suo valore numerico.",
        "parameters": {
            "value": {"type": "string", "required": True, "description": "Un numero (es. '49') o un numero romano (es. 'XLIX')."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        value = (parameters.get("value") or "").strip().upper()
        if not value:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        if value.isdigit():
            number = int(value)
            if not (0 < number < 4000):
                return SkillResult(success=False, data={"value": value}, error="INVALID_VALUE")
            result = ""
            remaining = number
            for amount, numeral in _ROMAN_VALUES:
                while remaining >= amount:
                    result += numeral
                    remaining -= amount
            return SkillResult(success=True, data={"input": value, "result": result})

        if all(char in _ROMAN_TO_VALUE for char in value):
            total = 0
            previous = 0
            for char in reversed(value):
                current = _ROMAN_TO_VALUE[char]
                total += current if current >= previous else -current
                previous = current
            return SkillResult(success=True, data={"input": value, "result": str(total)})

        return SkillResult(success=False, data={"value": value}, error="INVALID_VALUE")


class ConvertMorseCodeSkill:
    metadata = {
        "intent": "CONVERT_MORSE_CODE",
        "description": "Converte un testo in codice Morse, o del codice Morse in testo.",
        "parameters": {
            "text": {"type": "string", "required": True, "description": "Il testo da convertire, o il codice Morse (punti/linee separati da spazi)."},
            "direction": {"type": "string", "required": True, "description": "Uno tra: 'to_morse', 'from_morse'."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        direction = (parameters.get("direction") or "").strip().lower()
        if not text or direction not in ("to_morse", "from_morse"):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        if direction == "to_morse":
            result = " ".join(_MORSE_TABLE.get(char.upper(), "") for char in text)
        else:
            result = "".join(_MORSE_TO_LETTER.get(code, "") for code in text.split())
            result = result.replace("/", " ")

        return SkillResult(success=True, data={"result": result})
