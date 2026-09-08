from core.skill_result import SkillResult
from skills.window_control import _find_window


class ListOpenWindowsSkill:
    metadata = {
        "intent": "LIST_OPEN_WINDOWS",
        "description": "Elenca i titoli di tutte le finestre attualmente aperte sul desktop.",
        "parameters": {},
    }

    MAX_RESULTS = 20

    def execute(self, parameters: dict = None):
        from core.vision.screen import list_open_window_titles

        titles = list_open_window_titles(self.MAX_RESULTS)
        if not titles:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"titles": titles})


class MaximizeWindowSkill:
    metadata = {
        "intent": "MAXIMIZE_WINDOW",
        "description": "Massimizza una finestra, dato un testo (anche parziale) del suo titolo.",
        "parameters": {
            "title": {"type": "string", "required": True, "description": "Testo (anche parziale) del titolo della finestra."},
        },
    }

    def execute(self, parameters: dict = None):
        import win32con
        import win32gui

        parameters = parameters or {}
        title = (parameters.get("title") or "").strip()
        if not title:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        match = _find_window(title)
        if match is None:
            return SkillResult(success=False, data={"title": title}, error="WINDOW_NOT_FOUND")

        hwnd, real_title = match
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        return SkillResult(success=True, data={"title": real_title})


class RestoreWindowSkill:
    metadata = {
        "intent": "RESTORE_WINDOW",
        "description": "Ripristina (rimpicciolisce dallo stato massimizzato) una finestra, dato un testo del suo titolo.",
        "parameters": {
            "title": {"type": "string", "required": True, "description": "Testo (anche parziale) del titolo della finestra."},
        },
    }

    def execute(self, parameters: dict = None):
        import win32con
        import win32gui

        parameters = parameters or {}
        title = (parameters.get("title") or "").strip()
        if not title:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        match = _find_window(title)
        if match is None:
            return SkillResult(success=False, data={"title": title}, error="WINDOW_NOT_FOUND")

        hwnd, real_title = match
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        return SkillResult(success=True, data={"title": real_title})


class _SnapWindowSkill:
    """Ancora la finestra attiva a meta' schermo (sinistra o destra), simulando la scorciatoia
    nativa di Windows (Win+Freccia): piu' affidabile che calcolare a mano le coordinate per
    ogni possibile risoluzione/monitor."""

    def _snap(self, key_combo: str):
        import keyboard

        keyboard.send(key_combo)
        return SkillResult(success=True, data={})


class SnapWindowLeftSkill(_SnapWindowSkill):
    metadata = {
        "intent": "SNAP_WINDOW_LEFT",
        "description": "Affianca la finestra attiva alla meta' sinistra dello schermo.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        return self._snap("windows+left")


class SnapWindowRightSkill(_SnapWindowSkill):
    metadata = {
        "intent": "SNAP_WINDOW_RIGHT",
        "description": "Affianca la finestra attiva alla meta' destra dello schermo.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        return self._snap("windows+right")


class SwitchNextWindowSkill:
    metadata = {
        "intent": "SWITCH_NEXT_WINDOW",
        "description": "Passa alla finestra aperta successiva (come Alt+Tab).",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        import keyboard

        keyboard.send("alt+tab")
        return SkillResult(success=True, data={})


class MinimizeAllWindowsSkill:
    metadata = {
        "intent": "MINIMIZE_ALL_WINDOWS",
        "description": "Minimizza tutte le finestre e mostra il desktop.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        import keyboard

        keyboard.send("windows+d")
        return SkillResult(success=True, data={})


class SetWindowAlwaysOnTopSkill:
    metadata = {
        "intent": "SET_WINDOW_ALWAYS_ON_TOP",
        "description": "Mette una finestra sempre in primo piano rispetto alle altre, dato un testo del suo titolo.",
        "parameters": {
            "title": {"type": "string", "required": True, "description": "Testo (anche parziale) del titolo della finestra."},
        },
    }

    def execute(self, parameters: dict = None):
        import win32con
        import win32gui

        parameters = parameters or {}
        title = (parameters.get("title") or "").strip()
        if not title:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        match = _find_window(title)
        if match is None:
            return SkillResult(success=False, data={"title": title}, error="WINDOW_NOT_FOUND")

        hwnd, real_title = match
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
        return SkillResult(success=True, data={"title": real_title})


class ResizeWindowSkill:
    metadata = {
        "intent": "RESIZE_WINDOW",
        "description": "Ridimensiona una finestra a una larghezza/altezza specifica, dato un testo del suo titolo.",
        "parameters": {
            "title": {"type": "string", "required": True, "description": "Testo (anche parziale) del titolo della finestra."},
            "width": {"type": "integer", "required": True, "description": "Larghezza desiderata in pixel."},
            "height": {"type": "integer", "required": True, "description": "Altezza desiderata in pixel."},
        },
    }

    def execute(self, parameters: dict = None):
        import win32con
        import win32gui

        parameters = parameters or {}
        title = (parameters.get("title") or "").strip()
        width = parameters.get("width")
        height = parameters.get("height")
        if not title or not isinstance(width, int) or not isinstance(height, int):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        match = _find_window(title)
        if match is None:
            return SkillResult(success=False, data={"title": title}, error="WINDOW_NOT_FOUND")

        hwnd, real_title = match
        win32gui.SetWindowPos(hwnd, None, 0, 0, width, height, win32con.SWP_NOMOVE | win32con.SWP_NOZORDER)
        return SkillResult(success=True, data={"title": real_title, "width": width, "height": height})
