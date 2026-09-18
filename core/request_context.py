"""Contesto per-richiesta thread-safe (F1.2.3/F1.8.1, fondamenta): propaga l'identita' del
dispositivo companion mittente lungo l'intera catena di chiamate sincrona (companion_server ->
JakeCore.answer -> _resolve_and_execute/TaskAgent/PlanExecutor -> ActionLedger) SENZA aggiungere
un parametro `device_id` a ogni metodo intermedio - decine di firme (answer, _process,
_handle_confirmation, _finalize_pending_action, _resolve_and_execute, _run_agent, _try_plan...)
avrebbero dovuto propagarlo a mano solo per farlo arrivare ai quattro chokepoint che scrivono
davvero una ActionReceipt.

Usa `contextvars.ContextVar` invece di un attributo di istanza su JakeCore (es.
`self.current_device_id`): JakeCore e' UN'unica istanza condivisa sia dal loop voce sia da
core/companion_server.py (un `ThreadingHTTPServer` - ogni richiesta HTTP gira sul proprio thread),
quindi un attributo mutabile sarebbe una race condition tra due richieste concorrenti da
dispositivi diversi - esattamente la classe di buco gia' trovata e corretta piu' volte in questa
sessione (F1.8.1, F1.8.7). Un `ContextVar` e' invece isolato per thread by design: un thread
nuovo (ogni richiesta di un `ThreadingHTTPServer` ne crea uno) parte SEMPRE dal valore di default,
mai da quello impostato da un altro thread in corso - verificato empiricamente, non solo assunto
dalla documentazione, prima di scegliere questo approccio.

None (il default, e il comportamento di chi non chiama mai `set_current_device_id`) significa
"nessun dispositivo companion noto per questa richiesta" - il caso normale per il loop voce
locale, che resta un canale implicito separato (nessuna modifica li'), e per le automazioni
lanciate da TriggerScheduler sul proprio thread in background.

F1.2.3 (intersezione, capability per AGENTE): stesso identico principio/meccanismo, ma per
`TaskAgent.agent_name` ("general"/"coding"/"research", vedi core/agent.py) invece che per
dispositivo companion - `TaskAgent.run()` imposta il contextvar SOLO intorno alla chiamata
all'executor (l'unico punto in cui un passo dell'agente puo' davvero eseguire un intent), non per
l'intera durata di `run()`: il resto del metodo (chiamare il modello, decidere il prossimo passo)
non ha bisogno di sapere quale agente sta girando, e restringere la finestra al minimo evita che
un futuro codice intermedio legga per errore un valore che non gli compete. `None` (il default)
significa "nessun agente a passi in corso su questo thread" - il caso normale per un comando
diretto (JakeCore._resolve_and_execute chiamato senza passare da TaskAgent) o un'automazione
(PlanExecutor, un attore diverso, deliberatamente non coperto da questa dimensione - vedi il
docstring di core/policy_engine.py per il limite dichiarato).

F1.2.3 (intersezione, capability per SESSIONE) - decisione esplicita dell'utente su cosa
"sessione" dovesse significare: un'istanza di CONNESSIONE di un dispositivo companion, distinta
dalla sua identita' PERSISTENTE (`device_id`, sopra). Un dispositivo che si disconnette/
riconnette (l'app va in background e poi torna, una perdita di rete, un riavvio) genera un nuovo
session_id a ogni `DeviceRegistry.claim()` (core/device_registry.py), anche per lo stesso
device_id di prima - un permesso scoped alla sessione vale solo finche' QUELLA connessione resta
viva, non per il dispositivo per sempre. Stesso meccanismo/stesse garanzie di isolamento per
thread di current_device_id sopra."""
import contextvars

_current_device_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_device_id", default=None)
_current_agent_name: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_agent_name", default=None)
_current_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_session_id", default=None)


def current_device_id() -> str | None:
    """Il device_id del dispositivo companion che ha originato la richiesta in corso su QUESTO
    thread, o None se non impostato (comando vocale locale, automazione in background, o una
    richiesta companion senza device_id nel body - vedi
    core/companion_server.py::_Handler._handle_command)."""
    return _current_device_id.get()


def set_current_device_id(device_id: str | None) -> contextvars.Token:
    """Imposta il device_id per il resto dell'esecuzione su QUESTO thread. Restituisce un Token
    da passare a reset_current_device_id() per ripristinare il valore precedente al termine della
    richiesta."""
    return _current_device_id.set(device_id)


def reset_current_device_id(token: contextvars.Token) -> None:
    _current_device_id.reset(token)


def current_agent_name() -> str | None:
    """Il `TaskAgent.agent_name` che sta eseguendo un intent su QUESTO thread in questo momento
    (solo durante la chiamata all'executor dentro TaskAgent.run(), vedi core/agent.py), o None se
    nessun agente a passi e' in corso (comando diretto, o un'automazione via PlanExecutor)."""
    return _current_agent_name.get()


