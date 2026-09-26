"""Executor per la sintesi/conversione vocale che non trattiene l'uscita del processo.

`ThreadPoolExecutor` usa thread NON daemon e l'interprete li aspetta all'uscita: una conversione RVC
(timeout HTTP 30 s) o una sintesi Edge (6 s) gia' abbandonata da `stop()` teneva Jake in vita fino
alla sua fine (misurato: 8,3 s di attesa all'uscita con una conversione di 8 s). Qui ogni compito
gira su un thread daemon: all'uscita del processo il lavoro abbandonato viene lasciato li', come
per `core.turn_cancellation.cancellable_call`; non si uccide nulla, il risultato era gia' scartato.

Stessa interfaccia usata dai provider (`submit` -> `concurrent.futures.Future`, `shutdown`) e stesso
limite di concorrenza di `ThreadPoolExecutor(max_workers=n)`: al massimo `max_workers` compiti
insieme, gli altri aspettano uno slot (e possono essere annullati finche' aspettano).
"""
from __future__ import annotations

import itertools
import threading
from concurrent.futures import Future


class DaemonExecutor:
    def __init__(self, max_workers: int = 2, thread_name_prefix: str = "jake-voice-worker") -> None:
        if max_workers < 1:
            raise ValueError("max_workers deve essere almeno 1")
        self._slots = threading.BoundedSemaphore(max_workers)
        self._prefix = thread_name_prefix
        self._counter = itertools.count()
        self._lock = threading.Lock()
        self._shutdown = False
        self._threads: set[threading.Thread] = set()
        self._waiting: set[Future] = set()

    def submit(self, fn, /, *args, **kwargs) -> Future:
        future: Future = Future()
        with self._lock:
            if self._shutdown:
                raise RuntimeError("cannot schedule new futures after shutdown")
            self._waiting.add(future)
            thread = threading.Thread(target=self._run, args=(future, fn, args, kwargs),
                                      name=f"{self._prefix}_{next(self._counter)}", daemon=True)
            self._threads.add(thread)
        thread.start()
        return future

    def _run(self, future: Future, fn, args, kwargs) -> None:
        try:
            with self._slots:
                with self._lock:
                    self._waiting.discard(future)
                if not future.set_running_or_notify_cancel():
                    return
                try:
                    result = fn(*args, **kwargs)
                except BaseException as exc:  # consegnato a chi legge il future, mai perso nel thread
                    future.set_exception(exc)
                else:
                    future.set_result(result)
        finally:
            with self._lock:
                self._threads.discard(threading.current_thread())

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._lock:
            self._shutdown = True
            waiting = list(self._waiting) if cancel_futures else []
            threads = list(self._threads)
        for future in waiting:
            future.cancel()
        if wait:
            for thread in threads:
                thread.join()
