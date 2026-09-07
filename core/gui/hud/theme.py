"""Tema dell'HUD (v3.1): un solo gradiente, un solo vetro, ovunque."""
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QLinearGradient

FONT_FAMILY = "Segoe UI"
RADIUS = 26

# Il gradiente "Jake": blu elettrico -> blu notte -> navy profondo, semitrasparente. Alpha
# bassi apposta: deve leggersi come vetro sfocato con dietro il desktop vero (che resta
# riconoscibile), non come un pannello blu pieno con solo un'ombra vaga dietro.
GRADIENT_STOPS = [
    (0.00, (75, 145, 255, 22)),
    (0.55, (35, 65, 135, 18)),
    (1.00, (12, 28, 75, 30)),
]
GLASS_TINT = QColor(10, 18, 44, 34)        # scurisce quel poco che serve per la leggibilita'
BORDER = QColor(155, 210, 255, 140)
BORDER_STRONG = QColor(170, 220, 255, 220)
BORDER_DIM = QColor(120, 170, 230, 26)     # base del bordo a gradiente: si spegne verso il fondo
SHADE = QColor(2, 6, 20, 70)               # ombra interna in basso a destra: profondita' del vetro curvo
HIGHLIGHT = QColor(255, 255, 255, 64)
SPECULAR = QColor(255, 255, 255, 110)      # riflesso lucido in alto a sinistra, come vetro curvo
GRAIN_OPACITY = 0.07                       # granulosita': vetro smerigliato, non plastica liscia
VEIL = QColor(2, 8, 28, 150)               # velo scuro per la modalita' immersiva

TEXT = QColor(236, 246, 255, 240)
TEXT_DIM = QColor(190, 215, 255, 205)
TEXT_FAINT = QColor(150, 195, 255, 150)
ACCENT = QColor(90, 170, 255)
OK_GREEN = QColor(120, 255, 190)
WARN = QColor(255, 200, 90)
DANGER = QColor(255, 110, 110)

STATE_LABELS = {
    "idle": "Jake · pronto",
    "listening": "Ti ascolto...",
    "transcribing": "Sto capendo...",
    "thinking": "Ci penso...",
    "working": "Ci lavoro...",
    "responding": "Jake",
    "speaking": "Jake",
    "dictation": "Dettatura attiva",
    "paused": "In pausa",
    "notify": "Avviso",
    "error": "Problema",
    "input": "Dimmi cosa fare",
}

STATE_COLORS = {
    "idle": QColor(90, 160, 255),
    "listening": QColor(80, 220, 255),
    "transcribing": QColor(120, 200, 255),
    "thinking": QColor(160, 140, 255),
    "working": QColor(140, 160, 255),
    "responding": QColor(100, 190, 255),
    "speaking": QColor(100, 190, 255),
    "dictation": QColor(120, 255, 190),
    "paused": QColor(150, 160, 190),
    "notify": QColor(255, 200, 90),
    "error": QColor(255, 110, 110),
    "input": QColor(90, 170, 255),
}


def state_color(state: str) -> QColor:
    return STATE_COLORS.get(state, STATE_COLORS["idle"])


def state_label(state: str) -> str:
    return STATE_LABELS.get(state, STATE_LABELS["idle"])


def gradient(rect: QRectF) -> QLinearGradient:
    grad = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
    for position, (r, g, b, a) in GRADIENT_STOPS:
        grad.setColorAt(position, QColor(r, g, b, a))
    return grad


def font(size: int, weight=QFont.Normal) -> QFont:
    return QFont(FONT_FAMILY, size, weight)


def rgba(color: QColor, alpha: int = None) -> str:
    return f"rgba({color.red()},{color.green()},{color.blue()},{(alpha if alpha is not None else color.alpha()) / 255:.2f})"


# Stile dei widget standard dentro il vetro: sfondi trasparenti, bordi luminosi, niente
# grigio Windows.
STYLESHEET = f"""
QWidget {{ color: {rgba(TEXT)}; font-family: '{FONT_FAMILY}'; font-size: 11pt; background: transparent; }}
QLabel {{ background: transparent; }}
QLineEdit {{
    background: rgba(255,255,255,0.09); border: 1px solid {rgba(BORDER, 110)}; border-radius: 14px;
    padding: 6px 16px; color: {rgba(TEXT)}; font-size: 13pt; selection-background-color: rgba(90,170,255,0.55);
}}
QLineEdit:focus {{ border: 1px solid {rgba(BORDER_STRONG)}; background: rgba(255,255,255,0.13); }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ padding: 6px 10px; border-radius: 10px; color: {rgba(TEXT)}; }}
QListWidget::item:hover {{ background: rgba(120,190,255,0.14); }}
QListWidget::item:selected {{ background: rgba(120,190,255,0.22); color: {rgba(TEXT)}; }}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 4px 0; }}
QScrollBar::handle:vertical {{ background: rgba(150,205,255,0.35); border-radius: 3px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QPushButton#chip {{
    background: rgba(120,190,255,0.12); border: 1px solid {rgba(BORDER, 90)}; border-radius: 16px;
    padding: 6px 16px; color: {rgba(TEXT)}; font-size: 10.5pt;
}}
QPushButton#chip:hover {{ background: rgba(120,190,255,0.24); border: 1px solid {rgba(BORDER_STRONG)}; }}
QPushButton#chip:pressed {{ background: rgba(120,190,255,0.34); }}
QPushButton#icon {{
    background: rgba(120,190,255,0.12); border: 1px solid {rgba(BORDER, 90)}; border-radius: 18px;
    min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px; font-size: 13pt; color: {rgba(TEXT)};
}}
QPushButton#icon:hover {{ background: rgba(120,190,255,0.26); }}
QPushButton#icon:checked {{ background: rgba(90,170,255,0.45); border: 1px solid {rgba(BORDER_STRONG)}; }}
QToolTip {{ background: rgb(16,28,60); color: {rgba(TEXT)}; border: 1px solid {rgba(BORDER)}; padding: 4px; }}
"""
