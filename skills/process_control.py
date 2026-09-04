from core.skill_result import SkillResult


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

        matches = []
        for process in psutil.process_iter(["pid", "name"]):
            process_name = process.info.get("name") or ""
            if not name_filter or name_filter in process_name.lower():
                matches.append({"pid": process.info["pid"], "name": process_name})
                if len(matches) >= self.MAX_RESULTS:
                    break

        if not matches:
            return SkillResult(success=False, data={"name": name_filter}, error="NOT_FOUND")
        return SkillResult(success=True, data={"processes": matches})


class CloseAppSkill:
    """Termina i processi il cui nome corrisponde. Richiede sempre conferma (azione irreversibile)."""

    metadata = {
        "intent": "CLOSE_APP",
        "description": "Chiude (termina) un'applicazione in esecuzione, dato il nome del processo.",
        "parameters": {
            "name": {
                "type": "string",
                "required": True,
                "description": "Nome (anche parziale) del processo da chiudere, es. 'notepad'.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        import psutil

        parameters = parameters or {}
        name_filter = (parameters.get("name") or "").strip().lower()
        if not name_filter:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        matching = [
            process for process in psutil.process_iter(["pid", "name"])
            if name_filter in (process.info.get("name") or "").lower()
        ]
        if not matching:
            return SkillResult(success=False, data={"name": name_filter}, error="NOT_FOUND")

        if not parameters.get("confirmed"):
            names = ", ".join(sorted({p.info["name"] for p in matching}))
            return SkillResult(
                success=False,
                data={
                    "name": name_filter,
                    "message": f"Confermi di voler chiudere: {names}?",
                    "confirm_parameters": {"name": name_filter, "confirmed": True},
                },
                error="CONFIRMATION_REQUIRED",
            )

        closed = []
        for process in matching:
            try:
                process.terminate()
                closed.append(process.info["name"])
            except Exception:
                continue

        if not closed:
            return SkillResult(success=False, data={"name": name_filter}, error="OPERATION_FAILED")
        return SkillResult(success=True, data={"name": name_filter, "closed": closed})
