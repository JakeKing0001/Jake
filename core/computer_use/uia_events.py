"""Sottoscrizione agli eventi UI Automation per invalidare una cache (F3.2.4).

Perche' un thread e un apartment COM PROPRI: gli eventi UIA vengono recapitati al client dal sistema,
non "quando il client li chiede". L'adapter (`UIAutomationAdapter`) vive in un apartment STA - quello
del thread che lo ha creato - e un handler registrato li' riceverebbe i callback solo se quel thread
pompasse messaggi COM, cosa che un chiamante sincrono come le skill non fa mai (misurato: con il pump
di `comtypes.client.PumpEvents` e senza, zero eventi). Qui un thread dedicato inizializza COM come MTA,
crea un proprio `IUIAutomation` e registra li' gli handler: i callback arrivano su thread del sistema e
non dipendono dal thread principale. Un elemento non attraversa gli apartment: si passa l'HWND della
finestra, e la risoluzione in elemento avviene nel thread dell'ascoltatore.

**Limite misurato, non ipotizzato**: quali eventi arrivano dipende dal PROVIDER dell'app. Sulla fixture
Qt di F3.1 (PySide6) arrivano gli eventi di FOCUS, ma NON quelli di struttura, di proprieta'
(Name/IsEnabled/IsSelected/ToggleState) ne' Invoke/Selection: Qt non li solleva per i propri widget. Un
chiamante non deve quindi fidarsi del solo silenzio degli eventi come "niente e' cambiato": chi usa
`UIAEventListener` (vedi `core/computer_use/uia_cache.py::TreeCache`) tiene anche un TTL.

Non e' un modulo per le skill: espone solo `subscribe(hwnd, callback)`. Il callback gira sul thread di
callback di UIA e deve restare leggerissimo (impostare un flag)."""
from __future__ import annotations

import threading
from collections.abc import Callable

import comtypes
import comtypes.client

from core.computer_use.ui_automation_adapter import UIA
from core.logger import get_logger

EVENT_FOCUS = "focus"
EVENT_STRUCTURE = "structure"
EVENT_PROPERTY = "property"
EVENT_AUTOMATION = "automation"

# proprieta' il cui cambiamento rende obsoleta una descrizione dell'albero
_WATCHED_PROPERTIES = [
    UIA.UIA_NamePropertyId, UIA.UIA_IsEnabledPropertyId, UIA.UIA_ValueValuePropertyId,
    UIA.UIA_ToggleToggleStatePropertyId, UIA.UIA_SelectionItemIsSelectedPropertyId,
    UIA.UIA_BoundingRectanglePropertyId, UIA.UIA_IsOffscreenPropertyId,
]
_WATCHED_AUTOMATION_EVENTS = [
    UIA.UIA_Window_WindowOpenedEventId, UIA.UIA_Window_WindowClosedEventId, UIA.UIA_Invoke_InvokedEventId,
    UIA.UIA_SelectionItem_ElementSelectedEventId, UIA.UIA_MenuOpenedEventId, UIA.UIA_MenuClosedEventId,
]

Callback = Callable[[str, int], None]


