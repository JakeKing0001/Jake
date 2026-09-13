"""Chiusura "gentile" di una finestra (v3.0): WM_CLOSE, come cliccare la X. Diverso da
CLOSE_APP, che termina il processo (e chiede conferma perche' non lascia salvare).

F1.3.2 ("prove forti per... finestre"): prima di questa correzione, `success=True` veniva
restituito subito dopo `PostMessage(WM_CLOSE)`, senza aspettare che la finestra fosse DAVVERO
sparita - stesso identico buco gia' trovato e corretto per i processi (KILL_PROCESS_BY_PORT,
skills/dev_tools.py; CLOSE_APP nel ramo "termina il processo", skills/process_control.py).
PostMessage e' fire-and-forget: il programma puo' ignorare il messaggio, o mostrare un dialogo
"salvare le modifiche?" che blocca la chiusura vera - in entrambi i casi la finestra resta
aperta anche se l'invio del messaggio e' andato a buon fine. Ora attende fino a
CLOSE_WAIT_SECONDS che la finestra sparisca per davvero prima di dichiarare successo."""
from core.skill_result import SkillResult
from skills.window_control import _find_window, _wait_until_window_closed


class CloseWindowSkill:
    CLOSE_WAIT_SECONDS = 3.0
    _POLL_INTERVAL_SECONDS = 0.05

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

        # data["hwnd"]: F1.3.2, permette a un futuro verificatore indipendente
        # (core/execution_safety.py::INTENT_SAFETY_REGISTRY) di ricontrollare lo stesso fatto,
        # stesso principio gia' usato per data["pid"] in KillProcessByPortSkill.
        if _wait_until_window_closed(win32gui, hwnd, self.CLOSE_WAIT_SECONDS, self._POLL_INTERVAL_SECONDS):
            return SkillResult(success=True, data={"title": real_title, "hwnd": hwnd})
        return SkillResult(success=False, data={"title": real_title, "hwnd": hwnd}, error="OPERATION_FAILED")
