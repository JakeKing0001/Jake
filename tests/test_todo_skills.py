"""Test unitari per skills/todo.py: nessuna suite esisteva finora (core/todo_manager.py ha gia'
tests/test_todo_manager.py, ma copre solo list_stale_pending - add/list_pending/
complete_matching/delete_matching restano senza copertura anche li'). Usa un TodoManager vero
su file temporaneo, non un finto: e' proprio l'SQL LIKE di complete_matching/delete_matching a
essere la parte interessante da verificare.

Nota su un limite noto, non "corretto" qui: complete_matching/delete_matching cercano per
sottostringa (SQL LIKE '%query%') senza un limite di lunghezza minimo, e prendono il PIU'
VECCHIO risultato che corrisponde (ORDER BY id ASC LIMIT 1) - un testo di ricerca ambiguo
potrebbe risolvere al task sbagliato. A differenza del buco analogo e corretto in questa
sessione per CLOSE_APP (core/skills/process_control.py), qui non esiste un valore minimo
"sicuro" gia' curato da cui dedurre una soglia (li' il piu' corto alias legittimo era lungo 3),
e il rischio resta contenuto alla lista todo dell'utente stesso (non ad app/finestre di terzi) -
DELETE_TODO passa comunque dal gate centrale di conferma. Documentato qui con un test esplicito
invece di far finta che il problema non esista."""
import tempfile
import unittest
from pathlib import Path

from core.todo_manager import TodoManager
from skills.todo import AddTodoSkill, CompleteTodoSkill, DeleteTodoSkill, ListTodosSkill


class _WithManager(unittest.TestCase):
    def setUp(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_todo_skill_test_"))
        self.manager = TodoManager(db_path=tmp_dir / "todo.db")


class AddTodoTests(_WithManager):
    def test_missing_text_fails(self):
        result = AddTodoSkill(self.manager).execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_todo_is_actually_added_and_appears_in_the_pending_list(self):
        result = AddTodoSkill(self.manager).execute({"text": "comprare il pane"})
        self.assertTrue(result.success)
        self.assertEqual([t["text"] for t in self.manager.list_pending()], ["comprare il pane"])


class ListTodosTests(_WithManager):
    def test_no_pending_todos_reports_not_found(self):
        result = ListTodosSkill(self.manager).execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_lists_pending_todos_in_insertion_order(self):
        AddTodoSkill(self.manager).execute({"text": "primo"})
        AddTodoSkill(self.manager).execute({"text": "secondo"})
        result = ListTodosSkill(self.manager).execute({})
        self.assertEqual([t["text"] for t in result.data["todos"]], ["primo", "secondo"])


class CompleteTodoTests(_WithManager):
    def test_missing_text_fails(self):
        result = CompleteTodoSkill(self.manager).execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_completing_an_unknown_task_reports_not_found(self):
        result = CompleteTodoSkill(self.manager).execute({"text": "non esiste"})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_completing_a_task_removes_it_from_the_pending_list(self):
        AddTodoSkill(self.manager).execute({"text": "comprare il pane"})
        result = CompleteTodoSkill(self.manager).execute({"text": "pane"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["text"], "comprare il pane")
        self.assertEqual(self.manager.list_pending(), [])

    def test_a_completed_task_cannot_be_completed_again(self):
        AddTodoSkill(self.manager).execute({"text": "comprare il pane"})
        CompleteTodoSkill(self.manager).execute({"text": "pane"})
        result = CompleteTodoSkill(self.manager).execute({"text": "pane"})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_an_ambiguous_query_resolves_to_the_oldest_matching_task(self):
        """Comportamento noto, non 'corretto' qui (vedi il docstring del modulo): con piu' di un
        task che contiene la stessa sottostringa, vince il piu' vecchio - non necessariamente
        quello che l'utente intendeva."""
        AddTodoSkill(self.manager).execute({"text": "comprare il pane"})
        AddTodoSkill(self.manager).execute({"text": "comprare il panettone"})
        result = CompleteTodoSkill(self.manager).execute({"text": "pan"})
        self.assertEqual(result.data["text"], "comprare il pane")
        self.assertEqual([t["text"] for t in self.manager.list_pending()], ["comprare il panettone"])


class DeleteTodoTests(_WithManager):
    def test_missing_text_fails(self):
        result = DeleteTodoSkill(self.manager).execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_deleting_an_unknown_task_reports_not_found(self):
        result = DeleteTodoSkill(self.manager).execute({"text": "non esiste"})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_deleting_a_task_removes_it_permanently(self):
        AddTodoSkill(self.manager).execute({"text": "comprare il pane"})
        result = DeleteTodoSkill(self.manager).execute({"text": "pane"})
        self.assertTrue(result.success)
        self.assertEqual(self.manager.list_pending(), [])

    def test_deleting_does_not_affect_unrelated_tasks(self):
        AddTodoSkill(self.manager).execute({"text": "comprare il pane"})
        AddTodoSkill(self.manager).execute({"text": "portare fuori il cane"})
        DeleteTodoSkill(self.manager).execute({"text": "pane"})
        self.assertEqual([t["text"] for t in self.manager.list_pending()], ["portare fuori il cane"])


if __name__ == "__main__":
    unittest.main()
