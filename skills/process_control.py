from core.skill_result import SkillResult

# Nome parlato -> frammento del nome del processo. "chiudi blocco note" non trova nessun
# processo chiamato "blocco note": il processo e' notepad.exe.
PROCESS_ALIASES = {
    "blocco note": "notepad", "notepad": "notepad", "calcolatrice": "calculatorapp", "calcolatore": "calculatorapp",
    "esplora file": "explorer", "esplora risorse": "explorer", "visual studio code": "code", "vscode": "code",
    "vs code": "code", "chrome": "chrome", "google chrome": "chrome", "edge": "msedge", "microsoft edge": "msedge",
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook", "onenote": "onenote",
    "terminale": "windowsterminal", "windows terminal": "windowsterminal", "prompt dei comandi": "cmd", "cmd": "cmd",
    "powershell": "powershell", "paint": "mspaint", "impostazioni": "systemsettings", "task manager": "taskmgr",
    "gestione attivita": "taskmgr", "gestione attività": "taskmgr", "foto": "photos", "whatsapp": "whatsapp",
    "spotify": "spotify", "discord": "discord", "steam": "steam", "telegram": "telegram", "opera": "opera",
    "firefox": "firefox", "blender": "blender", "obs": "obs64", "vlc": "vlc", "teams": "ms-teams", "zoom": "zoom",
    "brave": "brave", "photoshop": "photoshop", "il browser": "msedge",
}


def _process_needle(name: str) -> str:
    lowered = (name or "").strip().lower()
    for suffix in (".exe",):
        if lowered.endswith(suffix):
            lowered = lowered[: -len(suffix)]
    return PROCESS_ALIASES.get(lowered, lowered)


# Sotto questa lunghezza il confronto per sottostringa (vedi _matching_processes) smette di
# essere un filtro sensato: "a" da solo corrisponde a ~150 processi su una macchina reale
# (quasi ogni nome eseguibile contiene la lettera "a"), incluse app con finestre visibili -
# riprodotto per davvero su questa macchina, con Opera/Impostazioni/Nahimic/l'overlay NVIDIA
# tra i risultati. Per CloseAppSkill questo non e' solo rumore: il ramo "chiusura gentile"
# (WM_CLOSE) sotto NON chiede mai conferma, quindi un filtro troppo corto avrebbe chiuso
# finestre reali dell'utente senza alcun avviso. Il piu' corto valore vero in PROCESS_ALIASES
# e' lungo 3 (es. "cmd", "vlc"), quindi questa soglia non esclude nessun alias curato.
MIN_MATCH_LENGTH = 3


def _matching_processes(needle: str, original: str):
    import psutil

    needles = {n for n in (needle, original.lower().replace(" ", "")) if len(n) >= MIN_MATCH_LENGTH}
    if not needles:
        return []
    matching = []
    for process in psutil.process_iter(["pid", "name"]):
        process_name = (process.info.get("name") or "").lower()
        if any(n in process_name for n in needles):
            matching.append(process)
    return matching


def _windows_of_pids(pids: set):
    """Finestre visibili con titolo appartenenti ai processi indicati: [(hwnd, titolo, pid)]."""
    import win32gui
    import win32process

    found = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        try:
            _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        if pid in pids:
            found.append((hwnd, title, pid))

    win32gui.EnumWindows(callback, None)
    return found


class ListProcessesSkill:
    metadata = {
        "intent": "LIST_PROCESSES",
        "description": "Elenca i processi in esecuzione che corrispondono a un nome (o tutti, se omesso).",
        "parameters": {
            "name": {
                "type": "string",
                "required": False,
                "description": "Nome (anche parziale) del processo da cercare. Se omesso, elenca i primi processi attivi.",
            },
        },
    }

    MAX_RESULTS = 15

    def execute(self, parameters: dict = None):
        import psutil

        parameters = parameters or {}
        name_filter = (parameters.get("name") or "").strip().lower()
        needle = _process_needle(name_filter) if name_filter else ""

        matches = []
        for process in psutil.process_iter(["pid", "name"]):
            process_name = process.info.get("name") or ""
            if not name_filter or needle in process_name.lower() or name_filter in process_name.lower():
                matches.append({"pid": process.info["pid"], "name": process_name})
                if len(matches) >= self.MAX_RESULTS:
                    break

        if not matches:
            return SkillResult(success=False, data={"name": name_filter}, error="NOT_FOUND")
        return SkillResult(success=True, data={"processes": matches})


