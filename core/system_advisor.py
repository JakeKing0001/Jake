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
import time
from pathlib import Path

from core.logger import get_logger

BATTERY_LOW_PERCENT = 15
DISK_FREE_LOW_GB = 3.0
STALE_TODO_DAYS = 3
# F6 (Proactive Intelligence & Autonomy, "digital housekeeping... prima come suggerimenti" -
# vedi ROADMAP.md): soglie deliberatamente larghe, per non diventare invadenti su una cartella
# che quasi tutti lasciano accumulare per settimane senza che sia un problema reale.
DOWNLOADS_SIZE_WARN_GB = 5.0
DOWNLOADS_OLD_FILE_DAYS = 30
DOWNLOADS_OLD_FILES_WARN_COUNT = 20
DOWNLOADS_MAX_SCANNED = 2000


class SystemAdvisor:
    def __init__(
        self, on_advisory=None, interval_seconds: float = 300, enabled: bool = True, todo_manager=None,
        downloads_dir: Path = None, memory_manager=None,
    ):
        self.on_advisory = on_advisory
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self.todo_manager = todo_manager
        # F6: iniettabile per i test (mai la vera cartella Download dell'utente in un test
        # automatico), default alla cartella Download reale altrimenti.
        self.downloads_dir = Path(downloads_dir) if downloads_dir else Path.home() / "Downloads"
        # F5 (Memory 2.0, scadenza): il "futuro hook di manutenzione" gia' previsto nel docstring
        # di MemoryManager.purge_expired() - senza questo, un ricordo con ttl_days (skills/
        # remember.py) smetteva di COMPARIRE in recall() alla scadenza ma restava per sempre sul
        # disco, mai davvero rimosso.
        self.memory_manager = memory_manager
        self._thread = None
        self._stop_event = threading.Event()
        self._logger = get_logger()
        self._battery_warned = False
        self._disk_warned = False
        self._downloads_warned = False
        self._stale_todo_ids_warned: set = set()  # (v4.2) gia' segnalate: non ripeterle ogni giro

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
            try:
                self._check_stale_todos()
            except Exception:
                self._logger.exception("Errore controllando le attivita' in sospeso")
            try:
                self._check_downloads_clutter()
            except Exception:
                self._logger.exception("Errore controllando la cartella Download")
            try:
                self._purge_expired_memories()
            except Exception:
                self._logger.exception("Errore ripulendo i ricordi scaduti")
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

    def _check_stale_todos(self) -> None:
        """Nota da sola una todo dimenticata (v4.2, Proactive Intelligence), invece di aspettare
        che l'utente chieda LIST_TODOS e si accorga solo allora di averla lasciata li'. Ogni
        attivita' viene segnalata una sola volta (per id): completarla o cancellarla non e'
        necessario perche' non venga piu' ripetuta, ma se torna a essere la piu' vecchia dopo che
        le altre sono state smaltite non viene ri-segnalata piu' del dovuto."""
        if self.todo_manager is None:
            return
        stale = self.todo_manager.list_stale_pending(days=STALE_TODO_DAYS)
        stale_ids = {todo["id"] for todo in stale}
        self._stale_todo_ids_warned &= stale_ids  # dimentica gli id non piu' in sospeso/stale
        new_ones = [todo for todo in stale if todo["id"] not in self._stale_todo_ids_warned]
        if not new_ones:
            return
        self._stale_todo_ids_warned |= {todo["id"] for todo in new_ones}
        if len(new_ones) == 1:
            self._advise(f"C'e' un'attivita' in sospeso da un po' nella todo list: \"{new_ones[0]['text']}\".")
        else:
            oldest = new_ones[0]["text"]
            self._advise(f"Hai {len(new_ones)} attivita' in sospeso da un po' nella todo list, la piu' vecchia e': \"{oldest}\".")

    def _check_downloads_clutter(self) -> None:
        """Nota da solo se la cartella Download ha accumulato troppo spazio o troppi file
        vecchi (F6, Proactive Intelligence & Autonomy: "digital housekeeping... prima come
        suggerimenti", vedi ROADMAP.md) - un suggerimento, non un'azione: Jake non cancella
        nulla da solo. Legge solo dimensione/data di modifica (os.stat), MAI il contenuto dei
        file: a differenza di FIND_DUPLICATE_FILES (skills/file_utils2.py, che legge e hasha
        ogni file per confrontarne il contenuto - va bene per una richiesta esplicita
        dell'utente, troppo costoso per un controllo periodico ogni pochi minuti in background).
        Solo il livello piu' alto della cartella (non ricorsivo), con lo stesso limite di
        sicurezza (DOWNLOADS_MAX_SCANNED) gia' usato altrove per non scandire all'infinito una
        cartella enorme."""
        if not self.downloads_dir.is_dir():
            return

        total_bytes = 0
        old_count = 0
        scanned = 0
        cutoff = time.time() - DOWNLOADS_OLD_FILE_DAYS * 86400
        try:
            with os.scandir(self.downloads_dir) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned > DOWNLOADS_MAX_SCANNED:
                        break
                    try:
                        if not entry.is_file():
                            continue
                        stat = entry.stat()
                    except OSError:
                        continue
                    total_bytes += stat.st_size
                    if stat.st_mtime < cutoff:
                        old_count += 1
        except OSError:
            return

        total_gb = total_bytes / (1024 ** 3)
        cluttered = total_gb >= DOWNLOADS_SIZE_WARN_GB or old_count >= DOWNLOADS_OLD_FILES_WARN_COUNT
        if cluttered and not self._downloads_warned:
            self._downloads_warned = True
            detail = f" ({old_count} più vecchi di {DOWNLOADS_OLD_FILE_DAYS} giorni)" if old_count else ""
            self._advise(
                f"La cartella Download ha accumulato {total_gb:.1f} GB{detail}: "
                "potresti fare un po' di pulizia quando hai tempo."
            )
        elif not cluttered:
            self._downloads_warned = False

    def _purge_expired_memories(self) -> None:
        """Rimuove per davvero i ricordi con ttl_days scaduto (F5, Memory 2.0). Silenzioso, non
        un avviso: a differenza di batteria/disco/Download, un ricordo scaduto e' esattamente
        cio' che l'utente ha chiesto impostando quella scadenza al momento di salvarlo (skills/
        remember.py) - non e' 'disordine' da segnalare, e' manutenzione di routine attesa. Resta
        comunque loggato (quanti e quando) per audit, coerente con 'memoria sotto il controllo
        dell'utente' (principi non negoziabili in cima a ROADMAP.md)."""
        if self.memory_manager is None:
            return
        removed = self.memory_manager.purge_expired()
        if removed:
            self._logger.info("Rimossi %d ricordi scaduti.", removed)

    def _advise(self, message: str) -> None:
        self._logger.info("Avviso proattivo: %s", message)
        if self.on_advisory is not None:
            try:
                self.on_advisory(message)
            except Exception:
                self._logger.exception("Errore nel callback di avviso proattivo")
