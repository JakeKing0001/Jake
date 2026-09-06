"""Componenti dell'HUD (v3.1): orb, forma d'onda, chip, conversazione, contesto, barra comandi."""
import math
import random
import time

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from core.gui.hud import theme
from core.gui.hud.glass import GlassPanel


class OrbWidget(QWidget):
    """Il cuore di Jake: sfera luminosa con anelli orbitanti, pulsa col microfono."""
    clicked = Signal()

    def __init__(self, diameter: int = 150, parent=None):
        super().__init__(parent)
        self.diameter = diameter
        self.setFixedSize(int(diameter * 1.55), int(diameter * 1.55))
        self.state = "idle"
        self.level = 0.0
        self._smoothed = 0.0
        self.phase = random.random() * 6.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Clicca per parlare con Jake")

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start(16)

    def stop(self) -> None:
        self._timer.stop()

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def set_level(self, level: float) -> None:
        self.level = max(0.0, min(1.0, level))

    def _tick(self) -> None:
        self.phase += 0.05
        target = self.level if self.state in ("listening", "transcribing", "dictation", "input") else 0.0
        if self.state in ("speaking", "responding"):
            target = 0.3 + 0.4 * abs(math.sin(self.phase * 2.3)) * random.uniform(0.6, 1.0)
        self._smoothed += (target - self._smoothed) * (0.35 if target > self._smoothed else 0.1)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        accent = theme.state_color(self.state)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        radius = self.diameter / 2.0 * 0.62
        pulse = 1.0 + 0.06 * math.sin(self.phase * 2.0) + 0.28 * self._smoothed

        glow = QRadialGradient(cx, cy, radius * 2.2 * pulse)
        glow.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 150))
        glow.setColorAt(0.45, QColor(accent.red(), accent.green(), accent.blue(), 55))
        glow.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(QPointF(cx, cy), radius * 2.2 * pulse, radius * 2.2 * pulse)

        core = QRadialGradient(cx - radius * 0.3, cy - radius * 0.35, radius * 1.1)
        core.setColorAt(0.0, QColor(235, 248, 255, 245))
        core.setColorAt(0.35, QColor(min(255, accent.red() + 40), min(255, accent.green() + 30), 255, 225))
        core.setColorAt(1.0, QColor(20, 60, 170, 210))
        painter.setBrush(QBrush(core))
        painter.drawEllipse(QPointF(cx, cy), radius * pulse, radius * pulse)

        painter.setBrush(Qt.NoBrush)
        speed = 3.0 if self.state in ("thinking", "working") else 1.0
        for index in range(3):
            angle = self.phase * speed + index * 2.1
            ring = radius * (1.3 + 0.14 * index) * (1 + 0.04 * math.sin(angle * 1.7))
            painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 120 - index * 30), 2.0))
            span = (100 if self.state not in ("thinking", "working") else 60) * 16
            painter.drawArc(QRectF(cx - ring, cy - ring, 2 * ring, 2 * ring), int(angle * 600) % 5760, span)
        if self.state == "paused":
            painter.setPen(QPen(QColor(230, 235, 245, 200), 4))
            painter.drawLine(QPointF(cx - 8, cy - 12), QPointF(cx - 8, cy + 12))
            painter.drawLine(QPointF(cx + 8, cy - 12), QPointF(cx + 8, cy + 12))


