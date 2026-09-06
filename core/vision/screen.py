import asyncio
from datetime import datetime
from pathlib import Path

SCREENSHOTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "screenshots"


def capture_screenshot(save: bool = True) -> Path:
    """Cattura lo schermo intero e salva un PNG con timestamp. Restituisce il percorso."""
    from PIL import ImageGrab

    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOTS_DIR / f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    image = ImageGrab.grab()
    image.save(path)
    return path


def capture_screenshot_image():
    """Come capture_screenshot ma restituisce l'immagine PIL senza salvarla."""
    from PIL import ImageGrab

    return ImageGrab.grab()


def read_screen_text(image_path: Path = None) -> str | None:
    """Estrae il testo visibile sullo schermo (o da un'immagine data) via OCR di Windows.
    None se nessun motore OCR e' disponibile per la lingua del profilo utente."""
    path = image_path or capture_screenshot()
    result = asyncio.run(_ocr_async(path))
    return result.text if result is not None else None


def read_screen_words(image_path: Path = None) -> list[dict] | None:
    """OCR con coordinate (v3.0): ogni parola con il suo rettangolo in pixel dello schermo,
    raggruppata per riga. Serve a "clicca su Accedi". None se l'OCR non e' disponibile."""
    path = image_path or capture_screenshot()
    result = asyncio.run(_ocr_async(path))
    if result is None:
        return None
    lines = []
    for line_index, line in enumerate(result.lines):
        words = []
        for word in line.words:
            rect = word.bounding_rect
            words.append({
                "text": word.text, "line": line_index,
                "x": int(rect.x), "y": int(rect.y), "w": int(rect.width), "h": int(rect.height),
            })
        lines.extend(words)
    return lines


async def _ocr_async(path: Path):
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
    return await engine.recognize_async(bitmap)


def get_active_window_title() -> str | None:
    """Restituisce il titolo della finestra attualmente in primo piano."""
    import win32gui

    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None
    title = win32gui.GetWindowText(hwnd)
    return title or None
