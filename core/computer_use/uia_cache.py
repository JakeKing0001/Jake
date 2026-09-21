"""Cache dell'albero UI Automation con invalidazione a eventi e a tempo (F3.2.2, F3.2.4).

`UIAutomationAdapter.describe_tree` legge ogni proprieta' con una chiamata COM dal vivo (~1 ms per
elemento su un'app reale). Rileggere lo stesso albero a ogni passo di un'azione e' quindi costoso e,
peggio, inutile finche' la finestra non cambia. `TreeCache` conserva l'ultima descrizione per finestra
(chiave: HWND + profondita') e la restituisce finche' e' valida.

Una voce smette di essere valida per DUE motivi indipendenti, perche' nessuno dei due basta da solo:
1. **evento**: `UIAEventListener` segnala che qualcosa nella finestra e' cambiato (focus, struttura,
   proprieta', finestre/menu aperti, Invoke, selezione) e la "generazione" della finestra avanza;
2. **tempo** (`ttl_s`): dopo il TTL la voce scade comunque. Serve perche' gli eventi dipendono dal
   provider dell'app: misurato sulla fixture Qt, arrivano solo quelli di focus, non struttura ne'
   proprieta' (vedi `uia_events.py`). Senza il TTL, una cache guidata dai soli eventi resterebbe
   silenziosamente obsoleta su una app che non li solleva.

Le proprieta' lette restano quelle di `ElementInfo` (nome, ruolo, id, bounds, stato): la cache salva
l'oggetto immutabile gia' costruito, non tiene riferimenti COM vivi (che non attraversano gli apartment
e possono diventare invalidi quando l'app li distrugge)."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from core.computer_use.ui_automation_adapter import ElementInfo


@dataclass
class _Entry:
    tree: ElementInfo | None
    at: float
    generation: int


class TreeCache:
    def __init__(self, adapter, ttl_s: float = 3.0, clock: Callable[[], float] = time.monotonic, listener=None) -> None:
        self._adapter = adapter
        self._ttl = ttl_s
        self._clock = clock
        self._listener = listener
        self._lock = threading.Lock()
        self._entries: dict[tuple[int, int], _Entry] = {}
        self._generation: dict[int, int] = {}
        self.hits = 0
        self.misses = 0
        self.expirations = 0
        self.event_invalidations = 0
        self.events_by_kind: dict[str, int] = {}

    # ---- lettura --------------------------------------------------------------------------------------

    def get_tree(self, element, hwnd: int, max_depth: int = 8) -> ElementInfo | None:
        """L'albero di `element` (la finestra `hwnd`): dalla cache se ancora valido, altrimenti riletto."""
        key = (hwnd, max_depth)
        with self._lock:
            entry = self._entries.get(key)
            generation = self._generation.get(hwnd, 0)
            if entry is not None:
                if entry.generation != generation:
                    entry = None  # un evento ha segnalato un cambiamento dopo la lettura
                elif self._clock() - entry.at > self._ttl:
                    self.expirations += 1
                    entry = None
            if entry is not None:
                self.hits += 1
                return entry.tree
            self.misses += 1
        tree = self._adapter.describe_tree(element, max_depth)  # fuori dal lock: e' lenta
        with self._lock:
            self._entries[key] = _Entry(tree, self._clock(), generation)
        return tree

    # ---- invalidazione ---------------------------------------------------------------------------------

    def invalidate(self, hwnd: int | None = None) -> None:
        """Butta una finestra (o tutte). Da chiamare dopo un'azione che si sa cambia la UI."""
        with self._lock:
            if hwnd is None:
                self._entries.clear()
            else:
                for key in [k for k in self._entries if k[0] == hwnd]:
                    del self._entries[key]

    def watch(self, hwnd: int) -> bool:
        """Sottoscrive gli eventi UIA della finestra: da ora ogni evento avanza la sua generazione. False se
        il listener non c'e' o non e' disponibile: la cache funziona lo stesso, col solo TTL."""
        if self._listener is None:
            return False
        return bool(self._listener.subscribe(hwnd, lambda kind, detail: self._on_event(hwnd, kind)))

    def _on_event(self, hwnd: int, kind: str) -> None:
        with self._lock:
            self._generation[hwnd] = self._generation.get(hwnd, 0) + 1
            self.event_invalidations += 1
            self.events_by_kind[kind] = self.events_by_kind.get(kind, 0) + 1

    def generation(self, hwnd: int) -> int:
        with self._lock:
            return self._generation.get(hwnd, 0)

    def stats(self) -> dict:
        with self._lock:
            return {
                "hits": self.hits, "misses": self.misses, "expirations": self.expirations,
                "event_invalidations": self.event_invalidations, "events_by_kind": dict(self.events_by_kind),
                "entries": len(self._entries),
            }
