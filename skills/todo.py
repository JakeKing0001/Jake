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
        return SkillResult(success=True, data={"text": completed["text"]})


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
