from core.skill_result import SkillResult


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
