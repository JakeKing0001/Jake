"""Procedure di Computer Use dimostrate dall'utente (F3.8, "Learn by demonstration").

- `RECORD_COMPUTER_PROCEDURE`: "osserva come faccio" avvia la registrazione nella finestra indicata
  (o in primo piano); "salva la procedura come X" la ferma, generalizza i testi scritti in parametri,
  salva la procedura (versione, app e versione dell'app, approvazione) e la mostra all'utente in
  parole. Solo selettori semantici, solo nella finestra bersaglio, mai il contenuto di un campo
  password (core/computer_use/demonstration.py).
- `RUN_COMPUTER_PROCEDURE`: la riesegue. Una procedura SOSPESA per drift non viene eseguita finche'
  un dry-run completo non la riattiva; se rischio o capability sono aumentati rispetto
  all'approvazione serve una nuova conferma; un drift durante l'esecuzione la sospende invece di
  improvvisare; l'ultima esecuzione si puo' annullare (valori precedenti dei campi, passi di
  annullamento dichiarati), e cio' che non e' annullabile viene detto.

`policy_engine` viene iniettato da JakeCore dopo la costruzione (stesso schema di RUN_WORKFLOW) e
riassegnato a `ComputerAgent` a ogni esecuzione: la policy dei passi che dichiarano un rischio resta
decisa dentro `ComputerAgent.click_element`/`type_into_element`."""
from core.skill_result import SkillResult


class RunComputerProcedureSkill:
    metadata = {
        "intent": "RUN_COMPUTER_PROCEDURE",
        "description": "Esegue una procedura di azioni sullo schermo (click/scritture su un'app) "
        "precedentemente registrata e salvata con un nome, dato il suo nome.",
        "parameters": {
            "name": {"type": "string", "required": True, "description": "Nome della procedura salvata da eseguire."},
            "parameters": {
                "type": "object", "required": False,
                "description": "Valori per i segnaposto ${nome} dei passi di scrittura (altrimenti i predefiniti dimostrati).",
            },
            "dry_run": {
                "type": "boolean", "required": False,
                "description": "Se vero verifica solo che i passi risolverebbero, senza eseguire nulla "
                "('mostrami prima cosa farebbe', 'provala').",
            },
            "reactivate": {
                "type": "boolean", "required": False,
                "description": "Riattiva una procedura sospesa, solo se un dry-run completo riesce.",
            },
            "undo": {"type": "boolean", "required": False, "description": "Annulla l'ultima esecuzione della procedura."},
        },
    }

    def __init__(self, procedure_manager, computer_agent=None, policy_engine=None):
        from core.computer_agent import ComputerAgent

        self.procedure_manager = procedure_manager
        self.computer_agent = computer_agent or ComputerAgent()
        self.policy_engine = policy_engine
        self._last_runs: dict = {}

    def _adapter(self):
        from core.computer_use.ui_automation_adapter import UIAutomationAdapter

        return UIAutomationAdapter()

    def execute(self, parameters: dict = None):
        from core.computer_use.procedure import dry_run_steps
        from core.computer_use.procedure_lifecycle import (
            STATUS_SUSPENDED,
            approve,
            needs_reapproval,
            reactivate,
            run_procedure,
            suspend,
            undo_run,
        )

        parameters = parameters or {}
        name = (parameters.get("name") or "").strip()
        if not name:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        procedure = self.procedure_manager.load_procedure(name)
        if procedure is None:
            return SkillResult(success=False, data={"name": name}, error="NOT_FOUND")
        if not procedure.steps:
            return SkillResult(success=False, data={"name": name}, error="EMPTY_PROCEDURE")

        self.computer_agent.policy_engine = self.policy_engine
        adapter = self._adapter()

        if parameters.get("undo"):
            run = self._last_runs.get(name)
            if run is None:
                return SkillResult(success=False, data={"name": name}, error="NOTHING_TO_UNDO")
            results = undo_run(self.computer_agent, adapter, run)
            undone = sum(1 for r in results if r.success)
            self._last_runs.pop(name, None)
            return SkillResult(success=undone == len(run.undo_plan), data={
                "name": name, "undone_steps": undone, "undo_steps": len(run.undo_plan),
                "not_undoable": list(run.not_undoable),
            })

        values = {**procedure.defaults_dict(), **(parameters.get("parameters") or {})}
        if parameters.get("dry_run") or parameters.get("reactivate"):
            results = dry_run_steps(adapter, list(procedure.steps), parameters=values, agent=self.computer_agent)
            all_ok = all(result.would_succeed for result in results)
            data = {
                "name": name, "dry_run": True, "total_steps": len(procedure.steps), "version": procedure.version,
                "status": procedure.status,
                "steps": [{"would_succeed": r.would_succeed, "error": r.error} for r in results],
            }
            if parameters.get("reactivate") and procedure.status == STATUS_SUSPENDED and all_ok:
                self.procedure_manager.save_procedure(reactivate(procedure))
                data["status"] = "active"
            return SkillResult(success=all_ok, data=data)

        if procedure.status == STATUS_SUSPENDED:
            return SkillResult(success=False, data={
                "name": name, "reason": procedure.suspended_reason,
                "message": "La procedura e' sospesa perche' l'app e' cambiata: provala con un dry-run per riattivarla.",
            }, error="PROCEDURE_SUSPENDED")

        reasons = needs_reapproval(procedure)
        if reasons:
            if not parameters.get("confirmed"):
                return SkillResult(success=False, data={
                    "message": f"La procedura «{name}» ora richiede piu' di quanto avevi approvato ({'; '.join(reasons)}). "
                    "La riapprovi?",
                    "confirm_parameters": {**parameters, "confirmed": True},
                }, error="CONFIRMATION_REQUIRED")
            procedure = approve(procedure)
            self.procedure_manager.save_procedure(procedure)

        run = run_procedure(self.computer_agent, adapter, procedure, parameters=values)
        if run.drift:
            self.procedure_manager.save_procedure(suspend(procedure, run.drift))
        if run.completed:
            self._last_runs[name] = run
        last = run.results[-1] if run.results else None
        return SkillResult(success=not run.drift and run.completed == len(procedure.steps), data={
            "name": name, "version": procedure.version, "completed_steps": run.completed,
            "total_steps": len(procedure.steps),
            "last_error": last.error if last is not None and not last.success else None,
            "likely_drift": run.drift is not None, "suspended": run.drift is not None, "drift": run.drift,
            "undo_steps": len(run.undo_plan), "not_undoable": list(run.not_undoable),
        })


