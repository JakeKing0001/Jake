"""Kill switch globale (F1, Trustworthy Agent Core 3.0): un solo interruttore, condiviso da
tutti gli agenti e le automazioni, per fermare tutto SUBITO invece di aspettare che un compito
lungo finisca da solo. Pensato per essere azionato da più punti diversi (voce/testo - vedi
skills/kill_switch.py -, hotkey globale e tray - vedi core/gui/hud/app.py -), tutti sullo stesso
oggetto condiviso, cosi' non importa da dove parte: ferma comunque tutto.

Fermare un agente A META' PASSO (mentre una skill sta scrivendo su disco, o mentre gira un
sotto-processo/una chiamata di rete che Jake stesso ha avviato) non è sicuro: il flag resta
controllato SOLO tra un passo e il successivo (vedi TaskAgent.run/PlanExecutor.execute), lo
stesso punto in cui gia' si controllano timeout e passi ripetuti - "arresto immediato" nel senso
di "nessun passo ulteriore", non di "thread abort violento", che lascerebbe stato a metà senza
nessuna delle garanzie di execution_safety.py (retry/verifica/rollback).

F1.8.3 (propagazione ad altre superfici oltre "tra un passo e il successivo"): due eccezioni
DELIBERATE a questo principio, entrambe NON un abort violento - in entrambe Jake si limita a
smettere di ASPETTARE un'operazione che ha gia' avviato, invece di interromperla a forza, e lo fa
solo per operazioni delle quali possiede gia' un riferimento e un esito "abbandonato" ben
definito (nessuno stato mai toccato a metà): (1) `skills/run_command.py::RunCommandSkill` sonda
il proprio subprocess ogni 0.2s e lo termina (`Popen.kill()`) se il kill switch scatta durante
l'attesa - qui la skill possiede il processo, terminarlo è sicuro; (2)
`core/agent.py::TaskAgent._chat_or_abandon` sonda ogni 0.2s l'attesa della risposta del modello e,
se il kill switch scatta, smette di aspettarla (la chiamata HTTP abbandonata continua in
background fino al proprio timeout, ma il suo risultato non verrà mai usato - Jake non la
interrompe a forza, semplicemente non resta più in ascolto)."""
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
