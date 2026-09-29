from core.skill_result import SkillResult
from core.todo_manager import TodoManager


class AddTodoSkill:
    metadata = {
        "intent": "ADD_TODO",
        "description": "Aggiunge un'attivita' alla lista delle cose da fare di Jake (todo list). "
        "Usalo per richieste come 'aggiungi alla lista delle cose da fare...', 'segna che devo...', "
        "'metti in lista...'. Diverso da SET_REMINDER: qui non c'e' un orario, e' solo un elenco.",
        "parameters": {
            "text": {
                "type": "string",
                "required": True,
                "description": "Il testo dell'attivita' da aggiungere, con le stesse parole dell'utente.",
            },
        },
    }

    def __init__(self, todo_manager: TodoManager):
        self.todo_manager = todo_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        self.todo_manager.add(text)
        return SkillResult(success=True, data={"text": text})


class ListTodosSkill:
    metadata = {
        "intent": "LIST_TODOS",
        "description": "Elenca le attivita' ancora da fare nella todo list. Usalo per richieste "
        "come 'cosa devo fare', 'mostrami la lista delle cose da fare', 'elenca i task'.",
        "parameters": {},
    }

    def __init__(self, todo_manager: TodoManager):
        self.todo_manager = todo_manager

    def execute(self, parameters: dict = None):
        pending = self.todo_manager.list_pending()
        if not pending:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"todos": pending})


class CompleteTodoSkill:
    metadata = {
        "intent": "COMPLETE_TODO",
        "description": "Segna come completata un'attivita' della todo list, cercandola per "
        "somiglianza col testo. Usalo per richieste come 'ho fatto...', 'segna come completato...', "
        "'ho finito il task...'.",
        "parameters": {
            "text": {
                "type": "string",
                "required": True,
                "description": "Testo (anche parziale) dell'attivita' completata da cercare in lista.",
            },
        },
    }

    def __init__(self, todo_manager: TodoManager):
        self.todo_manager = todo_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        completed = self.todo_manager.complete_matching(text)
        if completed is None:
            return SkillResult(success=False, data={"text": text}, error="NOT_FOUND")
        return SkillResult(success=True, data={"text": completed["text"], **{
            k: completed[k] for k in ("goal", "steps_total", "steps_done", "next_step") if k in completed}})


class PlanGoalSkill:
    metadata = {
        "intent": "PLAN_GOAL",
        "description": "Scompone un obiettivo della todo list in passi (milestone) nell'ordine detto dall'utente. Per "
        "'per finire la tesi i passi sono: scrivere l'introduzione, fare gli esperimenti, rileggere', "
        "'aggiungi all'obiettivo trasloco il passo prenotare il furgone'.",
        "parameters": {
            "goal": {"type": "string", "required": True, "description": "L'obiettivo, con le parole dell'utente."},
            "steps": {"type": "array", "required": True, "description": "I passi, nell'ordine, con le parole dell'utente."},
        },
    }

    def __init__(self, todo_manager: TodoManager):
        self.todo_manager = todo_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        goal = str(parameters.get("goal") or "").strip()
        raw = parameters.get("steps") or []
        steps = [s.strip() for s in (raw.split(",") if isinstance(raw, str) else raw) if str(s).strip()]
        if not goal or not steps:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        return SkillResult(success=True, data=self.todo_manager.plan_goal(goal, [str(s) for s in steps]))


class NextStepSkill:
    metadata = {
        "intent": "NEXT_STEP",
        "description": "Dice il prossimo passo ancora da fare di un obiettivo della todo list e a che punto e'. Per "
        "'qual e' il prossimo passo per la tesi?', 'cosa mi manca per il trasloco?'.",
        "parameters": {
            "goal": {"type": "string", "required": True, "description": "L'obiettivo, anche parziale."},
        },
    }

    def __init__(self, todo_manager: TodoManager):
        self.todo_manager = todo_manager

    def execute(self, parameters: dict = None):
        goal = str((parameters or {}).get("goal") or "").strip()
        if not goal:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        found = self.todo_manager.next_step(goal)
        if found is None:
            return SkillResult(success=False, data={"goal": goal}, error="NOT_FOUND")
        return SkillResult(success=True, data=found)


class DeleteTodoSkill:
    metadata = {
        "intent": "DELETE_TODO",
        "description": "Elimina un'attivita' dalla todo list senza segnarla come completata, cercandola per somiglianza col testo.",
        "parameters": {
            "text": {
                "type": "string",
                "required": True,
                "description": "Testo (anche parziale) dell'attivita' da eliminare.",
            },
        },
    }

    def __init__(self, todo_manager: TodoManager):
        self.todo_manager = todo_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        text = (parameters.get("text") or "").strip()
        if not text:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        deleted = self.todo_manager.delete_matching(text)
        if deleted is None:
            return SkillResult(success=False, data={"text": text}, error="NOT_FOUND")
        return SkillResult(success=True, data={"text": deleted["text"]})