class RecordComputerProcedureSkill:
    metadata = {
        "intent": "RECORD_COMPUTER_PROCEDURE",
        "description": "Impara una procedura guardando l'utente: 'start' inizia a osservare i click e le "
        "scritture in una finestra ('osserva come faccio'), 'stop' smette e la salva con un nome "
        "('salva la procedura come ...'). Registra solo elementi semantici, mai lo schermo.",
        "parameters": {
            "action": {"type": "string", "required": True, "description": "'start' oppure 'stop'."},
            "window": {
                "type": "string", "required": False,
                "description": "Parte del titolo della finestra da osservare (con 'start'; predefinita: quella in primo piano).",
            },
            "name": {"type": "string", "required": False, "description": "Nome con cui salvare la procedura (con 'stop')."},
        },
    }

    def __init__(self, procedure_manager, sampler_factory=None, foreground_title=None):
        self.procedure_manager = procedure_manager
        self._sampler_factory = sampler_factory
        self._foreground_title = foreground_title
        self._sampler = None
        self._window = None

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        action = (parameters.get("action") or "").strip().lower()
        if action == "start":
            return self._start((parameters.get("window") or "").strip())
        if action == "stop":
            return self._stop((parameters.get("name") or "").strip())
        return SkillResult(success=False, data={"action": action}, error="MISSING_PARAMETERS")

    def _start(self, window: str) -> SkillResult:
        if self._sampler is not None:
            return SkillResult(success=False, data={"window": self._window}, error="ALREADY_RECORDING")
        process_id = None
        if not window:
            window, process_id = (self._foreground_title or _foreground_window)()
        if not window:
            return SkillResult(success=False, data={}, error="WINDOW_NOT_FOUND")
        from core.computer_use.demonstration import UiaDemonstrationSampler

        if self._sampler_factory is not None:
            sampler = self._sampler_factory(window)
        else:
            sampler = UiaDemonstrationSampler(window, process_id=process_id)
        if not sampler.start():
            return SkillResult(success=False, data={"window": window, "reason": sampler.error}, error="WINDOW_NOT_FOUND")
        self._sampler, self._window = sampler, window
        return SkillResult(success=True, data={"recording": True, "window": window})

    def _stop(self, name: str) -> SkillResult:
        if self._sampler is None:
            return SkillResult(success=False, data={}, error="NOT_RECORDING")
        if not name:
            return SkillResult(success=False, data={"window": self._window}, error="MISSING_PARAMETERS")
        from core.computer_use.demonstration import describe_steps, generalize
        from core.computer_use.procedure_lifecycle import process_name_of, process_version_of, window_structure
        from core.computer_use.ui_automation_adapter import UIAutomationAdapter

        sampler, window = self._sampler, self._window
        self._sampler = self._window = None
        steps = sampler.stop()
        if not steps:
            return SkillResult(success=False, data={"window": window}, error="NOTHING_RECORDED")
        steps, defaults = generalize(steps)
        pid = getattr(sampler, "_pid", None)
        procedure = self.procedure_manager.create(
            name, steps, defaults=defaults, app_process=process_name_of(pid), app_version=process_version_of(pid),
            structure=window_structure(UIAutomationAdapter(), window),
        )
        return SkillResult(success=True, data={
            "name": name, "version": procedure.version, "steps": describe_steps(list(procedure.steps), defaults),
            "parameters": sorted(defaults), "app": procedure.app_process, "app_version": procedure.app_version,
        })


def _foreground_window() -> tuple[str, int | None]:
    """Titolo e processo della finestra in primo piano ("osserva come faccio" senza nominarla)."""
    try:
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        return win32gui.GetWindowText(hwnd) or "", win32process.GetWindowThreadProcessId(hwnd)[1]
    except Exception:
        return "", None
