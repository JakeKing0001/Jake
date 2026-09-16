"""Bus di eventi multi-consumatore (v4.9.1, HUD Engine 2.0), in aggiunta a SessionHooks (core/
session_hooks.py), non al suo posto: SessionHooks resta un aggancio A SINGOLO consumatore
(chiunque chiami _attach_hooks() per ultimo sovrascrive l'assegnazione precedente, per costruzione:
serve a UNA sessione attiva - voce o HUD PySide6 - di rimpiazzare i callback di default), quindi
non e' adatto a un HUD nativo separato o un'app companion che vogliono ASCOLTARE senza
contendersi l'unico slot. EventBus permette invece un numero qualsiasi di iscritti (usato da
core/companion_server.py) che vedono tutti gli stessi eventi, senza toccare ne' rompere il
percorso SessionHooks gia' esistente."""
import collections
import queue
import threading


class EventBus:
    def __init__(self, max_queue_size: int = 200, replay_buffer_size: int = 200):
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.max_queue_size = max_queue_size
        # F4.1.1: numero d'ordine GLOBALE, gapless tra tutti i produttori (JakeCore,
        # companion_server...) - assegnato QUI, non dal chiamante di publish(), perche' solo il
        # bus vede l'ordine reale di interlacciamento tra thread produttori diversi.
        self._next_sequence_id = 1
        # F4.1.3 ("resume dall'ultimo sequence id"): gli ultimi N eventi pubblicati, per poter
        # ricostruire cosa un client ha perso durante una disconnessione breve - vedi
        # subscribe_with_replay() sotto. Dimensione LIMITATA per costruzione (un assistente
        # personale, non un log illimitato): una disconnessione piu' lunga del buffer produce un
        # gap dichiarato onestamente (vedi "gap" sotto), mai un replay finto completo.
        self._replay_buffer: collections.deque = collections.deque(maxlen=replay_buffer_size)

    def publish(self, event) -> None:
        with self._lock:
            if hasattr(event, "sequence_id"):
                event.sequence_id = self._next_sequence_id
                self._next_sequence_id += 1
            self._replay_buffer.append(event)
            subscribers = list(self._subscribers)
        for subscriber_queue in subscribers:
            try:
                subscriber_queue.put_nowait(event)
            except queue.Full:
                # Un iscritto lento (client di rete disconnesso o bloccato) non deve mai far
                # aspettare o bloccare chi pubblica: perde semplicemente gli eventi piu' vecchi.
                try:
                    subscriber_queue.get_nowait()
                    subscriber_queue.put_nowait(event)
                except queue.Empty:
                    pass

    def subscribe(self) -> queue.Queue:
        subscriber_queue: queue.Queue = queue.Queue(maxsize=self.max_queue_size)
        with self._lock:
            self._subscribers.append(subscriber_queue)
        return subscriber_queue

    def subscribe_with_replay(self, since_sequence_id: int) -> tuple[queue.Queue, list, bool]:
        """Come subscribe(), ma calcola ANCHE gli eventi bufferizzati con sequence_id maggiore di
        since_sequence_id SOTTO LO STESSO LOCK dell'iscrizione - senza questo, un evento
        pubblicato esattamente tra "calcola il replay" e "iscriviti" (due operazioni separate)
        sparirebbe (mai visto ne' nel replay ne' dal vivo) o verrebbe duplicato (visto in
        entrambi), a seconda di quale delle due venisse fatta per prima. Ritorna
        (coda_iscritto, eventi_da_riprodurre, gap): `gap` e' True quando il buffer di replay
        (dimensione limitata) non copre l'intera finestra richiesta - uno o piu' eventi tra
        since_sequence_id e il piu' vecchio nel buffer sono persi per sempre, non recuperabili in
        alcun modo; il chiamante deve trattarlo onestamente (es. avvisare l'utente), mai far
        finta che il replay sia completo quando non lo e'. since_sequence_id<=0 (un client MAI
        connesso prima, non un reconnect) non produce mai un replay ne' un gap - non c'e' nulla
        da recuperare."""
        with self._lock:
            replayed: list = []
            gap = False
            if since_sequence_id > 0:
                buffered = list(self._replay_buffer)
                replayed = [event for event in buffered if getattr(event, "sequence_id", 0) > since_sequence_id]
                oldest_sequence_id = getattr(buffered[0], "sequence_id", None) if buffered else None
                gap = oldest_sequence_id is not None and oldest_sequence_id > since_sequence_id + 1
            subscriber_queue: queue.Queue = queue.Queue(maxsize=self.max_queue_size)
            self._subscribers.append(subscriber_queue)
        return subscriber_queue, replayed, gap

    def unsubscribe(self, subscriber_queue: queue.Queue) -> None:
        with self._lock:
            if subscriber_queue in self._subscribers:
                self._subscribers.remove(subscriber_queue)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)
