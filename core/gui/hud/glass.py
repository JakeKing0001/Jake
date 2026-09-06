"""Vetro liquido (v3.1).

DWM sa sfocare solo l'INTERA finestra (SetWindowCompositionAttribute non rispetta la
maschera): per avere pannelli di vetro sparsi su uno schermo altrimenti nitido, il blur viene
calcolato da Jake: uno screenshot del desktop, ridotto e sfocato, viene dipinto sotto ogni
pannello nella posizione corrispondente. Il desktop resta visibile e cliccabile intorno.

Oltre alla sfocatura, il vetro ha tre altri ingredienti che lo fanno leggere come vetro vero
(smerigliato, curvo, che cattura la luce) invece che come un pannello blu semitrasparente:
un velo di grana sottile (GRAIN_OPACITY), un riflesso lucido in alto a sinistra (SPECULAR,
come luce che colpisce una superficie curva) e un bordo a gradiente che si accende verso
l'alto e si spegne verso il basso, invece di un contorno a tinta unita uniforme."""
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QFrame

from core.gui.hud import theme

_grain_tile: QPixmap | None = None


def _grain_texture() -> QPixmap:
    """Piastrella di rumore in scala di grigi, generata una sola volta e riusata (come QBrush
    tramite QPainter.fillRect, che la ripete da sola): il 'granulo' del vetro smerigliato."""
    global _grain_tile
    if _grain_tile is not None:
        return _grain_tile
    try:
        import numpy as np

        size = 96
        rng = np.random.default_rng(20260907)
        noise = rng.integers(0, 255, size=(size, size), dtype=np.uint8)
        rgba = np.dstack([noise, noise, noise, np.full_like(noise, 255)])
        image = QImage(rgba.tobytes(), size, size, size * 4, QImage.Format_RGBA8888).copy()
        _grain_tile = QPixmap.fromImage(image)
    except Exception:
        _grain_tile = QPixmap(1, 1)
        _grain_tile.fill(QColor(128, 128, 128))
    return _grain_tile


class Backdrop:
    """Screenshot sfocato dello schermo primario, condiviso da tutti i pannelli."""

    def __init__(self, scale: int = 5, blur_radius: float = 9.0):
        self.scale = scale
        self.blur_radius = blur_radius
        self.pixmap: QPixmap | None = None
        self.captured_at = 0.0

    def refresh(self) -> bool:
        try:
            from PIL import ImageFilter, ImageGrab

            screen = QApplication.primaryScreen()
            geometry = screen.geometry()
            ratio = screen.devicePixelRatio()
            physical = (
                int(geometry.x() * ratio), int(geometry.y() * ratio),
                int((geometry.x() + geometry.width()) * ratio), int((geometry.y() + geometry.height()) * ratio),
            )
            image = ImageGrab.grab(bbox=physical, all_screens=True)
            small = image.resize((max(1, image.width // self.scale), max(1, image.height // self.scale)))
            small = small.filter(ImageFilter.GaussianBlur(self.blur_radius)).convert("RGBA")
            qimage = QImage(small.tobytes(), small.width, small.height, small.width * 4, QImage.Format_RGBA8888).copy()
            pixmap = QPixmap.fromImage(qimage).scaled(image.width, image.height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            pixmap.setDevicePixelRatio(ratio)
            self.pixmap = pixmap
            self.captured_at = time.time()
            return True
        except Exception:
            return False


class GlassPanel(QFrame):
    """Pannello con sfondo di vetro: blur del desktop + tinta + gradiente + grana + riflesso +
    bordo a gradiente. I widget figli hanno sfondo trasparente (vedi theme.STYLESHEET)."""

    def __init__(self, backdrop: Backdrop = None, parent=None, radius: int = theme.RADIUS, glow: QColor = None):
        super().__init__(parent)
        self.backdrop = backdrop
        self.radius = radius
        self.glow = glow
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def set_glow(self, color: QColor | None) -> None:
        self.glow = color
        self.update()

    def rounded_path(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), self.radius, self.radius)
        return path

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        path = self.rounded_path()
        painter.setClipPath(path)

        pixmap = self.backdrop.pixmap if self.backdrop is not None else None
        if pixmap is not None:
            origin = self.mapToGlobal(self.rect().topLeft())
            screen_origin = QApplication.primaryScreen().geometry().topLeft()
            painter.drawPixmap(QPointF(screen_origin.x() - origin.x(), screen_origin.y() - origin.y()), pixmap)
            painter.fillRect(self.rect(), theme.GLASS_TINT)
        else:
            painter.fillRect(self.rect(), QColor(8, 18, 48, 160))

        painter.fillRect(self.rect(), QBrush(theme.gradient(rect)))

        # Grana sottile: vetro smerigliato, non plastica liscia. QBrush(pixmap) si ripete da
        # solo su tutto il rettangolo (tiling nativo di Qt), quindi basta un fillRect.
        painter.setOpacity(theme.GRAIN_OPACITY)
        painter.fillRect(self.rect(), QBrush(_grain_texture()))
        painter.setOpacity(1.0)

        # Riflesso lucido in alto a sinistra: luce che colpisce una superficie di vetro curva.
        specular = QRadialGradient(rect.width() * 0.22, rect.height() * -0.15, rect.width() * 0.75)
        specular.setColorAt(0.0, theme.SPECULAR)
        specular.setColorAt(0.5, QColor(255, 255, 255, 22))
        specular.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(QBrush(specular))
        painter.setPen(Qt.NoPen)
        painter.drawRect(self.rect())

        # Ombra interna in basso a destra, speculare al riflesso: la luce che non arriva li'
        # e' quello che fa leggere il vetro come curvo, non come un piano piatto illuminato
        # uniformemente.
        shade = QRadialGradient(rect.width() * 0.85, rect.height() * 1.05, rect.width() * 0.8)
        shade.setColorAt(0.0, theme.SHADE)
        shade.setColorAt(0.55, QColor(2, 6, 20, 24))
        shade.setColorAt(1.0, QColor(2, 6, 20, 0))
        painter.setBrush(QBrush(shade))
        painter.drawRect(self.rect())

        painter.setClipping(False)

        # Bordo: se lo stato ha un colore (ascolto, avviso...) resta a tinta piena e ben
        # visibile; altrimenti un filo a gradiente che si accende in alto e si spegne in
        # basso, come il bordo di un oggetto di vetro che cattura la luce dall'alto.
        painter.setBrush(Qt.NoBrush)
        if self.glow is not None:
            painter.setPen(QPen(self.glow, 1.5))
        else:
            edge = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
            edge.setColorAt(0.0, theme.BORDER_STRONG)
            edge.setColorAt(0.5, theme.BORDER)
            edge.setColorAt(1.0, theme.BORDER_DIM)
            painter.setPen(QPen(QBrush(edge), 1.2))
        painter.drawPath(path)

        # Riga di luce in cima, piu' intensa al centro: il bordo superiore di un bicchiere
        # visto controluce.
        top_glow = QLinearGradient(self.radius, 0, self.width() - self.radius, 0)
        top_glow.setColorAt(0.0, QColor(255, 255, 255, 0))
        top_glow.setColorAt(0.5, theme.HIGHLIGHT)
        top_glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(QPen(QBrush(top_glow), 1.1))
        painter.drawLine(QPointF(self.radius, 1.4), QPointF(self.width() - self.radius, 1.4))
