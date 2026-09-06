"""Finestra HUD (v3.0): pannello di vetro semitrasparente blu, sempre in primo piano
(z-order massimo), frameless, con blur acrilico dietro. Un orb animato racconta lo stato di
Jake (in ascolto / sto capendo / ci penso / parlo), una forma d'onda segue il microfono, il
testo mostra cosa ha sentito e cosa risponde. In modalita' vocale la finestra e' click-through
e non prende mai il focus; con la hotkey diventa una barra comandi con campo di testo."""
import math
import random
import time

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from core.gui.hud import win_effects

STATE_LABELS = {
    "idle": "Jake · pronto",
    "listening": "Ti ascolto...",
    "transcribing": "Sto capendo...",
    "thinking": "Ci penso...",
    "responding": "Jake",
    "speaking": "Jake",
    "dictation": "Dettatura attiva",
    "paused": "In pausa",
    "notify": "Avviso",
    "error": "Problema",
    "input": "Scrivi un comando",
}

STATE_COLORS = {
    "idle": QColor(90, 160, 255),
    "listening": QColor(80, 220, 255),
    "transcribing": QColor(120, 200, 255),
    "thinking": QColor(160, 140, 255),
    "responding": QColor(100, 190, 255),
    "speaking": QColor(100, 190, 255),
    "dictation": QColor(120, 255, 190),
    "paused": QColor(150, 160, 190),
    "notify": QColor(255, 200, 90),
    "error": QColor(255, 110, 110),
    "input": QColor(90, 170, 255),
}


