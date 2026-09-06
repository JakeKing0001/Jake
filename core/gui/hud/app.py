"""Applicazione Jarvis (v3.0): HUD + tray + voce + hotkey, tutto su Qt.

Thread: Qt (HUD) sul thread principale; la sessione vocale (VAD + Whisper + core) su un
QThread; il core viene chiamato anche dalla barra comandi su un worker separato. Tutto cio'
che tocca i widget passa per segnali Qt (thread-safe, in coda sul thread principale)."""
import sys
import threading

from PySide6.QtCore import QObject, Qt, QThread, Signal, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QMenu, QSystemTrayIcon, QTextEdit, QVBoxLayout

from core.gui.hud.overlay import HudWindow
from core.logger import get_logger

HIDE_AFTER_RESPONSE_SECONDS = 6.0


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


class VoiceThread(QThread):
    def __init__(self, session):
        super().__init__()
        self.session = session

    def run(self):
        try:
            self.session.run()
        except Exception:
            get_logger().exception("La sessione vocale si e' interrotta")


class HistoryDialog(QDialog):
    def __init__(self, core, on_submit):
        super().__init__()
        self.core = core
        self.on_submit = on_submit
        self.setWindowTitle("Jake - Cronologia e comandi")
        self.resize(640, 520)
        layout = QVBoxLayout(self)
        self.output = QTextEdit(self)
        self.output.setReadOnly(True)
        layout.addWidget(self.output)
        self.entry = QLineEdit(self)
        self.entry.setPlaceholderText("Scrivi un comando e premi Invio")
        self.entry.returnPressed.connect(self._submit)
        layout.addWidget(self.entry)
        self.setStyleSheet(
            "QDialog { background: #0f1a30; } QTextEdit, QLineEdit { background: #16223d; color: #eaf4ff; "
            "border: 1px solid #2f4a80; border-radius: 8px; padding: 6px; font-family: 'Segoe UI'; font-size: 11pt; }"
        )
        self.reload()

    def reload(self):
        self.output.clear()
        for turn in self.core.memory_manager.get_recent_history(limit=60):
            label = "Tu" if turn["role"] == "user" else "Jake"
            self.output.append(f"<b>{label}</b> › {turn['text']}")

    def append(self, text: str, response: str):
        self.output.append(f"<b>Tu</b> › {text}")
        self.output.append(f"<b>Jake</b> › {response}")

    def _submit(self):
        text = self.entry.text().strip()
        if text:
            self.entry.clear()
            self.on_submit(text)


