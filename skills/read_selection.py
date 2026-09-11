"""Lettura ad alta voce del testo selezionato (v3.0): copia la selezione (Ctrl+C) e la
restituisce come risposta, che la sessione vocale legge con la voce di Jake."""
import time

from core.skill_result import SkillResult

MAX_CHARS = 2000


def _read_clipboard() -> str | None:
    import win32clipboard

    try:
        win32clipboard.OpenClipboard()
        try:
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return None


class ReadSelectionSkill:
    metadata = {
        "intent": "READ_SELECTION",
        "description": "Legge ad alta voce il testo attualmente selezionato/evidenziato sullo schermo "
        "(in qualsiasi programma). Usalo per 'leggi questo', 'leggimi il testo selezionato'. "
        "Diverso da READ_SCREEN (OCR di tutto lo schermo) e CLIPBOARD_READ (appunti gia' copiati).",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        import keyboard

        before = _read_clipboard()
        try:
            keyboard.send("ctrl+c")
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")
        time.sleep(0.35)
        text = _read_clipboard()
        # F1: buco reale corretto in questa sessione - la condizione originale aveva "and not
        # text.strip()" anche sul confronto con "before", quindi non scattava mai (era gia'
        # coperta dalla clausola precedente): se Ctrl+C non copiava nulla di nuovo (nessuna
        # selezione attiva), il testo VECCHIO gia' presente negli appunti veniva letto ad alta
        # voce come se fosse la selezione appena fatta, invece di restituire NO_SELECTION.
        if not text or not text.strip() or (before is not None and text == before):
            return SkillResult(success=False, data={}, error="NO_SELECTION")
        text = text.strip()
        truncated = len(text) > MAX_CHARS
        return SkillResult(success=True, data={"text": text[:MAX_CHARS], "truncated": truncated})