class WaveformWidget(QWidget):
    HISTORY = 40

    def __init__(self, parent=None, height: int = 70, preferred_width: int = 340):
        super().__init__(parent)
        self._preferred_width = preferred_width
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.state = "idle"
        self.level = 0.0
        self._smoothed = 0.0
        self._levels = [0.0] * self.HISTORY
        self.phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def sizeHint(self):
        # Senza questo, il layout (che allinea la barra con Qt.AlignHCenter invece di
        # stirarla) le assegna sizeHint()'s default, cioe' larghezza 0: la forma d'onda non
        # riceveva mai spazio e restava invisibile per intero.
        return QSize(self._preferred_width, self.minimumHeight())

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start(33)

    def stop(self) -> None:
        self._timer.stop()

    def set_state(self, state: str) -> None:
        self.state = state

    def set_level(self, level: float) -> None:
        self.level = max(0.0, min(1.0, level))

    def _tick(self) -> None:
        self.phase += 0.1
        target = self.level if self.state in ("listening", "transcribing", "dictation", "input") else 0.0
        if self.state in ("speaking", "responding"):
            target = 0.35 + 0.35 * abs(math.sin(self.phase * 1.6)) * random.uniform(0.6, 1.0)
        self._smoothed += (target - self._smoothed) * (0.4 if target > self._smoothed else 0.15)
        self._levels.append(self._smoothed)
        del self._levels[0]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        accent = theme.state_color(self.state)
        cy = self.height() / 2.0
        bar, gap = 4, 3
        count = max(1, min(self.HISTORY, self.width() // (bar + gap)))
        levels = self._levels[-count:]
        x0 = (self.width() - count * (bar + gap)) / 2.0
        painter.setPen(Qt.NoPen)
        for index, level in enumerate(levels):
            wobble = 0.5 + 0.5 * abs(math.sin(self.phase * 1.3 + index * 0.45))
            amplitude = 2 + (cy - 6) * level * wobble
            alpha = 80 + int(150 * min(1.0, level * 2))
            painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), alpha))
            painter.drawRoundedRect(QRectF(x0 + index * (bar + gap), cy - amplitude, bar, amplitude * 2), 2, 2)


class TypewriterLabel(QLabel):
    """Etichetta che rivela il testo poco a poco (la risposta 'scritta' in tempo reale)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self._full = ""
        self._index = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def set_text_animated(self, text: str) -> None:
        self._full = text or ""
        self._index = 0
        self.setText("")
        if self._full:
            self._timer.start(10)

    def _step(self) -> None:
        self._index = min(len(self._full), self._index + 3)
        self.setText(self._full[: self._index])
        if self._index >= len(self._full):
            self._timer.stop()


class FlowLayout(QLayout):
    """Layout a scorrimento (le chip vanno a capo da sole)."""

    def __init__(self, parent=None, margin: int = 0, spacing: int = 8):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._spacing = spacing
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect, test_only: bool) -> int:
        margins = self.contentsMargins()
        x, y = rect.x() + margins.left(), rect.y() + margins.top()
        line_height = 0
        right = rect.right() - margins.right()
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > right and line_height > 0:
                x = rect.x() + margins.left()
                y += line_height + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()

    def clear(self) -> None:
        while self._items:
            item = self._items.pop()
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()


class ChipButton(QPushButton):
    def __init__(self, label: str, command: str, parent=None, starred: bool = False):
        super().__init__(("★ " if starred else "") + label, parent)
        self.command = command
        self.setObjectName("chip")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(command)


class ConversationPanel(GlassPanel):
    """Cronologia della conversazione; un click su una frase dell'utente la ripete."""
    command_requested = Signal(str)

    def __init__(self, backdrop, parent=None):
        super().__init__(backdrop, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)
        title = QLabel("CONVERSAZIONE")
        title.setFont(theme.font(9, theme.QFont.DemiBold))
        title.setStyleSheet(f"color: {theme.rgba(theme.TEXT_FAINT)}; letter-spacing: 2px;")
        layout.addWidget(title)
        self.list = QListWidget(self)
        self.list.setWordWrap(True)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list, 1)
        hint = QLabel("Clicca una tua frase per ripeterla")
        hint.setFont(theme.font(8))
        hint.setStyleSheet(f"color: {theme.rgba(theme.TEXT_FAINT)};")
        layout.addWidget(hint)

    def load(self, turns: list[dict]) -> None:
        self.list.clear()
        for turn in turns:
            self.add_turn(turn.get("role", "user"), turn.get("text", ""), scroll=False)
        self.list.scrollToBottom()

    def add_turn(self, role: str, text: str, scroll: bool = True) -> None:
        if not text:
            return
        is_user = role == "user"
        item = QListWidgetItem(("Tu  ›  " if is_user else "Jake  ›  ") + text.replace("\n", " "))
        item.setData(Qt.UserRole, text if is_user else None)
        item.setForeground(theme.TEXT if is_user else theme.TEXT_DIM)
        if not is_user:
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
        self.list.addItem(item)
        while self.list.count() > 80:
            self.list.takeItem(0)
        if scroll:
            self.list.scrollToBottom()

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        text = item.data(Qt.UserRole)
        if text:
            self.command_requested.emit(text)


