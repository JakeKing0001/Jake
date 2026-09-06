"""Vetro liquido (v3.1).

DWM sa sfocare solo l'INTERA finestra (SetWindowCompositionAttribute non rispetta la
maschera): per avere pannelli di vetro sparsi su uno schermo altrimenti nitido, il blur viene
calcolato da Jake: uno screenshot del desktop, ridotto e sfocato, viene dipinto sotto ogni
pannello nella posizione corrispondente. Il desktop resta visibile e cliccabile intorno."""
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame

from core.gui.hud import theme


class Backdrop:
    """Screenshot sfocato dello schermo primario, condiviso da tutti i pannelli."""

    def __init__(self, scale: int = 6, blur_radius: float = 7.0):
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
    """Pannello con sfondo di vetro: blur del desktop + tinta + gradiente + bordo luminoso.
    I widget figli hanno sfondo trasparente (vedi theme.STYLESHEET)."""

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
        path = self.rounded_path()
        painter.setClipPath(path)

        pixmap = self.backdrop.pixmap if self.backdrop is not None else None
        if pixmap is not None:
            origin = self.mapToGlobal(self.rect().topLeft())
            screen_origin = QApplication.primaryScreen().geometry().topLeft()
            painter.drawPixmap(QPointF(screen_origin.x() - origin.x(), screen_origin.y() - origin.y()), pixmap)
            painter.fillRect(self.rect(), theme.GLASS_TINT)
        else:
            painter.fillRect(self.rect(), QColor(8, 18, 48, 200))

        painter.fillRect(self.rect(), QBrush(theme.gradient(QRectF(self.rect()))))
        painter.setClipping(False)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(self.glow if self.glow is not None else theme.BORDER, 1.4 if self.glow is not None else 1.2))
        painter.drawPath(path)
        # riflesso in alto
        painter.setPen(QPen(theme.HIGHLIGHT, 1))
        painter.drawLine(QPointF(self.radius, 1.5), QPointF(self.width() - self.radius, 1.5))
