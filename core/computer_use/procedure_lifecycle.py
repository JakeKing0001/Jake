"""F3.8.5-F3.8.7 — ciclo di vita di una procedura dimostrata.

`Procedure` e' cio' che viene salvato (core/procedure_manager.py): passi semantici, versione, app e
versione dell'app su cui e' stata dimostrata, parametri con i valori predefiniti, l'approvazione
dell'utente (livello di rischio e capability approvati) e lo stato (attiva/sospesa).

- F3.8.5 undo: `run_procedure` legge il valore di ogni campo PRIMA di riscriverlo e costruisce un
  piano di annullamento (valore precedente per i campi, il passo `undo` dichiarato per i click);
  un click senza annullamento noto e un campo password sono dichiarati non annullabili, mai finti;
- F3.8.6 drift: un fallimento con la forma di un cambiamento dell'app (finestra/elemento spariti,
  ambiguita', processo diverso da quello dimostrato) SOSPENDE la procedura invece di improvvisare;
  finche' e' sospesa non si esegue, e si riattiva solo dopo un dry-run completo riuscito;
- F3.8.7 approvazione: `assess` calcola rischio e capability richiesti ORA; se superano quelli
  approvati (un passo piu' rischioso, un'app diversa) serve una nuova approvazione esplicita."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from core.computer_use.procedure import (
    ACTION_CLICK,
    ACTION_TYPE,
    RecordedStep,
    is_likely_drift,
    replay_step,
)
from core.risk import RiskLevel, is_at_least, risk_of

SCHEMA_VERSION = 2
STATUS_ACTIVE = "active"
STATUS_SUSPENDED = "suspended"
# Un'azione su un'app qualsiasi senza rischio dichiarato: resta sul posto, annullabile a mano.
BASE_RISK = RiskLevel.LOCAL_REVERSIBLE


@dataclass(frozen=True)
class Procedure:
    name: str
    steps: tuple[RecordedStep, ...]
    version: int = 1
    app_process: str | None = None
    app_version: str | None = None
    defaults: tuple[tuple[str, str], ...] = ()
    approved_risk: str = BASE_RISK.value
    approved_capabilities: tuple[str, ...] = ()
    status: str = STATUS_ACTIVE
    suspended_reason: str | None = None

    def defaults_dict(self) -> dict[str, str]:
        return dict(self.defaults)

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION, "name": self.name, "version": self.version,
            "steps": [step.to_dict() for step in self.steps],
            "app": {"process": self.app_process, "version": self.app_version},
            "defaults": dict(self.defaults),
            "approval": {"risk": self.approved_risk, "capabilities": list(self.approved_capabilities)},
            "status": self.status, "suspended_reason": self.suspended_reason,
        }

    @classmethod
    def from_dict(cls, data, name: str | None = None) -> "Procedure":
        if isinstance(data, list):  # formato storico: solo la lista dei passi
            steps = tuple(RecordedStep.from_dict(step) for step in data)
            risk, capabilities = assess(steps, None)
            return cls(name=name or "", steps=steps, approved_risk=risk.value, approved_capabilities=capabilities)
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"schema procedura non supportato: {data.get('schema_version')!r}")
        app = data.get("app") or {}
        approval = data.get("approval") or {}
        return cls(
            name=data["name"], steps=tuple(RecordedStep.from_dict(step) for step in data["steps"]),
            version=int(data.get("version", 1)), app_process=app.get("process"), app_version=app.get("version"),
            defaults=tuple(sorted((data.get("defaults") or {}).items())),
            approved_risk=approval.get("risk", BASE_RISK.value),
            approved_capabilities=tuple(approval.get("capabilities") or ()),
            status=data.get("status", STATUS_ACTIVE), suspended_reason=data.get("suspended_reason"),
        )


def _all_steps(steps) -> list[RecordedStep]:
    out = []
    for step in steps:
        out.append(step)
        if step.undo is not None:
            out.append(step.undo)
    return out


def assess(steps, app_process: str | None) -> tuple[RiskLevel, tuple[str, ...]]:
    """Rischio massimo dichiarato e capability (app e intent di rischio) richiesti dai passi."""
    level = BASE_RISK
    capabilities = set()
    for step in _all_steps(steps):
        if step.risk_intent:
            capabilities.add(f"risk:{step.risk_intent}")
            if is_at_least(risk_of(step.risk_intent), level):
                level = risk_of(step.risk_intent)
    if app_process:
        capabilities.add(f"app:{app_process.lower()}")
    return level, tuple(sorted(capabilities))


def needs_reapproval(procedure: Procedure) -> list[str]:
    """Cosa e' cambiato rispetto all'approvazione (vuoto = nulla da riapprovare)."""
    level, capabilities = assess(procedure.steps, procedure.app_process)
    reasons = []
    approved = RiskLevel(procedure.approved_risk)
    if level != approved and is_at_least(level, approved):
        reasons.append(f"rischio salito da {approved.value} a {level.value}")
    new_caps = sorted(set(capabilities) - set(procedure.approved_capabilities))
    if new_caps:
        reasons.append("nuove capability: " + ", ".join(new_caps))
    return reasons


def approve(procedure: Procedure) -> Procedure:
    level, capabilities = assess(procedure.steps, procedure.app_process)
    return replace(procedure, approved_risk=level.value, approved_capabilities=capabilities,
                   version=procedure.version + 1)


def suspend(procedure: Procedure, reason: str) -> Procedure:
    return replace(procedure, status=STATUS_SUSPENDED, suspended_reason=reason)


def reactivate(procedure: Procedure) -> Procedure:
    return replace(procedure, status=STATUS_ACTIVE, suspended_reason=None)


# ---- esecuzione con piano di annullamento --------------------------------------------------------


@dataclass
class ProcedureRun:
    results: list = field(default_factory=list)
    undo_plan: list[RecordedStep] = field(default_factory=list)  # in ordine di esecuzione inversa
    not_undoable: list[str] = field(default_factory=list)
    drift: str | None = None

    @property
    def completed(self) -> int:
        return sum(1 for r in self.results if r.success)


_UNSAFE_LITERAL = re.compile(r"\$\{")


def _previous_value(adapter, step: RecordedStep) -> tuple[bool, str | None]:
    """(annullabile, valore) del campo prima di scriverci."""
    from core.computer_use.selector import SelectorEngine

    try:
        element = SelectorEngine(adapter).locate(step.selector, timeout_seconds=2.0)
    except Exception:
        return False, None
    if adapter.is_password(element):
        return False, None
    value = adapter.read_value(element)
    if value is None or _UNSAFE_LITERAL.search(value):
        return False, None
    return True, value


def check_app(adapter, procedure: Procedure) -> str | None:
    """Drift se la finestra bersaglio appartiene a un processo diverso da quello dimostrato."""
    if not procedure.app_process or not procedure.steps:
        return None
    from core.computer_use.ui_automation_adapter import WindowNotFoundError

    try:
        window = adapter.find_window_by_title_containing(procedure.steps[0].selector.window_title_contains, timeout_seconds=5.0)
    except WindowNotFoundError:
        return None  # lo segnalera' il primo passo con WINDOW_NOT_FOUND
    actual = process_name_of(adapter.process_id_of(window))
    if actual and actual.lower() != procedure.app_process.lower():
        return f"la finestra appartiene a {actual}, la procedura e' stata dimostrata su {procedure.app_process}"
    return None


def process_name_of(pid: int | None) -> str | None:
    if not pid:
        return None
    try:
        import psutil

        return psutil.Process(pid).name()
    except Exception:
        return None


def process_version_of(pid: int | None) -> str | None:
    """FileVersion dell'eseguibile (None se non leggibile)."""
    if not pid:
        return None
    try:
        import psutil
        import win32api

        exe = psutil.Process(pid).exe()
        info = win32api.GetFileVersionInfo(exe, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def run_procedure(agent, adapter, procedure: Procedure, parameters: dict[str, str] | None = None,
                  timeout_seconds: float = 5.0) -> ProcedureRun:
    """Esegue i passi fermandosi al primo fallimento; raccoglie il piano di annullamento."""
    run = ProcedureRun()
    app_drift = check_app(adapter, procedure)
    if app_drift:
        run.drift = app_drift
        return run
    values = {**procedure.defaults_dict(), **(parameters or {})}
    for index, step in enumerate(procedure.steps, start=1):
        undoable, previous = (False, None)
        if step.action == ACTION_TYPE:
            undoable, previous = _previous_value(adapter, step)
        result = replay_step(agent, adapter, step, timeout_seconds=timeout_seconds, parameters=values)
        run.results.append(result)
        if not result.success:
            if is_likely_drift(result):
                run.drift = f"passo {index}: {result.error}"
            break
        if step.action == ACTION_TYPE:
            if undoable:
                run.undo_plan.insert(0, replace(step, text=previous, undo=None, risk_intent=None))
            else:
                run.not_undoable.append(f"passo {index}: valore precedente non leggibile (campo protetto o assente)")
        elif step.action == ACTION_CLICK:
            if step.undo is not None:
                run.undo_plan.insert(0, step.undo)
            else:
                run.not_undoable.append(f"passo {index}: click senza annullamento noto")
    return run


def undo_run(agent, adapter, run: ProcedureRun, timeout_seconds: float = 5.0) -> list:
    """Esegue il piano di annullamento (dall'ultimo passo al primo), fermandosi al primo errore."""
    results = []
    for step in run.undo_plan:
        result = replay_step(agent, adapter, step, timeout_seconds=timeout_seconds)
        results.append(result)
        if not result.success:
            break
    return results