def set_current_agent_name(agent_name: str | None) -> contextvars.Token:
    """Imposta il nome dell'agente per il resto dell'esecuzione su QUESTO thread. Restituisce un
    Token da passare a reset_current_agent_name() per ripristinare il valore precedente."""
    return _current_agent_name.set(agent_name)


def reset_current_agent_name(token: contextvars.Token) -> None:
    _current_agent_name.reset(token)


def current_session_id() -> str | None:
    """L'id della sessione di CONNESSIONE companion (non il device_id persistente, vedi il
    docstring del modulo) che ha originato la richiesta in corso su QUESTO thread, o None se non
    impostato (comando vocale locale, automazione in background, o una richiesta companion senza
    session_id nel body)."""
    return _current_session_id.get()


def set_current_session_id(session_id: str | None) -> contextvars.Token:
    """Imposta il session_id per il resto dell'esecuzione su QUESTO thread. Restituisce un Token
    da passare a reset_current_session_id() per ripristinare il valore precedente."""
    return _current_session_id.set(session_id)


def reset_current_session_id(token: contextvars.Token) -> None:
    _current_session_id.reset(token)


# F1.5.2 (propagare il taint oltre l'osservazione dello STESSO turno, vedi core/agent.py::
# TaskAgent._observe() per il marcatore stesso): un comando diretto (JakeCore._execute_command,
# percorso a comando singolo - non l'agente, che gia' marca le proprie osservazioni da solo)
# che restituisce contenuto esterno (core.taint.EXTERNAL_CONTENT_INTENTS) diventa PAROLA PER
# PAROLA la risposta mostrata all'utente E la voce salvata in conversation_state (cronologia a
# breve termine): senza questo, un turno agente FUTURO che include quella cronologia (TaskAgent.
# run(), ultimi turni via `history`) la vedrebbe come un messaggio "assistant" pienamente
# fidato - un'autorita' MAGGIORE di una semplice osservazione di strumento nello stesso turno,
# non minore. Stesso meccanismo/stesse garanzie di isolamento per thread di current_device_id/
# current_agent_name sopra: impostato da _execute_command() SOLO quando il comando ha davvero
# restituito contenuto esterno, letto una volta da JakeCore.answer() subito prima di salvare la
# risposta in cronologia (mai per cio' che l'utente vede/sente - vedi il commento in jake_core.py).
_current_command_source_intent: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_command_source_intent", default=None,
)


def current_command_source_intent() -> str | None:
    return _current_command_source_intent.get()


def set_current_command_source_intent(intent: str | None) -> contextvars.Token:
    return _current_command_source_intent.set(intent)


def reset_current_command_source_intent(token: contextvars.Token) -> None:
    _current_command_source_intent.reset(token)


# F1.3.4 (adozione - quinta fetta, ActionSnapshot per TaskAgent - vedi core/action_snapshot.py e
# core/skill_registry.py::SkillRegistry.execute()): stesso identico meccanismo/stesse garanzie di
# isolamento per thread di current_agent_name sopra, usato per lo STESSO identico problema -
# TaskAgent.run() non puo' passare un action_id per-passo attraverso self.executor(intent,
# parameters), un callable a firma FISSA a 2 argomenti condiviso da execute_action_with_retry()
# (core/execution_safety.py) e da ~15 executor finti nei test (tests/test_agent.py e altri):
# cambiare quella firma per portare un terzo parametro avrebbe richiesto aggiornare ognuno di
# quei finti, per un beneficio che riguarda solo il singolo intent DELETE_PATH. Un ContextVar
# impostato SOLO intorno alla chiamata a execute_action_with_retry() (come current_agent_name)
# risolve lo stesso problema senza toccare quella firma condivisa: i due executor reali di
# JakeCore (core/jake_core.py, che chiamano _resolve_and_execute) lo leggono per etichettare un
# eventuale snapshot; un executor finto che non lo legge semplicemente lo ignora, esattamente
# come gia' fa con current_agent_name. None (il default) significa "nessun action_id noto per
# questo passo" - il caso normale finche' TaskAgent.run() non lo imposta.
_current_action_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_action_id", default=None)


def current_action_id() -> str | None:
    """L'action_id del passo dell'agente in esecuzione su QUESTO thread in questo momento (solo
    durante la chiamata all'executor dentro TaskAgent.run(), vedi core/agent.py), o None se
    nessun passo e' in corso (comando diretto, o un'automazione via PlanExecutor - entrambi
    passano gia' il proprio action_id direttamente come parametro, non tramite questo contextvar)."""
    return _current_action_id.get()


def set_current_action_id(action_id: str | None) -> contextvars.Token:
    """Imposta l'action_id per il resto dell'esecuzione su QUESTO thread. Restituisce un Token da
    passare a reset_current_action_id() per ripristinare il valore precedente."""
    return _current_action_id.set(action_id)


def reset_current_action_id(token: contextvars.Token) -> None:
    _current_action_id.reset(token)
