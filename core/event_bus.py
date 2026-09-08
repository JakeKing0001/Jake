"""Bus di eventi multi-consumatore (v4.9.1, HUD Engine 2.0), in aggiunta a SessionHooks (core/
session_hooks.py), non al suo posto: SessionHooks resta un aggancio A SINGOLO consumatore
(chiunque chiami _attach_hooks() per ultimo sovrascrive l'assegnazione precedente, per costruzione:
serve a UNA sessione attiva - voce o HUD PySide6 - di rimpiazzare i callback di default), quindi
non e' adatto a un HUD nativo separato o un'app companion che vogliono ASCOLTARE senza
contendersi l'unico slot. EventBus permette invece un numero qualsiasi di iscritti (usato da
core/companion_server.py) che vedono tutti gli stessi eventi, senza toccare ne' rompere il
percorso SessionHooks gia' esistente."""
import queue
import threading


class EventBus:
    def __init__(self, max_queue_size: int = 200):
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.max_queue_size = max_queue_size

    def publish(self, event) -> None:
        with self._lock:
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

    def unsubscribe(self, subscriber_queue: queue.Queue) -> None:
        with self._lock:
            if subscriber_queue in self._subscribers:
                self._subscribers.remove(subscriber_queue)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)