class ContextPanel(GlassPanel):
    """Cosa succede adesso: finestra attiva, timer e promemoria, cose da fare, appunti,
    sistema. Le voci sono cliccabili (annulla timer, completa attivita'...)."""
    command_requested = Signal(str)

    def __init__(self, backdrop, parent=None):
        super().__init__(backdrop, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)
        title = QLabel("ADESSO")
        title.setFont(theme.font(9, theme.QFont.DemiBold))
        title.setStyleSheet(f"color: {theme.rgba(theme.TEXT_FAINT)}; letter-spacing: 2px;")
        layout.addWidget(title)
        self.list = QListWidget(self)
        self.list.setWordWrap(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list, 1)
        self.stats = QLabel("")
        self.stats.setFont(theme.font(9))
        self.stats.setStyleSheet(f"color: {theme.rgba(theme.TEXT_DIM)};")
        layout.addWidget(self.stats)

    def _header(self, text: str) -> None:
        item = QListWidgetItem(text.upper())
        item.setFlags(Qt.NoItemFlags)
        item.setForeground(theme.TEXT_FAINT)
        item.setFont(theme.font(8, theme.QFont.DemiBold))
        self.list.addItem(item)

    def _entry(self, text: str, command: str = None, color: QColor = None) -> None:
        item = QListWidgetItem(("  " if command is None else "  ▸ ") + text)
        item.setData(Qt.UserRole, command)
        item.setForeground(color or theme.TEXT)
        if command is None:
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
        else:
            item.setToolTip(command)
        self.list.addItem(item)

    def update_snapshot(self, snapshot: dict) -> None:
        scroll = self.list.verticalScrollBar().value()
        self.list.clear()
        window = snapshot.get("window")
        self._header("Finestra attiva")
        self._entry(window[:70] if window else "—")

        timers = snapshot.get("timers") or []
        reminders = snapshot.get("reminders") or []
        self._header("Timer e promemoria")
        if not timers and not reminders:
            self._entry("Nessuno")
        for timer in timers:
            self._entry(f"⏱ {timer['label']}: {timer['remaining']}", f"annulla il timer {timer['label'] if timer['label'] != 'timer' else ''}".strip(), theme.OK_GREEN)
        for reminder in reminders[:4]:
            self._entry(f"{reminder['when']}  {reminder['text']}", f"cancella il promemoria {reminder['text']}", theme.WARN)

        todos = snapshot.get("todos") or []
        self._header("Da fare")
        if not todos:
            self._entry("Lista vuota")
        for todo in todos[:5]:
            self._entry(todo["text"], f"segna come fatto {todo['text']}")

        notes = snapshot.get("notes") or []
        if notes:
            self._header("Ultimi appunti")
            for note in notes[-3:]:
                self._entry(note[:80])

        learned = snapshot.get("learned") or []
        if learned:
            self._header("Comandi imparati")
            for phrase in learned[:5]:
                self._entry(f"«{phrase}»", phrase, theme.ACCENT)

        stats = []
        if snapshot.get("cpu") is not None:
            stats.append(f"CPU {snapshot['cpu']:.0f}%")
        if snapshot.get("ram") is not None:
            stats.append(f"RAM {snapshot['ram']:.0f}%")
        if snapshot.get("battery") is not None:
            plug = "⚡" if snapshot.get("plugged") else "🔋"
            stats.append(f"{plug} {snapshot['battery']:.0f}%")
        if snapshot.get("skills"):
            stats.append(f"{snapshot['skills']} capacità")
        self.stats.setText("   ·   ".join(stats))
        self.list.verticalScrollBar().setValue(scroll)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        command = item.data(Qt.UserRole)
        if command:
            self.command_requested.emit(command)


