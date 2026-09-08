"""Kill switch globale (F1, Trustworthy Agent Core 3.0): un solo interruttore, condiviso da
tutti gli agenti e le automazioni, per fermare tutto SUBITO invece di aspettare che un compito
lungo finisca da solo. Pensato per essere azionato da più punti diversi (voce/testo - vedi
skills/kill_switch.py -, hotkey globale e tray - vedi core/gui/hud/app.py -), tutti sullo stesso
oggetto condiviso, cosi' non importa da dove parte: ferma comunque tutto.

Fermare un agente A META' PASSO (mentre aspetta la risposta del modello, o mentre una skill sta
scrivendo su disco) non è sicuro: si controlla il flag SOLO tra un passo e il successivo (vedi
TaskAgent.run/PlanExecutor.execute), lo stesso punto in cui gia' si controllano timeout e passi
ripetuti. Non è quindi un kill istantaneo a livello di sistema operativo (non uccide un processo
a metà scrittura), ma impedisce che un compito composto ne faccia un altro dopo quello in corso -
"arresto immediato" nel senso di "nessun passo ulteriore", non di "thread abort violento", che
lascerebbe stato a metà senza nessuna delle garanzie di execution_safety.py (retry/verifica/
rollback)."""
import threading


class KillSwitch:
    def __init__(self):
        self._event = threading.Event()

    def activate(self) -> None:
        self._event.set()

    def reset(self) -> None:
        self._event.clear()

    def is_active(self) -> bool:
        return self._event.is_set()
