import threading
from datetime import date, datetime

from core.autonomy_budget import AutonomyBudget
from core.logger import get_logger, new_trace_id


class TriggerScheduler:
    """Controlla periodicamente i trigger salvati e fa partire da sola l'automazione collegata
    quando la condizione si avvera (v3.0: Jake proattivo), sullo stesso schema a doppio
    try/except di ReminderScheduler: un trigger o un'automazione rotta non deve mai fermare
    per sempre, in silenzio, il controllo di tutti gli altri.

    Due tipi di trigger supportati:
    - "time": {"at": "HH:MM"} - scatta una volta al giorno a quell'orario.
    - "app_focus": {"app_contains": "notepad"} - scatta quando la finestra attiva passa da
      NON contenere quel testo a contenerlo (un fronte di salita, non a ogni controllo mentre
      l'app resta in primo piano)."""

    def __init__(
        self,
        trigger_manager,
        workflow_manager,
        plan_executor,
        desktop_context,
        on_trigger=None,
        policy_engine=None,
        interval_seconds: float = 30,
        autonomy_budget: AutonomyBudget | None = None,
        stop_timeout_seconds: float = 2.0,
    ):
        self.trigger_manager = trigger_manager
        self.workflow_manager = workflow_manager
        self.plan_executor = plan_executor
        self.desktop_context = desktop_context
        self.on_trigger = on_trigger
        # F1 (core/policy_engine.py): UN riferimento condiviso con JakeCore, non piu' due
        # insiemi (blocked_intents/always_confirm_intents) da tenere sincronizzati a mano - vedi
        # il docstring di core/policy_engine.py sul perche' questa frammentazione e' esattamente
        # cio' che ha causato il bug di RunWorkflowSkill.
        self.policy_engine = policy_engine
        self.interval_seconds = interval_seconds
        # F6 (Proactive Intelligence & Autonomy, vedi core/autonomy_budget.py): un limite al
        # numero di automazioni che possono partire da sole in una finestra di tempo, condiviso
        # con JakeCore se passato (un solo budget per tutte le automazioni), altrimenti
        # un'istanza locale con i default - mai disattivato del tutto, a differenza di
        # blocked_intents/always_confirm_intents che possono restare None.
        self.autonomy_budget = autonomy_budget or AutonomyBudget()
        # F1.8.5: configurabile solo per i test - il comportamento di produzione resta invariato.
        self._stop_timeout_seconds = stop_timeout_seconds
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._logger = get_logger()
        # Stato del fronte di salita per i trigger "app_focus": memorizza se, all'ultimo
        # controllo, la finestra attiva corrispondeva gia' o no (per non risparare a ogni poll).
        self._app_focus_matched: dict[str, bool] = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        # F1.8.5 ("aggiungere deadlock timeout e diagnosi"): vedi core/scheduler.py::
        # ReminderScheduler.stop() per il buco reale riprodotto e il ragionamento completo -
        # stesso schema qui.
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._stop_timeout_seconds)
            if self._thread.is_alive():
                self._logger.warning(
                    "TriggerScheduler non si e' fermato entro il timeout: il thread precedente "
                    "e' ancora in esecuzione (probabilmente bloccato in un'automazione o un "
                    "controllo lento)."
                )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                for trigger in self.trigger_manager.list_all():
                    if self._is_due(trigger):
                        try:
                            self._fire(trigger)
                        except Exception:
                            self._logger.exception(
                                "Errore eseguendo il trigger %s", trigger.get("name")
                            )
            except Exception:
                self._logger.exception("Errore controllando i trigger")
            self._stop_event.wait(self.interval_seconds)

    def _is_due(self, trigger: dict) -> bool:
        trigger_type = trigger.get("type")
        spec = trigger.get("spec") or {}
        if trigger_type == "time":
            return self._time_is_due(trigger, spec)
        if trigger_type == "app_focus":
            return self._app_focus_is_due(trigger, spec)
        return False

    def _time_is_due(self, trigger: dict, spec: dict) -> bool:
        at_time = spec.get("at")
        if not at_time:
            return False
        now = datetime.now()
        if now.strftime("%H:%M") != at_time:
            return False
        last_fired = trigger.get("last_fired")
        if last_fired and last_fired.startswith(date.today().isoformat()):
            return False
        return True

    def _app_focus_is_due(self, trigger: dict, spec: dict) -> bool:
        needle = (spec.get("app_contains") or "").lower()
        if not needle:
            return False
        name = trigger.get("name") or ""
        current = (self.desktop_context.get_current_window() or "").lower()
        matches_now = needle in current
        matched_before = self._app_focus_matched.get(name, False)
        self._app_focus_matched[name] = matches_now
        return matches_now and not matched_before

    def _fire(self, trigger: dict) -> None:
        name = trigger.get("name")
        # F6 (Proactive Intelligence & Autonomy, core/autonomy_budget.py): controllato PRIMA di
        # caricare/eseguire il piano, non dopo - un trigger che scatta ripetutamente (una
        # condizione mal scritta, un bug) non deve continuare a far partire automazioni solo
        # perche' la precedente e' gia' finita. Niente mark_fired() qui: la prossima chiamata di
        # _run() (tra interval_seconds) ritentera' da sola quando il budget si sara' liberato,
        # invece di saltare questo trigger per il resto della giornata come se fosse gia' partito.
        if self.autonomy_budget.is_exceeded():
            self._logger.warning(
                "Trigger %s non fatto partire: budget di autonomia esaurito (%d azioni/%.0fs).",
                name, self.autonomy_budget.max_actions, self.autonomy_budget.window_seconds,
            )
            return

        workflow_name = trigger.get("workflow_name")
        plan = self.workflow_manager.load(workflow_name)
        if plan is None:
            self._logger.warning(
                "Trigger %s punta all'automazione '%s' che non esiste piu': salto", name, workflow_name
            )
            return

        self.autonomy_budget.record()
        # model=None: un'automazione esegue un piano gia' costruito, senza mai chiamare il
        # modello per decidere il passo successivo (a differenza dell'agente a passi) - non c'e'
        # nessun modello da riportare nel log strutturato. private=False: un trigger che parte
        # da solo non e' mai legato a una conversazione in modalita' privata.
        outcome = self.plan_executor.execute(
            plan, policy_engine=self.policy_engine,
            trace_id=new_trace_id(), private=False, model=None, requested_by=f"trigger:{name}",
        )
        self.trigger_manager.mark_fired(name, datetime.now().isoformat())

        if self.on_trigger is not None:
            self.on_trigger(trigger, outcome, len(plan.steps))