class JarvisApp:
    def __init__(self, core, session=None, hotkey: str = "ctrl+shift+j", auto_hide_seconds: float = 5.0, acrylic: bool = True):
        self.core = core
        self.session = session
        self.hotkey = hotkey
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
        self.hud = HudWindow(auto_hide_seconds=auto_hide_seconds, acrylic=acrylic)
        self.history_dialog = None
        self._voice_thread = None
        self._busy_lock = threading.Lock()

        self.bridge.state.connect(self._on_state)
        self.bridge.level.connect(self.hud.set_level)
        self.bridge.hotkey.connect(self._on_hotkey)
        self.bridge.response.connect(self._on_text_response)
        self.hud.submitted.connect(self._on_text_command)

        if self.session is not None:
            self.session.on_state = lambda state, detail: self.bridge.state.emit(state, detail or "")
            self.session.on_level = lambda level, speech: self.bridge.level.emit(float(level), bool(speech))
        else:
            hooks = getattr(core, "session_hooks", None)
            if hooks is not None:
                hooks.kind = "hud"
                hooks.set_state = lambda state, detail="": self.bridge.state.emit(state, detail or "")
            core.scheduler.on_due = self._on_reminder_due_text

        self._build_tray()
        self._register_hotkey()

    # ---- costruzione -------------------------------------------------------------------

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_build_tray_icon(), self.app)
        self.tray.setToolTip("Jake 3.0")
        menu = QMenu()
        show_action = QAction("Scrivi un comando  (" + self.hotkey.replace("+", " + ").title() + ")", menu)
        show_action.triggered.connect(lambda: self.hud.show_hud(input_mode=True))
        menu.addAction(show_action)
        history_action = QAction("Cronologia e comandi...", menu)
        history_action.triggered.connect(self._open_history)
        menu.addAction(history_action)
        if self.session is not None:
            self.listen_action = QAction("Ascolto vocale attivo", menu)
            self.listen_action.setCheckable(True)
            self.listen_action.setChecked(True)
            self.listen_action.toggled.connect(self._toggle_listening)
            menu.addAction(self.listen_action)
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
        if self.session is None:
            return
        if enabled:
            self.session.resume_listening()
        else:
            self.session.pause_listening(minutes=24 * 60)

    def _open_history(self) -> None:
        if self.history_dialog is None:
            self.history_dialog = HistoryDialog(self.core, self._on_text_command)
        self.history_dialog.reload()
        self.history_dialog.show()
        self.history_dialog.raise_()
        self.history_dialog.activateWindow()

    def _open_settings(self) -> None:
        import os
        path = self.core.skill_registry.config.path
        try:
            os.startfile(str(path))
        except OSError:
            self.logger.warning("Impossibile aprire %s", path)

    def _on_state(self, state: str, detail: str) -> None:
        """Mappa gli stati della sessione vocale sull'HUD."""
        if state == "exit":
            self.quit()
            return
        if state in ("listening", "transcribing"):
            self.hud.show_hud()
            self.hud.set_state(state)
            if state == "listening":
                self.hud.set_transcript("")
                self.hud.set_response("")
        elif state == "thinking":
            self.hud.show_hud()
            self.hud.set_transcript(detail)
            self.hud.set_state("thinking")
        elif state == "responding":
            self.hud.show_hud()
            self.hud.set_state("responding")
            self.hud.set_response(detail)
        elif state == "speaking":
            if self.hud.isVisible():
                self.hud.set_state("speaking")
        elif state == "dictation":
            self.hud.show_hud()
            self.hud.set_state("dictation")
            if detail:
                self.hud.set_response(detail)
            self.hud.schedule_hide(4.0)
        elif state == "paused":
            self.hud.show_hud()
            self.hud.set_state("paused")
            self.hud.set_response(f"Non ascolto{(' per ' + detail) if detail else ''}. Dì «Jake, svegliati» per riprendere.")
            self.hud.schedule_hide(4.0)
        elif state == "notify":
            self.hud.show_hud()
            self.hud.set_state("notify", detail)
            self.tray.showMessage("Jake", detail, QSystemTrayIcon.Information, 6000)
            self.hud.schedule_hide(10.0)
        elif state == "error":
            self.hud.show_hud()
            self.hud.set_state("error", detail)
            self.hud.schedule_hide(8.0)
        elif state == "idle":
            if self.hud.isVisible() and not self.hud.input_mode:
                self.hud.set_state("idle")
                self.hud.schedule_hide(HIDE_AFTER_RESPONSE_SECONDS if self.hud.response else 1.5)

    def _on_reminder_due_text(self, reminder: dict) -> None:
        message = self.core.format_due_reminder(reminder)
        self.bridge.state.emit("notify", message)

    # ---- comandi testuali ----------------------------------------------------------------

    def _on_text_command(self, text: str) -> None:
        self.hud.show_hud(input_mode=self.hud.input_mode)
        self.hud.set_transcript(text)
        self.hud.set_state("thinking")

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
        if self.history_dialog is not None and self.history_dialog.isVisible():
            self.history_dialog.append(text, response)
        if self.session is not None and response:
            self.session.speak(response)
        if not self.hud.input_mode:
            self.hud.schedule_hide(HIDE_AFTER_RESPONSE_SECONDS)
        else:
            self.hud.input.setFocus()

    # ---- ciclo di vita -------------------------------------------------------------------

    def run(self) -> int:
        if self.session is not None:
            self._voice_thread = VoiceThread(self.session)
            self._voice_thread.start()
            self.tray.showMessage("Jake 3.0", "Sono in ascolto: di' «Jake» oppure premi " + self.hotkey.replace("+", " + ").title(), QSystemTrayIcon.Information, 4000)
        else:
            self.tray.showMessage("Jake 3.0", "Pronto (solo testo): premi " + self.hotkey.replace("+", " + ").title(), QSystemTrayIcon.Information, 4000)
        QTimer.singleShot(400, lambda: (self.hud.show_hud(), self.hud.set_state("idle"), self.hud.set_response("Sistemi online. Di' «Jake» o premi " + self.hotkey.replace("+", " + ").title() + "."), self.hud.schedule_hide(4.0)))
        code = self.app.exec()
        self._cleanup()
        return code

    def quit(self) -> None:
        self.app.quit()

    def _cleanup(self) -> None:
        try:
            if self.session is not None:
                self.session.stop()
            if self._voice_thread is not None:
                self._voice_thread.wait(3000)
        except Exception:
            pass
        try:
            self.core.shutdown()
        except Exception:
            pass
        try:
            import keyboard
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        self.tray.hide()