class CloseAppSkill:
    """Chiude un'applicazione. Prima in modo gentile (v3.0: WM_CLOSE alle sue finestre, come
    cliccare la X, cosi' il programma puo' chiedere di salvare); solo se non ha finestre
    visibili termina i processi, e in quel caso chiede conferma (azione irreversibile).

    F1.3.2 ("prove forti per... processi"): stesso buco reale gia' trovato e corretto in
    skills/dev_tools.py::KillProcessByPortSkill - il ramo "termina il processo" dichiarava
    success=True subito dopo process.terminate(), senza aspettare che fosse davvero morto.

    F1.3.2 ("...finestre"): lo STESSO buco esisteva anche nel ramo "chiusura gentile" sotto -
    _close_windows()/_close_windows_by_title() inviavano WM_CLOSE e contavano una finestra come
    "chiusa" per il solo fatto che PostMessage non avesse sollevato un'eccezione, senza mai
    verificare che fosse davvero sparita (vedi skills/window_control.py::
    _wait_until_window_closed, condivisa con CloseWindowSkill)."""

    TERMINATE_WAIT_SECONDS = 3

    metadata = {
        "intent": "CLOSE_APP",
        "description": "Chiude un'applicazione in esecuzione dato il suo nome (es. 'chiudi spotify', "
        "'chiudi blocco note'): prima chiude le sue finestre, se serve termina il processo.",
        "parameters": {
            "name": {
                "type": "string",
                "required": True,
                "description": "Nome dell'applicazione o del processo da chiudere, come detto dall'utente.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        name_filter = (parameters.get("name") or "").strip().lower()
        if not name_filter:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        needle = _process_needle(name_filter)
        matching = _matching_processes(needle, name_filter)
        if not matching:
            return SkillResult(success=False, data={"name": name_filter}, error="NOT_FOUND")

        if not parameters.get("confirmed"):
            closed_titles = (
                self._close_windows(matching, self.TERMINATE_WAIT_SECONDS)
                or self._close_windows_by_title(name_filter, self.TERMINATE_WAIT_SECONDS)
            )
            if closed_titles:
                return SkillResult(success=True, data={"name": name_filter, "closed": closed_titles, "graceful": True})
            names = ", ".join(sorted({p.info["name"] for p in matching}))
            return SkillResult(
                success=False,
                data={
                    "name": name_filter,
                    "message": f"Non ha finestre aperte: confermi di voler terminare {names}?",
                    "confirm_parameters": {"name": name_filter, "confirmed": True},
                },
                error="CONFIRMATION_REQUIRED",
            )

        import psutil

        closed = []
        pids = []
        for process in matching:
            try:
                process.terminate()
            except Exception:
                continue
            try:
                process.wait(timeout=self.TERMINATE_WAIT_SECONDS)
            except psutil.NoSuchProcess:
                pass  # gia' terminato per conto suo tra la richiesta e l'attesa: comunque chiuso
            except psutil.TimeoutExpired:
                continue  # non e' morto in tempo: non lo si conta come chiuso davvero
            closed.append(process.info["name"])
            pids.append(process.pid)

        if not closed:
            return SkillResult(success=False, data={"name": name_filter}, error="OPERATION_FAILED")
        return SkillResult(success=True, data={"name": name_filter, "closed": closed, "pids": pids})

    @staticmethod
    def _close_windows_by_title(name: str, wait_seconds: float) -> list[str]:
        """Le app di Store (Calcolatrice, Foto...) hanno la finestra in ApplicationFrameHost,
        non nel loro processo: si riconoscono dal titolo.

        F1.3.2: restituisce solo i titoli delle finestre VERIFICATE chiuse entro wait_seconds
        (vedi skills/window_control.py::_wait_until_window_closed), non semplicemente quelle a
        cui e' stato inviato WM_CLOSE senza errori - un invio riuscito non garantisce che la
        finestra sia davvero sparita."""
        try:
            import win32con
            import win32gui
        except ImportError:
            return []
        from skills.window_control import _wait_until_window_closed

        needle = name.lower()
        candidates = []  # (hwnd, title)

        def callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if title and needle in title.lower() and not title.lower().startswith("jake"):
                try:
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    candidates.append((hwnd, title))
                except Exception:
                    pass

        win32gui.EnumWindows(callback, None)
        return [title for hwnd, title in candidates if _wait_until_window_closed(win32gui, hwnd, wait_seconds)]

    @staticmethod
    def _close_windows(processes, wait_seconds: float) -> list[str]:
        """F1.3.2: stessa verifica di _close_windows_by_title sopra, per le finestre trovate
        tramite i pid dei processi corrispondenti invece che per titolo."""
        try:
            import win32con
            import win32gui
        except ImportError:
            return []
        from skills.window_control import _wait_until_window_closed

        pids = {process.info["pid"] for process in processes}
        candidates = []  # (hwnd, title)
        for hwnd, title, _pid in _windows_of_pids(pids):
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                candidates.append((hwnd, title))
            except Exception:
                continue
        return [title for hwnd, title in candidates if _wait_until_window_closed(win32gui, hwnd, wait_seconds)]
