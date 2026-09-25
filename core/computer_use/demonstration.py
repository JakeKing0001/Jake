"""F3.8.1-F3.8.3 — imparare una procedura guardando l'utente.

Una dimostrazione diventa una lista di `RecordedStep` SEMANTICI: quale elemento (nome/ruolo/
automation id, dentro quale finestra) e cosa ci e' stato fatto. Mai coordinate, mai video, mai
screenshot. Due parti separate:

- `DemonstrationRecorder` (puro, testabile): trasforma osservazioni ("click su questo elemento",
  "questo campo ora contiene X") in passi, eliminando i click che servivano solo a dare il fuoco a
  un campo poi compilato e tenendo l'ultimo valore di un campo modificato piu' volte;
- `UiaDemonstrationSampler`: su un thread proprio legge lo stato del tasto sinistro del mouse
  (Win32) e, a ogni pressione, l'elemento UI Automation sotto il cursore; a ogni cambio di fuoco,
  il valore del campo appena lasciato. Registra SOLO dentro la finestra bersaglio (stesso processo):
  cio' che l'utente fa altrove non viene nemmeno letto. Il contenuto di un campo password non
  viene mai letto: diventa un parametro obbligatorio senza valore.

`generalize` (F3.8.2) trasforma ogni testo digitato in un parametro `${nome}` con il valore
dimostrato come predefinito; `describe_steps` (F3.8.3) produce la procedura in parole da mostrare
all'utente prima di salvarla."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

from core.computer_use.procedure import ACTION_CLICK, ACTION_TYPE, RecordedStep
from core.computer_use.selector import ElementSelector

# Ruoli in cui un click serve di solito solo a dare il fuoco prima di scrivere.
_TEXT_ROLES = frozenset({"Edit", "Document", "ComboBox"})
PASSWORD_PLACEHOLDER = "password"


@dataclass(frozen=True)
class Observation:
    kind: str  # "click" | "value"
    name: str
    control_type: str
    automation_id: str
    value: str | None = None
    secret: bool = False  # campo password: il valore non e' mai stato letto


class DemonstrationRecorder:
    def __init__(self, window_title_contains: str) -> None:
        if not window_title_contains.strip():
            raise ValueError("serve una sottostringa del titolo della finestra bersaglio")
        self.window_title_contains = window_title_contains
        self._observations: list[Observation] = []

    def observe(self, observation: Observation) -> None:
        self._observations.append(observation)

    def _selector(self, obs: Observation) -> ElementSelector:
        # automation_id quando c'e': sopravvive a traduzione e tema (F3.3.7); il ruolo restringe.
        if obs.automation_id:
            return ElementSelector(automation_id=obs.automation_id, control_type=obs.control_type or None,
                                   window_title_contains=self.window_title_contains)
        return ElementSelector(name=obs.name or None, control_type=obs.control_type or None,
                               window_title_contains=self.window_title_contains)

    @staticmethod
    def _same_element(a: Observation, b: Observation) -> bool:
        return (a.automation_id, a.name, a.control_type) == (b.automation_id, b.name, b.control_type)

    def steps(self) -> list[RecordedStep]:
        merged: list[Observation] = []
        for obs in self._observations:
            if obs.kind == "value" and merged and merged[-1].kind == "value" and self._same_element(merged[-1], obs):
                merged[-1] = obs  # stesso campo modificato piu' volte: conta l'ultimo valore
                continue
            if obs.kind == "value" and merged and merged[-1].kind == "click" and self._same_element(merged[-1], obs) \
                    and merged[-1].control_type in _TEXT_ROLES:
                merged[-1] = obs  # il click serviva solo a dare il fuoco al campo poi compilato
                continue
            merged.append(obs)
        steps = []
        for obs in merged:
            if not (obs.automation_id or obs.name):
                continue  # un elemento senza identita' semantica non e' rigiocabile: niente coordinate
            if obs.kind == "click":
                steps.append(RecordedStep(action=ACTION_CLICK, selector=self._selector(obs)))
            else:
                text = "${" + PASSWORD_PLACEHOLDER + "}" if obs.secret else (obs.value or "")
                steps.append(RecordedStep(action=ACTION_TYPE, selector=self._selector(obs), text=text))
        return steps


def _parameter_name(step: RecordedStep, used: set[str]) -> str:
    source = step.selector.automation_id or step.selector.name or "campo"
    base = re.sub(r"[^a-z0-9_]", "_", source.rsplit(".", 1)[-1].lower()).strip("_") or "campo"
    if not re.match(r"[a-z_]", base):
        base = f"campo_{base}"
    name, index = base, 2
    while name in used:
        name, index = f"{base}_{index}", index + 1
    used.add(name)
    return name


def generalize(steps: list[RecordedStep]) -> tuple[list[RecordedStep], dict[str, str]]:
    """Ogni testo scritto letterale diventa `${parametro}`; il valore dimostrato resta il predefinito.
    Un segnaposto gia' presente (es. la password) resta obbligatorio, senza predefinito."""
    from dataclasses import replace

    used: set[str] = {PASSWORD_PLACEHOLDER}
    defaults: dict[str, str] = {}
    out = []
    for step in steps:
        if step.action == ACTION_TYPE and step.text is not None and "${" not in step.text:
            name = _parameter_name(step, used)
            defaults[name] = step.text
            step = replace(step, text="${" + name + "}")
        out.append(step)
    return out, defaults


