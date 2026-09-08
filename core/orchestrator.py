"""Architettura multi-agente (v5.0, con 5.1 Coding Agent e 5.2 Research Agent): instrada una
richiesta composta verso l'agente generico o verso un agente specializzato, invece di un solo
agente con accesso indiscriminato a ~200 strumenti per qualsiasi compito.

Deliberatamente POCHI agenti specializzati, come dice la roadmap ("un piccolo numero di agenti
specializzati, non decine"): oggi solo Coding e Research, perche' sono gli unici due domini con
un insieme di strumenti chiaramente diverso da quello generico e abbastanza frequente da
giustificare un prompt dedicato. Tutti e tre gli agenti sono lo STESSO TaskAgent (core/agent.py):
la specializzazione e' solo l'elenco fisso di strumenti e la prima riga del prompt (vedi
TaskAgent.fixed_tools/persona_line), non una classe o un ciclo di esecuzione diversi - quindi
retry/verifica/rollback/timeout (v3.3) valgono identici per tutti."""
import re

CODING_TOOLS = (
    "GIT_STATUS", "GIT_PULL", "GIT_LOG", "GIT_BRANCH", "GIT_DIFF", "OPEN_IN_EDITOR",
    # RUN_COMMAND NON e' incluso: e' bandito per ogni agente (vedi NEVER_FOR_AGENT in
    # core/agent.py) a prescindere dalla lista qui, un comando shell suggerito da un modello e'
    # troppo rischioso anche dietro conferma. RUN_PYTHON_SCRIPT resta, e' l'alternativa piu'
    # sicura gia' prevista per questo (non passa dalla shell di sistema).
    "RUN_PYTHON_SCRIPT", "FORMAT_JSON", "COUNT_LINES_OF_CODE", "GENERATE_UUID",
    "CHECK_PORT_IN_USE", "KILL_PROCESS_BY_PORT", "FIND_FILE", "READ_FILE_TEXT", "CREATE_PATH",
    "OPEN_PATH", "CLIPBOARD_READ", "CLIPBOARD_WRITE",
)
CODING_PERSONA = (
    "Sei Jake, un agente di sviluppo che lavora su un PC Windows per conto dell'utente, in "
    "italiano: ti occupi di codice, repository git, script e comandi da terminale."
)

RESEARCH_TOOLS = (
    "RESEARCH", "WEB_SEARCH", "SEARCH_FILES", "SEMANTIC_SEARCH_FILES", "HYBRID_SEARCH_FILES",
    "GET_NEWS", "GET_WEATHER", "GET_BROWSER_HISTORY", "SUMMARIZE_TEXT", "TRANSLATE_TEXT",
    "ADD_NOTE", "REMEMBER", "CLIPBOARD_READ",
)
RESEARCH_PERSONA = (
    "Sei Jake, un agente di ricerca per conto dell'utente, in italiano: raccogli e riassumi "
    "informazioni dal web e dai file locali, invece di eseguire azioni sul sistema."
)

# Parole che segnalano un dominio specifico abbastanza chiaramente da giustificare un agente
# dedicato invece di quello generico: false positive occasionali non sono gravi (l'agente di
# dominio ha comunque READ_FILE_TEXT/CLIPBOARD_READ/etc per cavarsela su richieste limitrofe),
# un mancato instradamento verso il generico invece perderebbe solo un prompt piu' mirato, non
# la capacita' di fare il compito.
CODING_PATTERN = re.compile(
    r"\b(git|commit|branch|repository|repo|pull request|script|codice|funzione|bug|debug|"
    r"terminale|linea di comando|comando shell|file \.py|righe di codice)\b", re.IGNORECASE,
)
RESEARCH_PATTERN = re.compile(
    r"\b(ricerca|ricercami|cerca (?:online|sul web|in rete)|approfondisci|informati|notizie|"
    r"fonti|riassumi (?:cosa|le notizie)|documentati)\b", re.IGNORECASE,
)


class JakeOrchestrator:
    def __init__(self, general_agent, coding_agent, research_agent):
        self.general_agent = general_agent
        self.coding_agent = coding_agent
        self.research_agent = research_agent

    def pick_agent(self, request: str):
        """Sceglie l'agente piu' adatto SOLO in base a segnali lessicali chiari nella richiesta:
        nessuna chiamata al modello qui, la scelta deve essere immediata ed economica."""
        if CODING_PATTERN.search(request):
            return self.coding_agent
        if RESEARCH_PATTERN.search(request):
            return self.research_agent
        return self.general_agent

    def run(self, request: str, history: list[dict] = None, trace_id: str = None, private: bool = False):
        return self.pick_agent(request).run(request, history=history, trace_id=trace_id, private=private)
