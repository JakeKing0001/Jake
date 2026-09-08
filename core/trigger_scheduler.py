import threading
from datetime import date, datetime

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
        blocked_intents: set = None,
        always_confirm_intents: set = None,
        interval_seconds: float = 30,
    ):
        self.trigger_manager = trigger_manager
        self.workflow_manager = workflow_manager
        self.plan_executor = plan_executor
        self.desktop_context = desktop_context
        self.on_trigger = on_trigger
        self.blocked_intents = blocked_intents
        self.always_confirm_intents = always_confirm_intents
        self.interval_seconds = interval_seconds
        self._thread = None
        self._stop_event = threading.Event()
        self._logger = get_logger()
        # Stato del fronte di salita per i trigger "app_focus": memorizza se, all'ultimo
        # controllo, la finestra attiva corrispondeva gia' o no (per non risparare a ogni poll).
        self._app_focus_matched = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

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
        name = trigger.get("name")
        current = (self.desktop_context.get_current_window() or "").lower()
        matches_now = needle in current
        matched_before = self._app_focus_matched.get(name, False)
        self._app_focus_matched[name] = matches_now
        return matches_now and not matched_before

    def _fire(self, trigger: dict) -> None:
        name = trigger.get("name")
        workflow_name = trigger.get("workflow_name")
        plan = self.workflow_manager.load(workflow_name)
        if plan is None:
            self._logger.warning(
                "Trigger %s punta all'automazione '%s' che non esiste piu': salto", name, workflow_name
            )
            return

        # model=None: un'automazione esegue un piano gia' costruito, senza mai chiamare il
        # modello per decidere il passo successivo (a differenza dell'agente a passi) - non c'e'
        # nessun modello da riportare nel log strutturato. private=False: un trigger che parte
        # da solo non e' mai legato a una conversazione in modalita' privata.
        outcome = self.plan_executor.execute(
            plan, blocked_intents=self.blocked_intents, always_confirm_intents=self.always_confirm_intents,
            trace_id=new_trace_id(), private=False, model=None,
        )
        self.trigger_manager.mark_fired(name, datetime.now().isoformat())

        if self.on_trigger is not None:
            self.on_trigger(trigger, outcome, len(plan.steps))