def describe_steps(steps: list[RecordedStep], defaults: dict[str, str] | None = None) -> list[str]:
    defaults = defaults or {}
    lines = []
    for index, step in enumerate(steps, start=1):
        target = step.selector.name or step.selector.automation_id or step.selector.control_type
        where = f"«{target}»" + (f" ({step.selector.control_type})" if step.selector.control_type else "")
        if step.action == ACTION_CLICK:
            lines.append(f"{index}. Clicca {where}")
        else:
            params = re.findall(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}", step.text or "")
            shown = ", ".join(f"{p} (predefinito: {defaults[p]!r})" if p in defaults else f"{p} (obbligatorio)" for p in params)
            lines.append(f"{index}. Scrivi in {where}: {shown or repr(step.text)}")
        if step.risk_intent:
            lines[-1] += f" [rischio dichiarato: {step.risk_intent}]"
    return lines


class UiaDemonstrationSampler:
    """Campiona le azioni reali dell'utente nella finestra bersaglio (thread proprio, COM proprio)."""

    POLL_SECONDS = 0.02
    VK_LBUTTON = 0x01

    def __init__(self, window_title_contains: str, adapter_factory=None) -> None:
        self.recorder = DemonstrationRecorder(window_title_contains)
        self._adapter_factory = adapter_factory
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    def start(self, timeout_s: float = 10.0) -> bool:
        self._thread = threading.Thread(target=self._run, name="jake-demonstration", daemon=True)
        self._thread.start()
        return self._ready.wait(timeout_s) and self.error is None

    def stop(self, timeout_s: float = 5.0) -> list[RecordedStep]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_s)
        return self.recorder.steps()

    # ---- thread di campionamento -----------------------------------------------------------------

    def _run(self) -> None:
        import ctypes

        from core.computer_use.ui_automation_adapter import UIAutomationAdapter, WindowNotFoundError

        adapter = (self._adapter_factory or UIAutomationAdapter)()
        try:
            window = adapter.find_window_by_title_containing(self.recorder.window_title_contains, timeout_seconds=5.0)
        except WindowNotFoundError as exc:
            self.error = str(exc)
            self._ready.set()
            return
        self._pid = adapter.process_id_of(window)
        self._adapter = adapter
        self._focused = None
        self._focused_initial = None
        self._track_focus()
        self._ready.set()
        user32 = ctypes.windll.user32
        was_down = False
        user32.GetAsyncKeyState(self.VK_LBUTTON)  # azzera il bit "premuto dall'ultima lettura"
        while not self._stop.is_set():
            state = user32.GetAsyncKeyState(self.VK_LBUTTON)
            down = bool(state & 0x8000)
            # Un click piu' breve dell'intervallo di campionamento lascia solo il bit basso acceso.
            pressed_meanwhile = bool(state & 0x0001) and not was_down
            if (down and not was_down) or (pressed_meanwhile and not down):
                self._on_press(user32)
            elif not down:
                self._track_focus()
            was_down = down
            time.sleep(self.POLL_SECONDS)
        self._flush_value(self._focused)

    def _in_target(self, element) -> bool:
        return element is not None and self._adapter.process_id_of(element) == self._pid

    def _observation(self, kind: str, element, value: str | None = None, secret: bool = False) -> Observation | None:
        info = self._adapter.describe_element(element)
        if info is None:
            return None
        return Observation(kind, info.name, info.control_type, info.automation_id, value, secret)

    def _flush_value(self, element) -> None:
        if element is None or not self._in_target(element):
            return
        if self._adapter.is_password(element):
            obs = self._observation("value", element, secret=True)
            if obs is not None and self._focused_initial != "<password>":
                self.recorder.observe(obs)
            return
        value = self._adapter.read_value(element)
        if value is None or value == self._focused_initial:
            return
        obs = self._observation("value", element, value=value)
        if obs is not None:
            self.recorder.observe(obs)

    def _track_focus(self) -> None:
        focused = self._adapter.focused_element()
        if focused is None:
            return
        if self._focused is not None and self._adapter._uia.CompareElements(focused, self._focused):
            return
        self._flush_value(self._focused)
        self._focused = focused if self._in_target(focused) else None
        if self._focused is not None:
            self._focused_initial = "<password>" if self._adapter.is_password(focused) else self._adapter.read_value(focused)
        else:
            self._focused_initial = None

    def _on_press(self, user32) -> None:
        import ctypes
        import ctypes.wintypes

        # Prima il valore del campo che si sta lasciando: l'ordine dei passi deve essere quello reale.
        self._flush_value(self._focused)
        self._focused = None
        point = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        element = self._adapter.element_from_point(point.x, point.y)
        if not self._in_target(element):
            return
        obs = self._observation("click", element)
        if obs is not None:
            self.recorder.observe(obs)
