"""Freni della proattivita' comuni a promemoria, automazioni e avvisi (F6.1/F6.3), in `JakeCore.notify`.

Prima di questo modulo le tre fonti proattive passavano solo dalla matrice modalita' x tipo di
`NotificationCenter`: nessun limite ai duplicati, nessun budget, quiet hours ignorate (le conosceva solo
il task monitor, via `NotificationPolicy`) e nessuna attenzione a una conversazione in corso.

Regole (ciascuna con un test in tests/test_proactive_gate.py):
- DUPLICATO: lo stesso messaggio dello stesso tipo gia' consegnato negli ultimi `dedup_window_s` secondi
  si scarta (vale per tutti i tipi: un promemoria doppio e' comunque un errore da non ripetere a voce);
- i PROMEMORIA passano altrimenti sempre: l'orario l'ha chiesto l'utente (stesso principio di
  NotificationCenter, che li ammette in ogni modalita' tranne riunione);
- avvisi e automazioni: in quiet hours, durante una conversazione in corso, o oltre `hourly_budget`
  consegne nell'ultima ora vanno IN CODA (mai persi, mai inventati);
- un evento dichiarato critico dal produttore (mai dedotto dal testo) salta budget, quiet hours e
  conversazione, non il controllo dei duplicati.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

DELIVER = "deliver"
DUPLICATE = "duplicate"
DEFER = "defer"


class ProactiveGate:
    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        dedup_window_s: float = 600.0,
        hourly_budget: int = 3,
        in_quiet_hours: Callable[[], bool] = lambda: False,
        conversation_active: Callable[[], bool] = lambda: False,
    ) -> None:
        self._clock = clock
        self.dedup_window_s = dedup_window_s
        self.hourly_budget = hourly_budget
        self.in_quiet_hours = in_quiet_hours
        self.conversation_active = conversation_active
        self._recent: dict[tuple[str, str], float] = {}
        self._deliveries: deque[float] = deque()

    def check(self, kind: str, message: str, critical: bool = False) -> tuple[str, str]:
        """(esito, ragione): esito e' DELIVER, DUPLICATE (da scartare) o DEFER (da mettere in coda)."""
        now = self._clock()
        key = (kind, " ".join(message.lower().split()))
        last = self._recent.get(key)
        if last is not None and now - last < self.dedup_window_s:
            return DUPLICATE, f"gia' notificato {now - last:.0f} s fa"
        if kind != "reminder" and not critical:
            if self.in_quiet_hours():
                return DEFER, "quiet hours"
            if self.conversation_active():
                return DEFER, "conversazione in corso"
            while self._deliveries and now - self._deliveries[0] >= 3600:
                self._deliveries.popleft()
            if len(self._deliveries) >= self.hourly_budget:
                return DEFER, f"budget di {self.hourly_budget} notifiche proattive all'ora esaurito"
            self._deliveries.append(now)
        self._recent[key] = now
        if len(self._recent) > 500:  # memoria limitata: si dimenticano le voci piu' vecchie
            for old in sorted(self._recent, key=self._recent.get)[:250]:
                del self._recent[old]
        return DELIVER, "consegnata"