class QuickActionsPanel(GlassPanel):
    command_requested = Signal(str)

    def __init__(self, backdrop, parent=None):
        super().__init__(backdrop, parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 12, 20, 12)
        outer.setSpacing(6)
        title = QLabel("AZIONI RAPIDE")
        title.setFont(theme.font(9, theme.QFont.DemiBold))
        title.setStyleSheet(f"color: {theme.rgba(theme.TEXT_FAINT)}; letter-spacing: 2px;")
        outer.addWidget(title)
        self.flow_host = QWidget(self)
        self.flow = FlowLayout(self.flow_host, spacing=8)
        outer.addWidget(self.flow_host, 1)

    def set_actions(self, actions: list[tuple[str, str, bool]]) -> None:
        self.flow.clear()
        for label, command, starred in actions:
            chip = ChipButton(label, command, self.flow_host, starred=starred)
            chip.clicked.connect(lambda _checked=False, c=command: self.command_requested.emit(c))
            self.flow.addWidget(chip)
        self.flow_host.updateGeometry()


class CommandBar(GlassPanel):
    submitted = Signal(str)
    mic_toggled = Signal(bool)

    def __init__(self, backdrop, parent=None, hotkey_label: str = "Ctrl+Shift+J"):
        super().__init__(backdrop, parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(12)
        self.prompt = QLabel("›")
        self.prompt.setFont(theme.font(18, theme.QFont.DemiBold))
        self.prompt.setStyleSheet(f"color: {theme.rgba(theme.ACCENT)};")
        layout.addWidget(self.prompt)
        self.input = QLineEdit(self)
        self.input.setPlaceholderText(f"Dimmi cosa fare, oppure di' «Jake»   ·   {hotkey_label}   ·   Esc chiude")
        self.input.returnPressed.connect(self._submit)
        layout.addWidget(self.input, 1)
        self.mic = QPushButton("🎤")
        self.mic.setObjectName("icon")
        self.mic.setCheckable(True)
        self.mic.setChecked(True)
        self.mic.setToolTip("Ascolto vocale attivo/disattivo")
        self.mic.setCursor(Qt.PointingHandCursor)
        self.mic.toggled.connect(self.mic_toggled.emit)
        layout.addWidget(self.mic)

    def _submit(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.submitted.emit(text)


class TopBar(GlassPanel):
    close_requested = Signal()
    pin_toggled = Signal(bool)

    def __init__(self, backdrop, parent=None):
        super().__init__(backdrop, parent, radius=22)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 8, 14, 8)
        layout.setSpacing(14)
        self.title = QLabel("JAKE  3.0")
        self.title.setFont(theme.font(12, theme.QFont.Bold))
        self.title.setStyleSheet("letter-spacing: 4px;")
        layout.addWidget(self.title)
        self.status = QLabel("● pronto")
        self.status.setFont(theme.font(10))
        layout.addWidget(self.status)
        layout.addStretch(1)
        self.clock = QLabel("")
        self.clock.setFont(theme.font(12, theme.QFont.DemiBold))
        layout.addWidget(self.clock)
        layout.addStretch(1)
        self.pin = QPushButton("📌")
        self.pin.setObjectName("icon")
        self.pin.setCheckable(True)
        self.pin.setToolTip("Tieni aperto")
        self.pin.setCursor(Qt.PointingHandCursor)
        self.pin.toggled.connect(self.pin_toggled.emit)
        layout.addWidget(self.pin)
        self.close = QPushButton("✕")
        self.close.setObjectName("icon")
        self.close.setToolTip("Chiudi (Esc)")
        self.close.setCursor(Qt.PointingHandCursor)
        self.close.clicked.connect(self.close_requested.emit)
        layout.addWidget(self.close)
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick)
        self._clock_timer.start(1000)
        self._tick()

    def _tick(self) -> None:
        self.clock.setText(time.strftime("%H:%M  ·  %a %d %b").replace(".", ""))

    def set_state(self, state: str) -> None:
        color = theme.state_color(state)
        self.status.setText(f"● {theme.state_label(state)}")
        self.status.setStyleSheet(f"color: {theme.rgba(color, 230)};")


