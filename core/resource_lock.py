"""Coda/lock per resource key (F1.8.1, fase 7/10 del piano multi-device - decisione di prodotto
esplicita dell'utente, vedi ROADMAP_EXECUTION.md sezione F1.4).

"Le azioni che toccano la stessa risorsa non devono essere eseguite contemporaneamente. Azioni
read-only compatibili possono essere parallele. Azioni mutative sulla stessa risorsa devono
essere serializzate." - non un limite teorico: `core/reminder_manager.py`/`core/todo_manager.py`/
`core/device_registry.py` hanno gia' richiesto un lock interno per lo stesso motivo (F1.8.2/
F1.8.7), ma quei lock proteggono solo LO STORE di quella singola classe, non un'azione qualsiasi
su una risorsa arbitraria (es. due dispositivi che tentano di rinominare o spostare LO STESSO
file nello stesso istante - un file qualsiasi sul filesystem non e' "posseduto" da nessuno store
di Jake). Questo modulo offre un lock ESPLICITO per resource key, indipendente da quale skill lo
richieda - "prima una coda/resource-locking semplice e testabile, non uno scheduler distribuito
complesso" (decisione di prodotto esplicita dell'utente): un vero readers-writer lock per
resource key, non un sistema di priorita'/scheduling.

Deliberatamente NON affrontato qui (passo successivo dichiarato): quale `resource_key` derivare
da un dato intent/parametri (gli esempi della specifica - `filesystem:<path>`, `app:<name>`,
`window:<id>`, `browser:<profile/tab>`, `device:<id>`, `system:power`, `audio:output` - coprono
209 intent con forme di parametri diverse, un censimento a se', dello stesso ordine di grandezza
di `core/action_contracts.py::INTENT_EFFECT_CLASS`) ne' il collegamento ai quattro chokepoint
reali (`JakeCore`/`TaskAgent`/`PlanExecutor`) - stesso principio "prima il contratto/il
meccanismo, poi l'adozione" gia' seguito per `ActionProposal`/`DeviceIdentity` in questa
sessione."""
import contextlib
import threading
from collections.abc import Iterator


class ResourceLockManager:
    """Un lock per OGNI resource key mai richiesta, creato pigramente e mai ripulito - il numero
    di resource key distinte usate da un utente personale nel tempo resta piccolo (percorsi,
    nomi di app, finestre...), un dizionario che cresce nel tempo non e' un problema pratico qui
    (diverso da un servizio multi-tenant con milioni di chiavi). `threading.Lock`, non `RLock`:
    un lettore/scrittore che tentasse di rientrare sulla STESSA resource key dallo stesso thread
    (es. un'azione che ne richiama un'altra sulla stessa risorsa) si bloccherebbe da solo - non
    supportato da questa prima versione "semplice", vedi il docstring del modulo.

    Nota sulla scelta dell'algoritmo (readers-writer classico, "il primo lettore blocca, l'ultimo
    sblocca"): un limite noto e accettato, non nascosto - uno scrittore in attesa puo' restare
    indefinitamente in coda se i lettori si susseguono senza mai lasciare la risorsa
    completamente libera (starvation dello scrittore). Per un assistente personale con un numero
    di dispositivi/richieste concorrenti ridotto questo non e' il rischio che questa fase deve
    coprire (il rischio reale sono DUE azioni mutative concorrenti sulla stessa risorsa, non
    l'equita' di scheduling tra tante) - una coda equa (es. FIFO tra lettori e scrittori) resta
    lavoro futuro dichiarato se mai servisse."""

    def __init__(self):
        self._registry_lock = threading.Lock()  # protegge SOLO la creazione pigra sotto
        self._writer_locks: dict[str, threading.Lock] = {}
        self._reader_counts: dict[str, int] = {}
        self._reader_count_lock = threading.Lock()

    def _writer_lock_for(self, resource_key: str) -> threading.Lock:
        with self._registry_lock:
            lock = self._writer_locks.get(resource_key)
            if lock is None:
                lock = threading.Lock()
                self._writer_locks[resource_key] = lock
            return lock

    @contextlib.contextmanager
    def acquire_write(self, resource_key: str) -> Iterator[None]:
        """Esclusivo: aspetta sia altri scrittori sia ogni lettore gia' in corso sulla STESSA
        resource_key (non su altre: due resource key diverse non si bloccano mai a vicenda), poi
        blocca sia nuovi scrittori sia nuovi lettori finche' non esce dal blocco `with`."""
        lock = self._writer_lock_for(resource_key)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    @contextlib.contextmanager
    def acquire_read(self, resource_key: str) -> Iterator[None]:
        """Condiviso: piu' lettori sulla STESSA resource key procedono insieme (nessuna attesa
        tra loro), ma il PRIMO lettore aspetta che uno scrittore in corso finisca, e un nuovo
        scrittore aspetta che TUTTI i lettori correnti abbiano finito (non solo il primo ad
        arrivare) - stesso principio, applicato ai lettori invece che agli scrittori."""
        writer_lock = self._writer_lock_for(resource_key)
        with self._reader_count_lock:
            count = self._reader_counts.get(resource_key, 0)
            if count == 0:
                writer_lock.acquire()  # il primo lettore blocca nuovi scrittori finche' non finisce
            self._reader_counts[resource_key] = count + 1
        try:
            yield
        finally:
            with self._reader_count_lock:
                self._reader_counts[resource_key] -= 1
                if self._reader_counts[resource_key] == 0:
                    writer_lock.release()  # l'ultimo lettore libera la risorsa per gli scrittori
