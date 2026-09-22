"""Applicazione Jarvis (v3.1): HUD a schermo intero + tray + voce + hotkey, tutto su Qt.

Thread: Qt (HUD) sul thread principale; la sessione vocale (VAD + Whisper + core) su un
QThread; il core viene chiamato anche dalla barra comandi/chip su un thread separato. Tutto
cio' che tocca i widget passa per segnali Qt (thread-safe, in coda sul thread principale)."""
import os
import sys
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from core.gui.hud.overlay import HudOverlay
from core.logger import get_logger

HIDE_AFTER_RESPONSE_SECONDS = 6.0

DEFAULT_QUICK_ACTIONS = [
    ("Cosa c'è a schermo", "descrivi cosa vedi sullo schermo"),
    ("Leggi lo schermo", "leggi lo schermo"),
    ("Screenshot", "fai uno screenshot"),
    ("Timer 5 min", "metti un timer di 5 minuti"),
    ("Pomodoro", "avvia un pomodoro"),
    ("Cose da fare", "cosa devo fare"),
    ("Appunti", "leggimi gli appunti"),
    ("Promemoria", "che promemoria ho"),
    ("Spotify", "apri spotify"),
    ("YouTube", "apri youtube"),
    ("Pausa musica", "metti in pausa la musica"),
    ("Dettatura", "scrivi sotto dettatura"),
    ("Mostra desktop", "mostra il desktop"),
    ("Blocca PC", "blocca il computer"),
    ("Cosa sai fare", "cosa sai fare"),
]


def _build_tray_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    gradient = QRadialGradient(24, 22, 34)
    gradient.setColorAt(0.0, QColor(230, 245, 255))
    gradient.setColorAt(0.4, QColor(90, 170, 255))
    gradient.setColorAt(1.0, QColor(20, 60, 170))
    painter.setBrush(gradient)
    painter.setPen(QColor(150, 205, 255, 200))
    painter.drawEllipse(6, 6, 52, 52)
    painter.end()
    return QIcon(pixmap)


class Bridge(QObject):
    """Segnali emessi dai thread di voce/core, consumati sul thread Qt."""
    state = Signal(str, str)
    level = Signal(float, bool)
    hotkey = Signal()
    response = Signal(str, str)
    show = Signal(bool)
    hide = Signal()
    # F2.2.7/Gate F2 ("accessibilita' via sottotitoli"): a differenza di `state` (che mostra il
    # comando solo a frase FINITA, gia' collegato) questo porta il testo PARZIALE mentre l'utente
    # sta ancora parlando (WakeWordSession.on_transcript, TranscriptEvent.text) - la sottotitolazione
    # live che mancava davvero, non uno stub.
    transcript = Signal(str)


class VoiceThread(QThread):
    def __init__(self, session):
        super().__init__()
        self.session = session

    def run(self):
        try:
            self.session.run()
        except Exception:
            get_logger().exception("La sessione vocale si e' interrotta")