class HudWindow(QWidget):
    submitted = Signal(str)  # comando scritto nella barra
    closed_by_user = Signal()

    WIDTH = 820
    HEIGHT = 178
    MARGIN_BOTTOM = 56
    LEVEL_HISTORY = 48

    def __init__(self, auto_hide_seconds: float = 5.0, acrylic: bool = True):
        super().__init__()
        self.auto_hide_seconds = auto_hide_seconds
        self.acrylic_enabled = acrylic
        self.setWindowTitle("Jake HUD")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.resize(self.WIDTH, self.HEIGHT)

        self.state = "idle"
        self.title_text = STATE_LABELS["idle"]
        self.transcript = ""
        self.response = ""
        self._response_full = ""
        self._response_reveal_index = 0
        self.level = 0.0
        self._smoothed_level = 0.0
        self._levels = [0.0] * self.LEVEL_HISTORY
        self.phase = 0.0
        self.input_mode = False
        self._effects_applied = False
        self._visible_target = False
        self._hide_connected = False

        self.input = QLineEdit(self)
        self.input.setPlaceholderText("Scrivi un comando e premi Invio...  (Esc per chiudere)")
        self.input.setGeometry(214, 96, self.WIDTH - 214 - 26, 40)
        self.input.setFont(QFont("Segoe UI", 12))
        self.input.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,0.10); border: 1px solid rgba(160,210,255,0.45); "
            "border-radius: 12px; padding: 4px 14px; color: #EAF4FF; selection-background-color: rgba(90,170,255,0.6); }"
            "QLineEdit:focus { border: 1px solid rgba(120,200,255,0.9); background: rgba(255,255,255,0.14); }"
        )
        self.input.returnPressed.connect(self._on_submit)
        self.input.hide()

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide_hud)
        self._reveal_timer = QTimer(self)
        self._reveal_timer.timeout.connect(self._reveal_step)

        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._opacity_anim.setDuration(220)
        self._opacity_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._pos_anim = QPropertyAnimation(self, b"pos", self)
        self._pos_anim.setDuration(260)
        self._pos_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.setWindowOpacity(0.0)

    # ---- posizionamento / effetti ------------------------------------------------------

    def _target_position(self) -> QPoint:
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.center().x() - self.WIDTH // 2
        y = screen.bottom() - self.HEIGHT - self.MARGIN_BOTTOM
        return QPoint(x, y)

    def _apply_native_effects(self) -> None:
        hwnd = int(self.winId())
        win_effects.set_rounded_corners(hwnd)
        if self.acrylic_enabled:
            win_effects.apply_acrylic(hwnd, tint=(14, 28, 64, 150))
        win_effects.set_no_activate(hwnd, not self.input_mode)
        win_effects.set_click_through(hwnd, not self.input_mode)
        self._effects_applied = True

    # ---- API ---------------------------------------------------------------------------

    def show_hud(self, input_mode: bool = False) -> None:
        self._hide_timer.stop()
        was_visible = self.isVisible() and self._visible_target
        self.input_mode = input_mode
        self._visible_target = True
        target = self._target_position()
        if not was_visible:
            self.move(target + QPoint(0, 26))
            self.setWindowOpacity(0.0)
            self.show()
            self._pos_anim.stop()
            self._pos_anim.setStartValue(self.pos())
            self._pos_anim.setEndValue(target)
            self._pos_anim.start()
            self._opacity_anim.stop()
            self._opacity_anim.setStartValue(0.0)
            self._opacity_anim.setEndValue(1.0)
            self._opacity_anim.start()
        self._apply_native_effects()
        if input_mode:
            self.set_state("input", "")
            self.input.show()
            self.input.clear()
            self.raise_()
            self.activateWindow()
            self.input.setFocus()
        else:
            self.input.hide()
        if not self._anim_timer.isActive():
            self._anim_timer.start(16)
        self.update()

    def hide_hud(self) -> None:
        if not self.isVisible():
            return
        self._visible_target = False
        self.input.hide()
        self.input_mode = False
        self._opacity_anim.stop()
        self._opacity_anim.setStartValue(self.windowOpacity())
        self._opacity_anim.setEndValue(0.0)
        if not self._hide_connected:
            self._opacity_anim.finished.connect(self._finish_hide)
            self._hide_connected = True
        self._opacity_anim.start()

    def _finish_hide(self) -> None:
        # Collegato una volta sola: la dissolvenza in entrata finisce con opacita' 1 e non
        # deve nascondere nulla, quindi qui si controlla il bersaglio, non si scollega.
        if self.windowOpacity() > 0.01:
            return
        if not self._visible_target:
            self.hide()
            self._anim_timer.stop()
            self.transcript = ""
            self.response = ""
            self._response_full = ""

    def schedule_hide(self, seconds: float = None) -> None:
        if self.input_mode:
            return
        delay = self.auto_hide_seconds if seconds is None else seconds
        self._hide_timer.start(int(max(0.5, delay) * 1000))

    def set_state(self, state: str, detail: str = "") -> None:
        self.state = state if state in STATE_LABELS else "idle"
        self.title_text = STATE_LABELS[self.state]
        if state == "notify" and detail:
            self.set_response(detail)
        if state == "error" and detail:
            self.set_response(detail)
        self.update()

    def set_transcript(self, text: str) -> None:
        self.transcript = text or ""
        self.update()

    def set_response(self, text: str) -> None:
        self._response_full = text or ""
        self._response_reveal_index = 0
        self.response = ""
        self._reveal_timer.start(12)

    def set_level(self, level: float, is_speech: bool = False) -> None:
        self.level = max(0.0, min(1.0, level))

    # ---- interni -----------------------------------------------------------------------

    def _on_submit(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.set_transcript(text)
        self.set_state("thinking", text)
        self.submitted.emit(text)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide_hud()
            self.closed_by_user.emit()
            return
        super().keyPressEvent(event)

    def _reveal_step(self) -> None:
        if self._response_reveal_index >= len(self._response_full):
            self._reveal_timer.stop()
            self.response = self._response_full
        else:
            self._response_reveal_index = min(len(self._response_full), self._response_reveal_index + 3)
            self.response = self._response_full[: self._response_reveal_index]
        self.update()

    def _tick(self) -> None:
        self.phase += 0.05
        target = self.level if self.state in ("listening", "transcribing", "dictation", "input") else 0.0
        if self.state in ("speaking", "responding"):
            target = 0.35 + 0.35 * abs(math.sin(self.phase * 2.3)) * random.uniform(0.6, 1.0)
        self._smoothed_level += (target - self._smoothed_level) * (0.35 if target > self._smoothed_level else 0.12)
        self._levels.append(self._smoothed_level)
        del self._levels[0]
        self.update()

    # ---- disegno -----------------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        width, height = self.width(), self.height()
        accent = STATE_COLORS.get(self.state, STATE_COLORS["idle"])

        # pannello di vetro
        panel = QRectF(1, 1, width - 2, height - 2)
        gradient = QLinearGradient(0, 0, width, height)
        gradient.setColorAt(0.0, QColor(70, 140, 255, 62))
        gradient.setColorAt(0.55, QColor(30, 60, 130, 58))
        gradient.setColorAt(1.0, QColor(10, 25, 70, 80))
        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(QColor(150, 205, 255, 120), 1.2))
        painter.drawRoundedRect(panel, 30, 30)
        # riflesso in alto
        highlight = QLinearGradient(0, 0, 0, 40)
        highlight.setColorAt(0.0, QColor(255, 255, 255, 46))
        highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(highlight))
        painter.drawRoundedRect(QRectF(2, 2, width - 4, 40), 28, 28)

        self._paint_orb(painter, accent, height)
        self._paint_waveform(painter, accent, height)
        self._paint_text(painter, accent, width, height)

    def _paint_orb(self, painter: QPainter, accent: QColor, height: int) -> None:
        cx, cy = 92.0, height / 2.0
        radius = 44.0
        pulse = 1.0 + 0.06 * math.sin(self.phase * 2.0) + 0.25 * self._smoothed_level
        # alone
        glow = QRadialGradient(cx, cy, radius * 2.1 * pulse)
        glow.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 150))
        glow.setColorAt(0.45, QColor(accent.red(), accent.green(), accent.blue(), 55))
        glow.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(QPointF(cx, cy), radius * 2.1 * pulse, radius * 2.1 * pulse)
        # nucleo
        core = QRadialGradient(cx - 12, cy - 14, radius * 1.1)
        core.setColorAt(0.0, QColor(235, 248, 255, 245))
        core.setColorAt(0.35, QColor(min(255, accent.red() + 40), min(255, accent.green() + 30), 255, 225))
        core.setColorAt(1.0, QColor(20, 60, 170, 210))
        painter.setBrush(QBrush(core))
        painter.drawEllipse(QPointF(cx, cy), radius * pulse, radius * pulse)
        # anelli orbitanti
        painter.setBrush(Qt.NoBrush)
        ring_count = 3
        speed = 3.0 if self.state == "thinking" else 1.0
        for index in range(ring_count):
            angle = self.phase * speed + index * 2.1
            ring_radius = radius * (1.28 + 0.13 * index) * (1 + 0.04 * math.sin(angle * 1.7))
            alpha = 120 - index * 30
            painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), alpha), 2.0))
            span = 100 * 16 if self.state != "thinking" else 60 * 16
            painter.drawArc(QRectF(cx - ring_radius, cy - ring_radius, 2 * ring_radius, 2 * ring_radius), int(angle * 600) % 5760, span)
        if self.state == "paused":
            painter.setPen(QPen(QColor(230, 235, 245, 200), 4))
            painter.drawLine(QPointF(cx - 8, cy - 12), QPointF(cx - 8, cy + 12))
            painter.drawLine(QPointF(cx + 8, cy - 12), QPointF(cx + 8, cy + 12))

    def _paint_waveform(self, painter: QPainter, accent: QColor, height: int) -> None:
        if self.input_mode:
            return
        cy = height / 2.0
        x0 = 176
        bar_width = 4
        gap = 3
        painter.setPen(Qt.NoPen)
        for index, level in enumerate(self._levels[-int(190 / (bar_width + gap)):]):
            wobble = 0.5 + 0.5 * abs(math.sin(self.phase * 2.5 + index * 0.45))
            amplitude = 3 + 42 * level * wobble
            alpha = 90 + int(140 * min(1.0, level * 2))
            painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), alpha))
            x = x0 + index * (bar_width + gap)
            painter.drawRoundedRect(QRectF(x, cy - amplitude, bar_width, amplitude * 2), 2, 2)

    def _paint_text(self, painter: QPainter, accent: QColor, width: int, height: int) -> None:
        left = 214 if self.input_mode else 396
        right_margin = 26
        text_width = width - left - right_margin
        painter.setPen(QColor(236, 246, 255, 240))
        painter.setFont(QFont("Segoe UI", 15, QFont.DemiBold))
        painter.drawText(QRectF(left, 26, text_width, 32), Qt.AlignLeft | Qt.AlignVCenter, self.title_text)

        painter.setFont(QFont("Segoe UI", 10))
        painter.setPen(QColor(190, 215, 255, 205))
        if self.transcript and not self.input_mode:
            painter.drawText(QRectF(left, 58, text_width, 22), Qt.AlignLeft | Qt.AlignVCenter, f"«{self._elide(self.transcript, 78)}»")

        if not self.input_mode:
            painter.setFont(QFont("Segoe UI", 11))
            painter.setPen(QColor(228, 240, 255, 235))
            painter.drawText(QRectF(left, 82, text_width, 62), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self._elide(self.response, 220))
        elif self.response:
            painter.setFont(QFont("Segoe UI", 10))
            painter.setPen(QColor(228, 240, 255, 235))
            painter.drawText(QRectF(left, 58, text_width, 34), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self._elide(self.response, 160))

        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor(150, 195, 255, 150))
        painter.drawText(QRectF(left, height - 28, text_width, 18), Qt.AlignLeft | Qt.AlignVCenter,
                         "JAKE 3.0  ·  di' «Jake» oppure Ctrl+Shift+J  ·  " + time.strftime("%H:%M"))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 220))
        painter.drawEllipse(QPointF(width - 22, 22), 4, 4)

    @staticmethod
    def _elide(text: str, max_chars: int) -> str:
        text = (text or "").replace("\n", " ")
        return text if len(text) <= max_chars else text[: max_chars - 1] + "…"