class UIAEventListener:
    """Un thread MTA con un `IUIAutomation` proprio. `subscribe` registra gli handler sulla finestra data;
    ogni evento chiama `callback(tipo, dettaglio)`. Un solo ascoltatore puo' servire piu' finestre."""

    def __init__(self) -> None:
        self._logger = get_logger()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._commands: list[tuple[str, tuple, threading.Event, list]] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self.error: str | None = None
        self.events_received = 0
        self._thread = threading.Thread(target=self._run, daemon=True, name="uia-events")
        self._thread.start()
        self._ready.wait(10)

    @property
    def available(self) -> bool:
        return self._ready.is_set() and self.error is None and self._thread.is_alive()

    # ---- API (thread del chiamante) -------------------------------------------------------------------

    def subscribe(self, hwnd: int, callback: Callback, timeout: float = 5.0) -> bool:
        """Registra gli handler sulla finestra `hwnd`. True se andata a buon fine."""
        return self._command("subscribe", (hwnd, callback), timeout)

    def unsubscribe_all(self, timeout: float = 5.0) -> bool:
        return self._command("unsubscribe", (), timeout)

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=5)

    def _command(self, name: str, args: tuple, timeout: float) -> bool:
        if not self.available:
            return False
        done, result = threading.Event(), []
        with self._lock:
            self._commands.append((name, args, done, result))
        self._wake.set()
        return done.wait(timeout) and bool(result and result[0])

    # ---- thread MTA -------------------------------------------------------------------------------------

    def _run(self) -> None:
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self._ready.set()
            return
        handlers: list = []  # tiene vivi gli oggetti COM: un handler raccolto dal GC smette di funzionare in silenzio
        self._ready.set()
        try:
            while not self._stop.is_set():
                self._wake.wait(0.2)
                self._wake.clear()
                with self._lock:
                    pending, self._commands = self._commands, []
                for name, args, done, result in pending:
                    try:
                        if name == "subscribe":
                            self._subscribe(uia, handlers, *args)
                        else:
                            uia.RemoveAllEventHandlers()
                            handlers.clear()
                        result.append(True)
                    except Exception:
                        self._logger.exception("Errore nella sottoscrizione eventi UIA")
                        result.append(False)
                    finally:
                        done.set()
        finally:
            try:
                uia.RemoveAllEventHandlers()
            except Exception:
                pass
            handlers.clear()
            comtypes.CoUninitialize()

    def _subscribe(self, uia, handlers: list, hwnd: int, callback: Callback) -> None:
        window = uia.ElementFromHandle(hwnd)
        window_pid = window.CurrentProcessId
        listener = self

        def notify(kind: str, detail: int) -> None:
            listener.events_received += 1
            try:
                callback(kind, detail)
            except Exception:
                listener._logger.exception("Errore nel callback degli eventi UIA")

        class Automation(comtypes.COMObject):
            _com_interfaces_ = [UIA.IUIAutomationEventHandler]

            def HandleAutomationEvent(self, sender, event_id):
                notify(EVENT_AUTOMATION, int(event_id))

        class Property(comtypes.COMObject):
            _com_interfaces_ = [UIA.IUIAutomationPropertyChangedEventHandler]

            def HandlePropertyChangedEvent(self, sender, property_id, new_value):
                notify(EVENT_PROPERTY, int(property_id))

        class Structure(comtypes.COMObject):
            _com_interfaces_ = [UIA.IUIAutomationStructureChangedEventHandler]

            def HandleStructureChangedEvent(self, sender, change_type, runtime_id):
                notify(EVENT_STRUCTURE, int(change_type))

        class Focus(comtypes.COMObject):
            _com_interfaces_ = [UIA.IUIAutomationFocusChangedEventHandler]

            def HandleFocusChangedEvent(self, sender):
                # il focus e' un evento GLOBALE del desktop: si tiene solo quello dell'app osservata (nessun
                # contenuto viene letto, ma un focus altrui non deve invalidare la cache di questa finestra)
                try:
                    if sender.CurrentProcessId != window_pid:
                        return
                except Exception:
                    return
                notify(EVENT_FOCUS, 0)

        automation, prop, structure, focus = Automation(), Property(), Structure(), Focus()
        handlers.extend([automation, prop, structure, focus])
        for event_id in _WATCHED_AUTOMATION_EVENTS:
            try:
                uia.AddAutomationEventHandler(
                    event_id, window, UIA.TreeScope_Subtree, None, automation.QueryInterface(UIA.IUIAutomationEventHandler),
                )
            except comtypes.COMError:
                pass  # un evento che questo sistema/provider non supporta non deve far fallire gli altri
        uia.AddPropertyChangedEventHandler(
            window, UIA.TreeScope_Subtree, None,
            prop.QueryInterface(UIA.IUIAutomationPropertyChangedEventHandler), _WATCHED_PROPERTIES,
        )
        uia.AddStructureChangedEventHandler(
            window, UIA.TreeScope_Subtree, None, structure.QueryInterface(UIA.IUIAutomationStructureChangedEventHandler),
        )
        uia.AddFocusChangedEventHandler(None, focus.QueryInterface(UIA.IUIAutomationFocusChangedEventHandler))
