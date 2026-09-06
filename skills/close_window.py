"""Chiusura "gentile" di una finestra (v3.0): WM_CLOSE, come cliccare la X. Diverso da
CLOSE_APP, che termina il processo (e chiede conferma perche' non lascia salvare)."""
from core.skill_result import SkillResult
from skills.window_control import _find_window


class CloseWindowSkill:
    metadata = {
        "intent": "CLOSE_WINDOW",
        "description": "Chiude la finestra attiva (o quella con il titolo indicato) come premendo la X: "
        "il programma puo' chiedere di salvare. Usalo per 'chiudi questa finestra', 'chiudi la finestra "
        "di X'. Diverso da CLOSE_APP, che uccide il processo.",
        "parameters": {
            "title": {"type": "string", "required": False, "description": "Testo (anche parziale) del titolo della finestra. Se omesso, la finestra attiva."},
        },
    }

    def execute(self, parameters: dict = None):
        import win32con
        import win32gui

        parameters = parameters or {}
        title_fragment = (parameters.get("title") or "").strip()
        if title_fragment:
            match = _find_window(title_fragment)
            if match is None:
                return SkillResult(success=False, data={"title": title_fragment}, error="WINDOW_NOT_FOUND")
            hwnd, real_title = match
        else:
            hwnd = win32gui.GetForegroundWindow()
            real_title = win32gui.GetWindowText(hwnd) if hwnd else ""
            if not hwnd or not real_title or real_title.lower().startswith("jake"):
                return SkillResult(success=False, data={"title": ""}, error="WINDOW_NOT_FOUND")

        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            return SkillResult(success=False, data={"title": real_title}, error="OPERATION_FAILED")
        return SkillResult(success=True, data={"title": real_title})
