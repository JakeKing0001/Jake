"""Legge il testo di una pagina web reale (F3.6.4, "isolare testo web come non fidato" - CHIUDE
il gap dichiarato dal docstring di `core/computer_use/browser_adapter.py::read_page_text`:
"nessuna skill/intent ancora legge testo di pagina, quindi non c'e' ancora un punto di produzione
a cui collegarsi"). La PRIMA skill reale che usa `launch_isolated_browser`/`find_page_document`/
`read_page_text` (F3.6, dichiarati "additivi, non ancora usati da nessuna skill" fin dal loro
stesso docstring) - lo stesso genere di gap gia' chiuso per F3.8 da
`skills/computer_procedure.py::RunComputerProcedureSkill`.

Un browser ISOLATO (F3.6.1, mai il profilo reale dell'utente - vedi il docstring di
`launch_isolated_browser` per il rischio di privacy gia' trovato e i flag di isolamento
espliciti): questa skill legge quindi SOLO pagine PUBBLICHE, nessun cookie/sessione di login
sopravvive tra un lancio e l'altro - un limite dichiarato apertamente, non nascosto, lo stesso
genere di limite gia' accettato per VS Code/Notepad in F3.2 quando un rischio di privacy reale ha
escluso l'app dal criterio "cinque app".

`RiskLevel.READ_ONLY` (`core/risk.py`) - nessuna azione persistente sul sistema dell'utente
sopravvive (il browser e' terminato e il profilo temporaneo cancellato prima che `execute()`
ritorni, sempre in un blocco `finally`) - ma il testo restituito resta CONTENUTO ESTERNO (scritto
da chiunque possieda la pagina, non dall'utente): registrato in
`core/taint.py::EXTERNAL_CONTENT_INTENTS`, la stessa distinzione gia' fatta per `WEB_SEARCH`/
`RESEARCH` (anch'esse READ_ONLY ma taintate)."""
from urllib.parse import urlparse

from core.skill_result import SkillResult

_ALLOWED_SCHEMES = {"http", "https"}
# Stessa lista di skills/open_url.py::OpenUrlSkill - deliberatamente duplicata qui (nessun helper
# condiviso esiste ancora per una validazione cosi' piccola e specifica): uno schema pericoloso
# senza "//" (es. "javascript:alert(1)") passerebbe il controllo come un semplice nome di dominio
# non valido se non rifiutato PRIMA di anteporre "https://" sotto.
_DANGEROUS_SCHEME_PREFIXES = ("javascript:", "data:", "file:", "vbscript:")

# Stesso ordine di grandezza gia' usato altrove in questo progetto per testo esterno grezzo (F1.5)
# - una pagina web puo' essere arbitrariamente lunga, un limite evita un'osservazione enorme che
# consumerebbe da sola gran parte del contesto del modello.
_MAX_TEXT_CHARS = 8000


class ReadWebPageSkill:
    metadata = {
        "intent": "READ_WEB_PAGE",
        "description": "Legge il testo visibile di una pagina web pubblica (non richiede un login) "
        "aprendola in un browser isolato e restituendone il contenuto testuale. Usalo quando "
        "l'utente chiede di leggere, riassumere o controllare il contenuto di una pagina "
        "specifica di cui da' l'indirizzo - diverso da WEB_SEARCH (una risposta rapida senza "
        "aprire alcuna pagina) e da OPEN_URL (apre solo il browser per l'utente, non legge nulla).",
        "remote": True,
        "parameters": {
            "url": {
                "type": "string",
                "required": True,
                "description": "Indirizzo della pagina da leggere (con o senza https://).",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_url = (parameters.get("url") or "").strip()
        if not raw_url:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        lowered = raw_url.lower()
        if lowered.startswith(_DANGEROUS_SCHEME_PREFIXES):
            return SkillResult(success=False, data={"url": raw_url}, error="INVALID_URL")
        url = raw_url if lowered.startswith(("http://", "https://")) else f"https://{raw_url}"
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
            return SkillResult(success=False, data={"url": raw_url}, error="INVALID_URL")

        from core.computer_use.browser_adapter import (
            BrowserNotFoundError,
            find_edge_executable,
            find_page_document,
            launch_isolated_browser,
            read_page_text,
    find_isolated_browser_window,
)
        from core.computer_use.ui_automation_adapter import UIAutomationAdapter, WindowNotFoundError

        try:
            find_edge_executable()
        except BrowserNotFoundError:
            return SkillResult(success=False, data={"url": url}, error="BROWSER_UNAVAILABLE")

        browser = launch_isolated_browser(url)
        try:
            adapter = UIAutomationAdapter()
            try:
                window = find_isolated_browser_window(adapter, browser, timeout_seconds=25.0)
            except WindowNotFoundError:
                return SkillResult(success=False, data={"url": url}, error="OPERATION_FAILED")
            document = find_page_document(adapter, window)
            text = read_page_text(adapter, document)
        finally:
            # SEMPRE, anche se una ricerca sopra ha sollevato - stesso principio "chi lancia un
            # processo reale lo ripulisce" gia' seguito da ogni test di questa sessione (F3.6.1),
            # qui applicato per la prima volta in codice di PRODUZIONE, non solo nei test.
            browser.terminate_and_cleanup()

        if not text:
            return SkillResult(success=False, data={"url": url}, error="NOT_FOUND")
        truncated = len(text) > _MAX_TEXT_CHARS
        if truncated:
            text = text[:_MAX_TEXT_CHARS]
        return SkillResult(success=True, data={"url": url, "text": text, "truncated": truncated})
