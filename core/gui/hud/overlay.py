"""HUD a schermo intero (v3.1): una finestra trasparente grande quanto lo schermo, sempre in
primo piano, con pannelli di vetro cliccabili (conversazione, contesto, azioni rapide, barra
comandi, top bar) intorno all'orb centrale. Fuori dai pannelli la finestra "non c'e'"
(maschera): il desktop resta visibile e cliccabile.

Modalita' (config hud_mode): "full" (tutti i pannelli) o "compact" (solo la pillola in
basso). Sfondo (hud_backdrop): "clear" (desktop nitido fuori dai pannelli) o "immersive"
(tutto lo schermo sfocato e scurito, click fuori = chiudi)."""
import time

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QRegion
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from core.gui.hud import theme, win_effects
from core.gui.hud.glass import Backdrop, GlassPanel
from core.gui.hud.widgets import (
    CenterStage, CommandBar, ContextPanel, ConversationPanel, OrbWidget, QuickActionsPanel, TopBar, TypewriterLabel,
    WaveformWidget,
)


class PillPanel(GlassPanel):
    """Layout compatto: orb piccolo + forma d'onda + testi, in una pillola in basso."""

    def __init__(self, backdrop, parent=None):
        super().__init__(backdrop, parent, radius=30)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 10, 24, 10)
        layout.setSpacing(10)
        self.orb = OrbWidget(84, self)
        layout.addWidget(self.orb, 0, Qt.AlignVCenter)
        self.waveform = WaveformWidget(self, height=60)
        self.waveform.setFixedWidth(170)
        layout.addWidget(self.waveform, 0, Qt.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title = QLabel(theme.state_label("idle"))
        self.title.setFont(theme.font(15, theme.QFont.DemiBold))
        texts.addWidget(self.title)
        self.transcript = QLabel("")
        self.transcript.setFont(theme.font(10))
        self.transcript.setStyleSheet(f"color: {theme.rgba(theme.TEXT_DIM)};")
        texts.addWidget(self.transcript)
        self.response = TypewriterLabel(self)
        self.response.setFont(theme.font(11))
        self.response.setMaximumHeight(52)
        texts.addWidget(self.response)
        self.footer = QLabel("")
        self.footer.setFont(theme.font(8))
        self.footer.setStyleSheet(f"color: {theme.rgba(theme.TEXT_FAINT)};")
        texts.addWidget(self.footer)
        layout.addLayout(texts, 1)

    def start(self):
        self.orb.start()
        self.waveform.start()

    def stop(self):
        self.orb.stop()
        self.waveform.stop()


class HudOverlay(QWidget):
    submitted = Signal(str)            # testo dalla barra comandi
    command_requested = Signal(str)    # click su chip / voce del contesto / frase precedente
    mic_toggled = Signal(bool)
    orb_clicked = Signal()
    closed_by_user = Signal()

    MARGIN = 36
    SIDE_WIDTH = 440
    HIDE_AFTER_RESPONSE = 6.0

    def __init__(self, mode: str = "full", backdrop_mode: str = "clear", auto_hide_seconds: float = 6.0,
                 hotkey_label: str = "Ctrl+Shift+J", snapshot_provider=None):
        super().__init__()
        self.mode = "compact" if mode == "compact" else "full"
        self.backdrop_mode = "immersive" if backdrop_mode == "immersive" else "clear"
        self.auto_hide_seconds = auto_hide_seconds
        self.snapshot_provider = snapshot_provider
        self.state = "idle"
        self.pinned = False
        self.input_mode = False
        self._visible_target = False
        self._hide_connected = False
        self._last_shown_at = 0.0

        self.setWindowTitle("Jake HUD")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet(theme.STYLESHEET)
        self.setGeometry(QApplication.primaryScreen().geometry())

        self.backdrop = Backdrop()
        self.top_bar = TopBar(self.backdrop, self)
        self.conversation = ConversationPanel(self.backdrop, self)
        self.context_panel = ContextPanel(self.backdrop, self)
        self.stage = CenterStage(self.backdrop, self)
        self.quick_actions = QuickActionsPanel(self.backdrop, self)
        self.command_bar = CommandBar(self.backdrop, self, hotkey_label=hotkey_label)
        self.pill = PillPanel(self.backdrop, self)
        self.pill.footer.setText(f"JAKE 3.0  ·  di' «Jake» oppure {hotkey_label}")

        self.top_bar.close_requested.connect(self._close_by_user)
        self.top_bar.pin_toggled.connect(self._set_pinned)
        self.conversation.command_requested.connect(self.command_requested.emit)
        self.context_panel.command_requested.connect(self.command_requested.emit)
        self.quick_actions.command_requested.connect(self.command_requested.emit)
        self.command_bar.submitted.connect(self._on_submit)
        self.command_bar.mic_toggled.connect(self.mic_toggled.emit)
        self.stage.orb_clicked.connect(self.orb_clicked.emit)
        self.pill.orb.clicked.connect(self.orb_clicked.emit)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._auto_hide)
        self._snapshot_timer = QTimer(self)
        self._snapshot_timer.timeout.connect(self.refresh_snapshot)
        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._opacity_anim.setDuration(240)
        self._opacity_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.setWindowOpacity(0.0)
        self._layout_panels()
        self._apply_mode_visibility()

    # ---- layout --------------------------------------------------------------------------

    def _layout_panels(self) -> None:
        width, height = self.width(), self.height()
        margin = self.MARGIN
        side = min(self.SIDE_WIDTH, int(width * 0.27))
        bar_height = 56
        command_height = 68
        actions_height = 112
        self.top_bar.setGeometry(QRect(margin, 22, width - 2 * margin, bar_height))
        column_top = 22 + bar_height + 18
        command_top = height - margin - command_height - 34
        actions_top = command_top - 16 - actions_height
        column_height = actions_top - 18 - column_top
        self.conversation.setGeometry(QRect(margin, column_top, side, column_height))
        self.context_panel.setGeometry(QRect(width - margin - side, column_top, side, column_height))
        stage_left = margin + side + 30
        self.stage.setGeometry(QRect(stage_left, column_top, width - 2 * (margin + side + 30), column_height))
        self.quick_actions.setGeometry(QRect(margin, actions_top, width - 2 * margin, actions_height))
        command_width = min(1040, width - 2 * margin)
        self.command_bar.setGeometry(QRect((width - command_width) // 2, command_top, command_width, command_height))
        pill_width, pill_height = min(880, width - 2 * margin), 150
        self.pill.setGeometry(QRect((width - pill_width) // 2, height - pill_height - 60, pill_width, pill_height))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_panels()
        self._update_mask()

    def _full_panels(self) -> list[QWidget]:
        return [self.top_bar, self.conversation, self.context_panel, self.quick_actions, self.command_bar]

    def _apply_mode_visibility(self) -> None:
        full = self.mode == "full"
        for panel in self._full_panels():
            panel.setVisible(full)
        self.stage.setVisible(full)
        self.pill.setVisible(not full)
        self._update_mask()

    def _update_mask(self) -> None:
        """La sagoma della finestra (v3.1): un rettangolo arrotondato per ciascun pannello di
        vetro, stage centrale incluso (ora e' un pannello di vetro come gli altri). Fuori da
        questi rettangoli la finestra 'non c'e'': il desktop resta visibile e cliccabile."""
        if self.backdrop_mode == "immersive":
            self.clearMask()
            return
        region = QRegion()
        panels = self._full_panels() + [self.stage] if self.mode == "full" else [self.pill]
        for panel in panels:
            if not panel.isVisible():
                continue
            path = QPainterPath()
            path.addRoundedRect(QRectF(panel.geometry()), getattr(panel, "radius", theme.RADIUS), getattr(panel, "radius", theme.RADIUS))
            region = region.united(QRegion(path.toFillPolygon().toPolygon()))
        self.setMask(region)

    def set_mode(self, mode: str) -> None:
        self.mode = "compact" if mode == "compact" else "full"
        self._apply_mode_visibility()
        if self.isVisible():
            (self.stage if self.mode == "full" else self.pill).start()
            (self.pill if self.mode == "full" else self.stage).stop()

    # ---- mostra / nascondi ---------------------------------------------------------------

    def _apply_native_effects(self) -> None:
        hwnd = int(self.winId())
        win_effects.set_no_activate(hwnd, not self.input_mode)
        win_effects.set_click_through(hwnd, False)
        if self.backdrop_mode == "immersive":
            win_effects.apply_acrylic(hwnd, tint=(6, 14, 40, 170))

    def show_hud(self, input_mode: bool = False, mode: str = None) -> None:
        self._hide_timer.stop()
        if mode is not None and mode != self.mode:
            self.set_mode(mode)
        was_visible = self.isVisible() and self._visible_target
        self.input_mode = input_mode or (self.input_mode and was_visible)
        self._visible_target = True
        if not was_visible:
            self.backdrop.refresh()  # con la finestra ancora nascosta: cosi' non sfoca se stessa
            self.setWindowOpacity(0.0)
            self.setGeometry(QApplication.primaryScreen().geometry())
            self.show()
            self._opacity_anim.stop()
            self._opacity_anim.setStartValue(0.0)
            self._opacity_anim.setEndValue(1.0)
            self._opacity_anim.start()
            (self.stage if self.mode == "full" else self.pill).start()
            self._snapshot_timer.start(2000)
            self.refresh_snapshot()
            self._last_shown_at = time.time()
        self._apply_native_effects()
        self._update_mask()
        if input_mode:
            self.raise_()
            self.activateWindow()
            self.command_bar.input.setFocus()
        self.update()

    def hide_hud(self) -> None:
        if not self.isVisible():
            return
        self._visible_target = False
        self.input_mode = False
        self._hide_timer.stop()
        self._snapshot_timer.stop()
        self._opacity_anim.stop()
        self._opacity_anim.setStartValue(self.windowOpacity())
        self._opacity_anim.setEndValue(0.0)
        if not self._hide_connected:
            self._opacity_anim.finished.connect(self._finish_hide)
            self._hide_connected = True
        self._opacity_anim.start()

    def _finish_hide(self) -> None:
        if self.windowOpacity() > 0.01 or self._visible_target:
            return
        self.hide()
        self.stage.stop()
        self.pill.stop()
        self.stage.set_transcript("")
        self.stage.set_response("")
        self.stage.clear_steps()
        self.pill.transcript.setText("")
        self.pill.response.setText("")

    def _close_by_user(self) -> None:
        self.hide_hud()
        self.closed_by_user.emit()

    def _set_pinned(self, pinned: bool) -> None:
        self.pinned = pinned
        if pinned:
            self._hide_timer.stop()

    def schedule_hide(self, seconds: float = None) -> None:
        if self.pinned or self.input_mode:
            return
        delay = self.auto_hide_seconds if seconds is None else seconds
        self._hide_timer.start(int(max(0.5, delay) * 1000))

    def _auto_hide(self) -> None:
        if self.pinned or self.input_mode or self.underMouse() or self.command_bar.input.hasFocus():
            self._hide_timer.start(3000)  # l'utente ci sta lavorando: riprova piu' tardi
            return
        self.hide_hud()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._close_by_user()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        # In modalita' immersiva un click sul vuoto chiude l'HUD.
        if self.backdrop_mode == "immersive" and self.childAt(event.position().toPoint()) is None:
            self._close_by_user()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        if self.backdrop_mode != "immersive":
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.VEIL)

    # ---- stato e contenuti ---------------------------------------------------------------

    def set_state(self, state: str, detail: str = "") -> None:
        self.state = state if state in theme.STATE_LABELS else "idle"
        self.stage.set_state(self.state)
        self.pill.orb.set_state(self.state)
        self.pill.waveform.set_state(self.state)
        self.pill.title.setText(theme.state_label(self.state))
        self.top_bar.set_state(self.state)
        glow = theme.state_color(self.state) if self.state in ("listening", "dictation", "notify", "error") else None
        self.pill.set_glow(glow)
        self.command_bar.set_glow(glow)
        if state in ("notify", "error") and detail:
            self.set_response(detail)

    def set_level(self, level: float, is_speech: bool = False) -> None:
        self.stage.set_level(level)
        self.pill.orb.set_level(level)
        self.pill.waveform.set_level(level)

    def set_transcript(self, text: str) -> None:
        self.stage.set_transcript(text)
        self.pill.transcript.setText(f"«{text}»" if text else "")

    def set_response(self, text: str) -> None:
        self.stage.set_response(text)
        self.pill.response.set_text_animated(text)

    def add_turn(self, role: str, text: str) -> None:
        self.conversation.add_turn(role, text)

    def load_history(self, turns: list[dict]) -> None:
        self.conversation.load(turns)

    def clear_steps(self) -> None:
        self.stage.clear_steps()

    def add_step(self, text: str) -> None:
        self.stage.add_step(text)

    def set_quick_actions(self, actions: list[tuple[str, str, bool]]) -> None:
        self.quick_actions.set_actions(actions)

    def set_mic_enabled(self, enabled: bool) -> None:
        self.command_bar.mic.blockSignals(True)
        self.command_bar.mic.setChecked(enabled)
        self.command_bar.mic.blockSignals(False)

    def refresh_snapshot(self) -> None:
        if self.snapshot_provider is None or not self.isVisible() or self.mode != "full":
            return
        try:
            snapshot = self.snapshot_provider() or {}
        except Exception:
            return
        self.context_panel.update_snapshot(snapshot)

    def _on_submit(self, text: str) -> None:
        self.set_transcript(text)
        self.set_state("thinking")
        self.add_turn("user", text)
        self.submitted.emit(text)