class JarvisApp:
    def __init__(self, core, session=None, hotkey: str = "ctrl+shift+j", auto_hide_seconds: float = 6.0,
                 mode: str = "full", backdrop: str = "clear", quick_actions: list = None):
        self.core = core
        self.session = session
        self.hotkey = hotkey
        self.hotkey_label = "+".join(part.capitalize() for part in hotkey.split("+"))
        self.logger = get_logger()
        try:
            # Senza un AppUserModelID esplicito le notifiche di Windows si intitolano "Python".
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Jake")
        except Exception:
            pass
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName("Jake")
        self.app.setApplicationDisplayName("Jake")
        self.bridge = Bridge()
        self.hud = HudOverlay(
            mode=mode, backdrop_mode=backdrop, auto_hide_seconds=auto_hide_seconds,
            hotkey_label=self.hotkey_label, snapshot_provider=self._snapshot,
        )
        self.custom_quick_actions = quick_actions or []
        self._voice_thread = None
        self._busy_lock = threading.Lock()
        self._listening_enabled = True

        self.bridge.state.connect(self._on_state)
        self.bridge.level.connect(self.hud.set_level)
        self.bridge.hotkey.connect(self._on_hotkey)
        self.bridge.response.connect(self._on_text_response)
        self.bridge.transcript.connect(self.hud.set_transcript)
        self.bridge.show.connect(lambda input_mode: self.hud.show_hud(input_mode=input_mode))
        self.bridge.hide.connect(self.hud.hide_hud)
        self.hud.submitted.connect(self._on_text_command)
        self.hud.command_requested.connect(self._on_chip_command)
        self.hud.mic_toggled.connect(self._toggle_listening)
        self.hud.orb_clicked.connect(self._on_orb_clicked)

        hooks = getattr(core, "session_hooks", None)
        if self.session is not None:
            self.session.on_state = lambda state, detail: self.bridge.state.emit(state, detail or "")
            self.session.on_level = lambda level, speech: self.bridge.level.emit(float(level), bool(speech))
            # F2.2.7 (sottotitolazione live): un partial arriva SOLO mentre l'utente sta ancora
            # parlando (VadListener.on_utterance_frame, gia' filtrato per privacy da
            # WakeWordSession._transcript_is_addressed prima di raggiungere questo callback -
            # mai il parlato ambientale/la dettatura). event.text cresce/si corregge a ogni
            # revisione (LocalAgreement-2): lo stesso QLabel gia' usato per il transcript a frase
            # finita (state == "thinking") si limita ad aggiornarsi PRIMA che la frase finisca,
            # invece di comparire tutto insieme solo alla fine.
            self.session.on_transcript = lambda event: self.bridge.transcript.emit(event.text)
        else:
            if hooks is not None:
                hooks.kind = "hud"
                hooks.set_state = lambda state, detail="": self.bridge.state.emit(state, detail or "")
            core.scheduler.on_due = self._on_reminder_due_text
            advisor = getattr(core, "system_advisor", None)
            if advisor is not None:
                advisor.on_advisory = self._on_advisory_text
        if hooks is not None:
            hooks.show_hud = lambda: self.bridge.show.emit(False)
            hooks.hide_hud = lambda: self.bridge.hide.emit()

        self._refresh_quick_actions()
        self.hud.load_history(self.core.memory_manager.get_recent_history(limit=30))
        self._build_tray()
        self._register_hotkey()

    # ---- dati per l'HUD ------------------------------------------------------------------

    def _snapshot(self) -> dict:
        core = self.core
        snapshot = {"skills": len(core.skill_registry.skills)}
        try:
            snapshot["window"] = core.desktop_context.get_current_window()
        except Exception:
            snapshot["window"] = None
        try:
            now = datetime.now().astimezone()
            timers = []
            for timer in core.reminder_manager.list_upcoming(kind="timer"):
                remaining = max(0, int((datetime.fromisoformat(timer["due_at"]) - now).total_seconds()))
                minutes, seconds = divmod(remaining, 60)
                timers.append({"label": timer["text"], "remaining": f"{minutes}:{seconds:02d}"})
            snapshot["timers"] = timers
            snapshot["reminders"] = [
                {"text": r["text"], "when": datetime.fromisoformat(r["due_at"]).astimezone().strftime("%H:%M")}
                for r in core.reminder_manager.list_upcoming(limit=5)
            ]
        except Exception:
            snapshot["timers"], snapshot["reminders"] = [], []
        try:
            snapshot["todos"] = core.skill_registry.todo_manager.list_pending(limit=6)
        except Exception:
            snapshot["todos"] = []
        try:
            from skills.notes import NOTES_PATH
            if NOTES_PATH.is_file():
                lines = [line.strip() for line in NOTES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
                snapshot["notes"] = lines[-3:]
        except Exception:
            pass
        try:
            snapshot["learned"] = [example.text for example in core.learning.list_taught()][-5:]
        except Exception:
            snapshot["learned"] = []
        try:
            import psutil
            snapshot["cpu"] = psutil.cpu_percent(interval=None)
            snapshot["ram"] = psutil.virtual_memory().percent
            battery = psutil.sensors_battery()
            if battery is not None:
                snapshot["battery"] = battery.percent
                snapshot["plugged"] = battery.power_plugged
        except Exception:
            pass
        return snapshot

    def _refresh_quick_actions(self) -> None:
        actions = [(label, command, False) for label, command in (self.custom_quick_actions or DEFAULT_QUICK_ACTIONS)]
        try:
            for example in self.core.learning.list_taught()[-8:]:
                actions.append((example.text, example.text, True))
        except Exception:
            pass
        self.hud.set_quick_actions(actions)

    # ---- costruzione -------------------------------------------------------------------

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_build_tray_icon(), self.app)
        self.tray.setToolTip("Jake 3.0")
        menu = QMenu()
        show_action = QAction(f"Apri la console  ({self.hotkey_label})", menu)
        show_action.triggered.connect(lambda: self.hud.show_hud(input_mode=True))
        menu.addAction(show_action)
        if self.session is not None:
            self.listen_action = QAction("Ascolto vocale attivo", menu)
            self.listen_action.setCheckable(True)
            self.listen_action.setChecked(True)
            self.listen_action.toggled.connect(self._toggle_listening)
            menu.addAction(self.listen_action)
        mode_action = QAction("HUD compatta (solo pillola)", menu)
        mode_action.setCheckable(True)
        mode_action.setChecked(self.hud.mode == "compact")
        mode_action.toggled.connect(lambda compact: self.hud.set_mode("compact" if compact else "full"))
        menu.addAction(mode_action)
        settings_action = QAction("Apri impostazioni (settings.json)", menu)
        settings_action.triggered.connect(self._open_settings)
        menu.addAction(settings_action)
        menu.addSeparator()
        quit_action = QAction("Esci", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _register_hotkey(self) -> None:
        try:
            import keyboard
            keyboard.add_hotkey(self.hotkey, lambda: self.bridge.hotkey.emit(), suppress=False)
        except Exception:
            self.logger.exception("Hotkey %s non registrata", self.hotkey)

    # ---- eventi ------------------------------------------------------------------------

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.hud.show_hud(input_mode=True)

    def _on_hotkey(self) -> None:
        if self.hud.isVisible() and self.hud.input_mode:
            self.hud.hide_hud()
        else:
            self.hud.show_hud(input_mode=True)

    def _toggle_listening(self, enabled: bool) -> None:
        self._listening_enabled = enabled
        self.hud.set_mic_enabled(enabled)
        if hasattr(self, "listen_action"):
            self.listen_action.blockSignals(True)
            self.listen_action.setChecked(enabled)
            self.listen_action.blockSignals(False)
        if self.session is None:
            return
        if enabled:
            self.session.resume_listening()
        else:
            self.session.pause_listening(minutes=24 * 60)

    def _on_orb_clicked(self) -> None:
        """Click sull'orb: apre l'ascolto senza dover dire 'Jake' (o riattiva il microfono)."""
        if self.session is None:
            self.hud.show_hud(input_mode=True)
            return
        if not self._listening_enabled:
            self._toggle_listening(True)
        self.session.arm_listening()
        self.hud.show_hud()
        self.hud.set_state("listening")

    def _open_settings(self) -> None:
        path = self.core.skill_registry.config.path
        try:
            os.startfile(str(path))
        except OSError:
            self.logger.warning("Impossibile aprire %s", path)

    def _on_state(self, state: str, detail: str) -> None:
        """Mappa gli stati della sessione vocale sull'HUD."""
        hud = self.hud
        if state == "exit":
            self.quit()
            return
        if state in ("listening", "transcribing"):
            hud.show_hud()
            hud.set_state(state)
            if state == "listening":
                hud.set_transcript("")
                hud.set_response("")
                hud.clear_steps()
        elif state == "thinking":
            hud.show_hud()
            hud.set_transcript(detail)
            hud.clear_steps()
            if detail:
                hud.add_turn("user", detail)
            hud.set_state("thinking")
        elif state == "working":
            hud.show_hud()
            hud.set_state("working")
            if detail:
                hud.add_step(detail)
        elif state == "responding":
            hud.show_hud()
            hud.set_state("responding")
            hud.set_response(detail)
            if detail:
                hud.add_turn("jake", detail)
            self._refresh_quick_actions()
        elif state == "speaking":
            if hud.isVisible():
                hud.set_state("speaking")
        elif state == "dictation":
            hud.show_hud()
            hud.set_state("dictation")
            if detail:
                hud.set_response(detail)
            hud.schedule_hide(4.0)
        elif state == "paused":
            hud.show_hud()
            hud.set_state("paused")
            hud.set_response(f"Non ascolto{(' per ' + detail) if detail else ''}. Dì «Jake, svegliati» per riprendere.")
            hud.schedule_hide(4.0)
        elif state == "notify":
            hud.show_hud()
            hud.set_state("notify", detail)
            self.tray.showMessage("Jake", detail, QSystemTrayIcon.Information, 6000)
            hud.schedule_hide(10.0)
        elif state == "error":
            hud.show_hud()
            hud.set_state("error", detail)
            hud.schedule_hide(8.0)
        elif state == "idle":
            if hud.isVisible() and not hud.input_mode:
                hud.set_state("idle")
                hud.schedule_hide(HIDE_AFTER_RESPONSE_SECONDS if hud.stage.response.text() else 1.5)

    def _on_reminder_due_text(self, reminder: dict) -> None:
        message = self.core.format_due_reminder(reminder)
        self.bridge.state.emit("notify", message)

    def _on_advisory_text(self, message: str) -> None:
        self.bridge.state.emit("notify", message)

    # ---- comandi testuali ----------------------------------------------------------------

    def _on_chip_command(self, text: str) -> None:
        self.hud.set_transcript(text)
        self.hud.set_state("thinking")
        self.hud.add_turn("user", text)
        self._on_text_command(text)

    def _on_text_command(self, text: str) -> None:
        self.hud.show_hud(input_mode=self.hud.input_mode)
        self.hud.clear_steps()

        def worker():
            with self._busy_lock:
                try:
                    response = self.core.answer(text)
                except Exception:
                    self.logger.exception("Errore eseguendo il comando testuale")
                    response = "Si è verificato un errore."
            self.bridge.response.emit(text, response)

        threading.Thread(target=worker, daemon=True).start()

    def _on_text_response(self, text: str, response: str) -> None:
        if response == self.core.EXIT_SENTINEL:
            self.quit()
            return
        self.hud.set_state("responding" if response else "idle")
        self.hud.set_response(response)
        if response:
            self.hud.add_turn("jake", response)
        self._refresh_quick_actions()
        self.hud.refresh_snapshot()
        if self.session is not None and response:
            self.session.speak(response)
        if not self.hud.input_mode:
            self.hud.schedule_hide(HIDE_AFTER_RESPONSE_SECONDS)
        else:
            self.hud.command_bar.input.setFocus()

    # ---- ciclo di vita -------------------------------------------------------------------

    def run(self) -> int:
        if self.session is not None:
            self._voice_thread = VoiceThread(self.session)
            self._voice_thread.start()
            self.tray.showMessage("Jake 3.0", f"Sono in ascolto: di' «Jake» oppure premi {self.hotkey_label}", QSystemTrayIcon.Information, 4000)
        else:
            self.tray.showMessage("Jake 3.0", f"Pronto (solo testo): premi {self.hotkey_label}", QSystemTrayIcon.Information, 4000)

        def welcome():
            self.hud.show_hud()
            self.hud.set_state("idle")
            self.hud.set_response(f"Sistemi online. Di' «Jake» oppure premi {self.hotkey_label}.")
            self.hud.schedule_hide(5.0)

        QTimer.singleShot(500, welcome)
        code = self.app.exec()
        self._cleanup()
        return code

    def quit(self) -> None:
        self.app.quit()

    def _cleanup(self) -> None:
        # F1.8.4 (stesso principio gia' applicato a JakeCore.shutdown/TaskAgent.on_step in
        # questa sessione): un fallimento qui spariva senza log - un utente che chiude Jake e
        # nota l'hotkey ancora attivo, o `core.shutdown()` (gia' esso stesso robusto per singolo
        # componente, ma non a prova di un fallimento nel proprio ciclo) che non ha salvato le
        # cache, non avrebbe avuto modo di scoprire perche'.
        try:
            if self.session is not None:
                self.session.stop()
            if self._voice_thread is not None:
                self._voice_thread.wait(3000)
        except Exception:
            self.logger.exception("Errore fermando la sessione voce durante la chiusura del HUD")
        try:
            self.core.shutdown()
        except Exception:
            self.logger.exception("Errore in core.shutdown() durante la chiusura del HUD")
        try:
            import keyboard
            keyboard.unhook_all_hotkeys()
        except Exception:
            self.logger.exception("Errore rimuovendo gli hotkey globali durante la chiusura del HUD")
        self.tray.hide()