class CenterStage(GlassPanel):
    """Al centro: orb, forma d'onda, stato, cosa ha sentito, cosa risponde, passi in corso.

    E' un pannello di vetro come gli altri (stesso sfondo sfocato, stesso gradiente, stesso
    bordo): senza un proprio sfondo il riquadro centrale mostrava il desktop grezzo (non
    sfocato, perche' il buco di trasparenza lascia passare i pixel reali) dietro al testo di
    Jake, illeggibile su una finestra affollata. Con questo la lettura resta chiara ovunque."""
    orb_clicked = Signal()

    def __init__(self, backdrop=None, parent=None, orb_diameter: int = 150):
        super().__init__(backdrop, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(8)
        layout.addStretch(1)
        self.orb = OrbWidget(orb_diameter, self)
        self.orb.clicked.connect(self.orb_clicked.emit)
        layout.addWidget(self.orb, 0, Qt.AlignHCenter)
        self.waveform = WaveformWidget(self, height=64)
        self.waveform.setMaximumWidth(360)
        layout.addWidget(self.waveform, 0, Qt.AlignHCenter)
        self.title = QLabel(theme.state_label("idle"))
        self.title.setFont(theme.font(20, theme.QFont.DemiBold))
        self.title.setAlignment(Qt.AlignHCenter)
        self.title.setStyleSheet(f"color: {theme.rgba(theme.TEXT)};")
        layout.addWidget(self.title)
        self.transcript = QLabel("")
        self.transcript.setFont(theme.font(12))
        self.transcript.setAlignment(Qt.AlignHCenter)
        self.transcript.setWordWrap(True)
        self.transcript.setStyleSheet(f"color: {theme.rgba(theme.TEXT_DIM)};")
        layout.addWidget(self.transcript)
        self.response = TypewriterLabel(self)
        self.response.setFont(theme.font(13))
        self.response.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.response.setStyleSheet(f"color: {theme.rgba(theme.TEXT)};")
        self.response.setMinimumHeight(90)
        layout.addWidget(self.response)
        self.steps = QLabel("")
        self.steps.setFont(theme.font(10))
        self.steps.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.steps.setWordWrap(True)
        self.steps.setStyleSheet(f"color: {theme.rgba(theme.TEXT_FAINT)};")
        layout.addWidget(self.steps)
        layout.addStretch(2)
        self._step_lines = []

    def start(self) -> None:
        self.orb.start()
        self.waveform.start()

    def stop(self) -> None:
        self.orb.stop()
        self.waveform.stop()

    def set_state(self, state: str) -> None:
        self.orb.set_state(state)
        self.waveform.set_state(state)
        self.title.setText(theme.state_label(state))
        self.title.setStyleSheet(f"color: {theme.rgba(theme.TEXT)};" if state not in ("error", "notify") else f"color: {theme.rgba(theme.state_color(state), 240)};")

    def set_level(self, level: float) -> None:
        self.orb.set_level(level)
        self.waveform.set_level(level)

    def set_transcript(self, text: str) -> None:
        self.transcript.setText(f"«{text}»" if text else "")

    def set_response(self, text: str) -> None:
        self.response.set_text_animated(text or "")

    def clear_steps(self) -> None:
        self._step_lines = []
        self.steps.setText("")

    def add_step(self, text: str) -> None:
        self._step_lines.append(f"{len(self._step_lines) + 1}. {text}")
        self.steps.setText("\n".join(self._step_lines[-5:]))
