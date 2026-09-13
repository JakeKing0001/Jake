import time

from core.skill_result import SkillResult


def _wait_until_window_closed(win32gui, hwnd, wait_seconds: float, poll_interval: float = 0.05) -> bool:
    """F1.3.2 ("prove forti per... finestre"): attende che una finestra a cui e' stato appena
    inviato WM_CLOSE sia DAVVERO sparita, invece di fidarsi che l'invio sia andato a buon fine
    solo perche' PostMessage non ha sollevato un'eccezione. PostMessage e' fire-and-forget: il
    programma destinatario puo' ignorare il messaggio, o mostrare un dialogo "salvare le
    modifiche?" che blocca la chiusura vera - in entrambi i casi la finestra resta aperta anche
    se la richiesta e' stata "inviata con successo". Condivisa tra CloseWindowSkill e
    CloseAppSkill (skills/close_window.py, skills/process_control.py): stesso identico buco,
    stessa correzione, un solo posto da mantenere invece di due copie che potrebbero divergere.
    win32gui e' passato dal chiamante (gia' importato li', mai una seconda import qui) cosi' i
    test possono continuare a mockarlo come modulo intero."""
    deadline = time.monotonic() + wait_seconds
    while True:
        if not win32gui.IsWindow(hwnd):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval)


def _find_window(title_fragment: str):
    import win32gui

    title_fragment = title_fragment.lower()
    matches = []

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title_fragment in title.lower():
                matches.append((hwnd, title))

    win32gui.EnumWindows(callback, None)
    return matches[0] if matches else None


class FocusWindowSkill:
    metadata = {
        "intent": "FOCUS_WINDOW",
        "description": "Porta in primo piano la finestra il cui titolo contiene il testo indicato.",
        "parameters": {
            "title": {
                "type": "string",
                "required": True,
                "description": "Testo (anche parziale) del titolo della finestra da attivare.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        title_fragment = (parameters.get("title") or "").strip()
        if not title_fragment:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        match = _find_window(title_fragment)
        if match is None:
            return SkillResult(success=False, data={"title": title_fragment}, error="WINDOW_NOT_FOUND")

        import win32con
        import win32gui

        hwnd, real_title = match
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            return SkillResult(success=False, data={"title": real_title}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"title": real_title})


class MinimizeWindowSkill:
    metadata = {
        "intent": "MINIMIZE_WINDOW",
        "description": "Minimizza la finestra il cui titolo contiene il testo indicato.",
        "parameters": {
            "title": {
                "type": "string",
                "required": True,
                "description": "Testo (anche parziale) del titolo della finestra da minimizzare.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        title_fragment = (parameters.get("title") or "").strip()
        if not title_fragment:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        match = _find_window(title_fragment)
        if match is None:
            return SkillResult(success=False, data={"title": title_fragment}, error="WINDOW_NOT_FOUND")

        import win32con
        import win32gui

        hwnd, real_title = match
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        except Exception:
            return SkillResult(success=False, data={"title": real_title}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"title": real_title})
