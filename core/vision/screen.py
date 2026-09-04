import asyncio
from datetime import datetime
from pathlib import Path

SCREENSHOTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "screenshots"


def capture_screenshot() -> Path:
    """Cattura lo schermo intero e salva un PNG con timestamp. Restituisce il percorso."""
    from PIL import ImageGrab

    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOTS_DIR / f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    image = ImageGrab.grab()
    image.save(path)
    return path


def read_screen_text(image_path: Path = None) -> str | None:
    """Estrae il testo visibile sullo schermo (o da un'immagine data) via OCR di Windows.
    None se nessun motore OCR e' disponibile per la lingua del profilo utente."""
    path = image_path or capture_screenshot()
    return asyncio.run(_ocr_async(path))


async def _ocr_async(path: Path) -> str | None:
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.storage import FileAccessMode, StorageFile

    file = await StorageFile.get_file_from_path_async(str(path))
    stream = await file.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return None
    result = await engine.recognize_async(bitmap)
    return result.text


def get_active_window_title() -> str | None:
    """Restituisce il titolo della finestra attualmente in primo piano."""
    import win32gui

    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None
    title = win32gui.GetWindowText(hwnd)
    return title or None
