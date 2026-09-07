"""Osservazioni proattive (v3.2): Jake nota da solo batteria scarica e disco quasi pieno e lo
dice, come farebbe Jarvis ("Signore, la batteria e' al 12% e non e' in carica"), invece di
aspettare che tu glielo chieda. Stesso schema a thread daemon + doppio try/except di
ReminderScheduler/TriggerScheduler: un controllo che fallisce non deve mai fermare gli altri.

Ogni avviso scatta una sola volta per 'episodio' (fronte di salita sotto soglia), non ad ogni
controllo: altrimenti ogni 12% di batteria residua ripeterebbe lo stesso avviso ogni pochi
minuti. Rientra sopra soglia (batteria messa in carica, spazio liberato) riarma l'avviso per
la prossima volta."""
import os
import threading

from core.logger import get_logger

BATTERY_LOW_PERCENT = 15
DISK_FREE_LOW_GB = 3.0


class SystemAdvisor:
    def __init__(self, on_advisory=None, interval_seconds: float = 300, enabled: bool = True):
        self.on_advisory = on_advisory
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self._thread = None
        self._stop_event = threading.Event()
        self._logger = get_logger()
        self._battery_warned = False
        self._disk_warned = False

    def start(self) -> None:
        if not self.enabled or (self._thread is not None and self._thread.is_alive()):
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
                self._check_battery()
            except Exception:
                self._logger.exception("Errore controllando la batteria")
            try:
                self._check_disk()
            except Exception:
                self._logger.exception("Errore controllando lo spazio su disco")
            self._stop_event.wait(self.interval_seconds)

    def _check_battery(self) -> None:
        import psutil

        battery = psutil.sensors_battery()
        if battery is None:
            return
        low = battery.percent < BATTERY_LOW_PERCENT and not battery.power_plugged
        if low and not self._battery_warned:
            self._battery_warned = True
            self._advise(f"Batteria al {round(battery.percent)}% e non in carica: potrebbe spegnersi a breve.")
        elif not low:
            self._battery_warned = False

    def _check_disk(self) -> None:
        import psutil

        try:
            usage = psutil.disk_usage(f"{os.environ.get('SystemDrive', 'C:')}\\")
        except OSError:
            return
        free_gb = usage.free / (1024 ** 3)
        low = free_gb < DISK_FREE_LOW_GB
        if low and not self._disk_warned:
            self._disk_warned = True
            self._advise(f"Lo spazio libero sul disco di sistema sta finendo: restano {free_gb:.1f} GB.")
        elif not low:
            self._disk_warned = False

    def _advise(self, message: str) -> None:
        self._logger.info("Avviso proattivo: %s", message)
        if self.on_advisory is not None:
            try:
                self.on_advisory(message)
            except Exception:
                self._logger.exception("Errore nel callback di avviso proattivo")
