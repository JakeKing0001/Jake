"""Test unitari per l'apprendimento continuo (v3.0, core/learning_manager.py): nessuna suite
dedicata esisteva finora, nonostante governi cosa Jake impara da solo dai comandi eseguiti -
un'associazione frase->intent imparata a torto (parametro allucinato, intent non imparabile,
osservazione mai confermata dall'utente) sopravvive ai riavvii e prende la corsia veloce
(nessuna chiamata al modello) la volta successiva. Usa un ExampleStore vero con file temporanei
(coerente con la convenzione della suite per i moduli backed da filesystem/DB), non un finto: e'
proprio l'interazione con il file JSONL reale (add_learned/find_exact/remove_learned) a essere
la parte interessante da verificare."""
import tempfile
import unittest
from pathlib import Path

from core.command import Command
from core.learning_manager import NON_LEARNABLE_INTENTS, LearningManager
from core.nlu.examples import ExampleStore


class FakeRetriever:
    def __init__(self, raise_on_add: bool = False):
        self.added = []
        self.removed = []
        self.refreshed = 0
        self.raise_on_add = raise_on_add

    def add_example(self, example):
        if self.raise_on_add:
            raise RuntimeError("indice non disponibile")
        self.added.append(example)

    def remove_example(self, text):
        self.removed.append(text)

    def refresh(self):
        self.refreshed += 1


class FakeNormalizer:
    def __init__(self):
        self.learned = []

    def learn_replacement(self, heard, meant):
        self.learned.append((heard, meant))


def _store() -> ExampleStore:
    tmp_dir = tempfile.mkdtemp(prefix="jake_learning_manager_test_")
    return ExampleStore(
        builtin_path=Path(tmp_dir) / "builtin.jsonl", learned_path=Path(tmp_dir) / "learned.jsonl",
    )


class ParametersGroundedTests(unittest.TestCase):
    def test_string_value_present_in_text_is_grounded(self):
        self.assertTrue(LearningManager._parameters_grounded("apri il blocco note", {"app": "blocco note"}))

    def test_string_value_absent_from_text_is_not_grounded(self):
        self.assertFalse(LearningManager._parameters_grounded("apri qualcosa", {"app": "blocco note"}))

    def test_matching_is_case_insensitive(self):
        self.assertTrue(LearningManager._parameters_grounded("apri BLOCCO NOTE", {"app": "blocco note"}))

    def test_empty_string_value_is_always_grounded(self):
        self.assertTrue(LearningManager._parameters_grounded("qualsiasi cosa", {"note": ""}))

    def test_booleans_are_never_checked_against_the_text(self):
        self.assertTrue(LearningManager._parameters_grounded("qualsiasi cosa", {"force": True}))

    def test_int_value_present_as_digits_is_grounded(self):
        self.assertTrue(LearningManager._parameters_grounded("libera la porta 8080", {"port": 8080}))

    def test_int_value_absent_is_not_grounded(self):
        self.assertFalse(LearningManager._parameters_grounded("libera una porta", {"port": 8080}))

    def test_list_of_strings_all_present_is_grounded(self):
        self.assertTrue(LearningManager._parameters_grounded(
            "sposta a e b", {"items": ["a", "b"]},
        ))

    def test_list_with_one_missing_item_is_not_grounded(self):
        self.assertFalse(LearningManager._parameters_grounded(
            "sposta a", {"items": ["a", "b"]},
        ))

    def test_unsupported_value_type_is_not_grounded(self):
        # Un valore che non e' ne' stringa/numero/bool/lista di stringhe (es. un dict annidato)
        # non puo' essere verificato contro il testo: meglio rifiutare che fidarsi alla cieca.
        self.assertFalse(LearningManager._parameters_grounded("qualsiasi cosa", {"nested": {"a": 1}}))


class ObserveAndCommitTests(unittest.TestCase):
    def _manager(self, retriever=None, normalizer=None):
        return LearningManager(_store(), retriever=retriever, normalizer=normalizer)

    def test_successful_llm_observation_is_learned_as_auto_after_the_next_call(self):
        manager = self._manager()
        result = _success({"app": "blocco note"})
        manager.observe("apri il blocco note", Command("OPEN_APP", {"app": "blocco note"}), result, route="llm")
        self.assertIsNone(manager.example_store.find_exact("apri il blocco note"))
        manager.commit_pending()
        example = manager.example_store.find_exact("apri il blocco note")
        self.assertIsNotNone(example)
        self.assertEqual(example.intent, "OPEN_APP")
        self.assertEqual(example.source, "auto")

    def test_next_observe_call_commits_the_previous_pending_example(self):
        manager = self._manager()
        result = _success({"app": "blocco note"})
        manager.observe("apri il blocco note", Command("OPEN_APP", {"app": "blocco note"}), result, route="llm")
        manager.observe("che ore sono", None, None, route="llm")  # qualsiasi comando successivo
        self.assertIsNotNone(manager.example_store.find_exact("apri il blocco note"))

    def test_non_llm_route_is_never_learned(self):
        manager = self._manager()
        result = _success({"app": "blocco note"})
        manager.observe("apri il blocco note", Command("OPEN_APP", {"app": "blocco note"}), result, route="exact")
        manager.commit_pending()
        self.assertIsNone(manager.example_store.find_exact("apri il blocco note"))

    def test_non_learnable_intent_is_never_learned(self):
        manager = self._manager()
        for intent in ("RUN_COMMAND", "SYSTEM_POWER", "DELETE_PATH", "SET_MODEL"):
            manager.observe("qualsiasi frase", Command(intent, {}), _success({}), route="llm")
            manager.commit_pending()
            self.assertIsNone(manager.example_store.find_exact("qualsiasi frase"), msg=intent)

    def test_none_result_is_never_learned(self):
        manager = self._manager()
        manager.observe("apri il blocco note", Command("OPEN_APP", {"app": "blocco note"}), None, route="llm")
        manager.commit_pending()
        self.assertIsNone(manager.example_store.find_exact("apri il blocco note"))

    def test_failed_result_is_never_learned(self):
        manager = self._manager()
        result = _failure("OPERATION_FAILED")
        manager.observe("apri il blocco note", Command("OPEN_APP", {"app": "blocco note"}), result, route="llm")
        manager.commit_pending()
        self.assertIsNone(manager.example_store.find_exact("apri il blocco note"))

    def test_hallucinated_parameter_not_present_in_text_is_never_learned(self):
        """Il cuore della garanzia dichiarata nel modulo: un parametro che il modello ha
        inventato (non compare nella frase dell'utente) non deve cristallizzarsi come esempio,
        altrimenti la corsia veloce lo riproporrebbe identico anche quando e' sbagliato."""
        manager = self._manager()
        result = _success({"app": "spotify"})
        manager.observe("apri qualcosa", Command("OPEN_APP", {"app": "spotify"}), result, route="llm")
        manager.commit_pending()
        self.assertIsNone(manager.example_store.find_exact("apri qualcosa"))

    def test_discard_pending_prevents_learning(self):
        manager = self._manager()
        result = _success({"app": "blocco note"})
        manager.observe("apri il blocco note", Command("OPEN_APP", {"app": "blocco note"}), result, route="llm")
        manager.discard_pending()
        manager.commit_pending()
        self.assertIsNone(manager.example_store.find_exact("apri il blocco note"))

    def test_auto_observation_never_overwrites_an_existing_taught_example(self):
        manager = self._manager()
        manager.teach("apri il blocco note", "OPEN_APP", {"app": "blocco note"})
        result = _success({"app": "diverso"})
        manager.observe("apri il blocco note", Command("OPEN_APP", {"app": "diverso"}), result, route="llm")
        manager.commit_pending()
        example = manager.example_store.find_exact("apri il blocco note")
        self.assertEqual(example.source, "taught")
        self.assertEqual(example.parameters, {"app": "blocco note"})

    def test_confirmation_required_error_does_not_block_setting_pending(self):
        """Il modulo tratta esplicitamente CONFIRMATION_REQUIRED come 'non un fallimento da
        scartare' (vedi il controllo su result.error in observe()): oggi nessun chiamante in
        core/jake_core.py raggiunge observe() con questo errore (la richiesta di conferma
        interrompe il flusso prima), ma la funzione va comunque verificata per quello che fa
        davvero, non per come viene chiamata oggi."""
        manager = self._manager()
        result = _failure("CONFIRMATION_REQUIRED", data={"app": "blocco note"})
        manager.observe("apri il blocco note", Command("OPEN_APP", {"app": "blocco note"}), result, route="llm")
        manager.commit_pending()
        self.assertIsNotNone(manager.example_store.find_exact("apri il blocco note"))

    def test_retriever_is_updated_when_an_example_is_learned(self):
        retriever = FakeRetriever()
        manager = self._manager(retriever=retriever)
        result = _success({"app": "blocco note"})
        manager.observe("apri il blocco note", Command("OPEN_APP", {"app": "blocco note"}), result, route="llm")
        manager.commit_pending()
        self.assertEqual(len(retriever.added), 1)
        self.assertEqual(retriever.added[0].intent, "OPEN_APP")

    def test_retriever_failure_does_not_prevent_the_example_from_being_stored(self):
        retriever = FakeRetriever(raise_on_add=True)
        manager = self._manager(retriever=retriever)
        result = _success({"app": "blocco note"})
        manager.observe("apri il blocco note", Command("OPEN_APP", {"app": "blocco note"}), result, route="llm")
        manager.commit_pending()
        self.assertIsNotNone(manager.example_store.find_exact("apri il blocco note"))


class TeachCorrectForgetTests(unittest.TestCase):
    def test_teach_stores_with_source_taught_by_default(self):
        manager = LearningManager(_store())
        manager.teach("gioca a testa o croce", "FLIP_COIN", {})
        example = manager.example_store.find_exact("gioca a testa o croce")
        self.assertEqual(example.source, "taught")

    def test_teach_discards_any_pending_auto_observation_first(self):
        manager = LearningManager(_store())
        manager.observe("apri il blocco note", Command("OPEN_APP", {"app": "blocco note"}), _success({"app": "blocco note"}), route="llm")
        manager.teach("gioca a testa o croce", "FLIP_COIN", {})
        self.assertIsNone(manager.example_store.find_exact("apri il blocco note"))

    def test_correct_stores_the_new_mapping_with_source_corrected(self):
        manager = LearningManager(_store())
        previous_command = Command("FLIP_COIN", {})
        new_command = Command("ROLL_DICE", {})
        manager.correct("lancia una moneta", previous_command, new_command)
        example = manager.example_store.find_exact("lancia una moneta")
        self.assertEqual(example.intent, "ROLL_DICE")
        self.assertEqual(example.source, "corrected")

    def test_correct_with_a_non_learnable_new_intent_is_ignored(self):
        manager = LearningManager(_store())
        manager.correct("fai qualcosa", Command("UNKNOWN", {}), Command("RUN_COMMAND", {"command": "dir"}))
        self.assertIsNone(manager.example_store.find_exact("fai qualcosa"))

    def test_correct_with_empty_previous_text_is_ignored(self):
        manager = LearningManager(_store())
        manager.correct("", Command("UNKNOWN", {}), Command("FLIP_COIN", {}))
        self.assertEqual(manager.example_store.learned(), [])

    def test_correct_learns_a_single_differing_string_parameter_as_a_vocabulary_replacement(self):
        normalizer = FakeNormalizer()
        manager = LearningManager(_store(), normalizer=normalizer)
        previous_command = Command("OPEN_APP", {"app": "judy westcode"})
        new_command = Command("OPEN_APP", {"app": "visual studio code"})
        manager.correct("apri judy westcode", previous_command, new_command)
        self.assertEqual(normalizer.learned, [("judy westcode", "visual studio code")])

    def test_correct_does_not_learn_a_replacement_when_the_intent_changed(self):
        normalizer = FakeNormalizer()
        manager = LearningManager(_store(), normalizer=normalizer)
        previous_command = Command("FLIP_COIN", {})
        new_command = Command("OPEN_APP", {"app": "visual studio code"})
        manager.correct("apri qualcosa", previous_command, new_command)
        self.assertEqual(normalizer.learned, [])

    def test_correct_does_not_learn_a_replacement_when_more_than_one_parameter_differs(self):
        normalizer = FakeNormalizer()
        manager = LearningManager(_store(), normalizer=normalizer)
        previous_command = Command("SEARCH_FILES", {"query": "foo", "folder": "vecchia"})
        new_command = Command("SEARCH_FILES", {"query": "bar", "folder": "nuova"})
        manager.correct("cerca foo", previous_command, new_command)
        self.assertEqual(normalizer.learned, [])

    def test_forget_removes_a_learned_example_and_notifies_the_retriever(self):
        retriever = FakeRetriever()
        manager = LearningManager(_store(), retriever=retriever)
        manager.teach("gioca a testa o croce", "FLIP_COIN", {})
        removed = manager.forget("gioca a testa o croce")
        self.assertTrue(removed)
        self.assertIsNone(manager.example_store.find_exact("gioca a testa o croce"))
        self.assertEqual(retriever.removed, ["gioca a testa o croce"])

    def test_forget_returns_false_when_nothing_matches(self):
        manager = LearningManager(_store())
        self.assertFalse(manager.forget("frase mai insegnata"))

    def test_forget_intent_removes_every_example_of_that_intent_and_refreshes_the_retriever(self):
        retriever = FakeRetriever()
        manager = LearningManager(_store(), retriever=retriever)
        manager.teach("gioca a testa o croce", "FLIP_COIN", {})
        manager.teach("lancia una moneta", "FLIP_COIN", {})
        manager.teach("tira un dado", "ROLL_DICE", {})
        removed = manager.forget_intent("FLIP_COIN")
        self.assertEqual(removed, 2)
        self.assertIsNone(manager.example_store.find_exact("gioca a testa o croce"))
        self.assertIsNotNone(manager.example_store.find_exact("tira un dado"))
        self.assertEqual(retriever.refreshed, 1)

    def test_forget_intent_survives_a_retriever_refresh_failure(self):
        class ExplodingRetriever(FakeRetriever):
            def refresh(self):
                raise RuntimeError("indice corrotto")

        manager = LearningManager(_store(), retriever=ExplodingRetriever())
        manager.teach("gioca a testa o croce", "FLIP_COIN", {})
        removed = manager.forget_intent("FLIP_COIN")  # non deve sollevare
        self.assertEqual(removed, 1)

    def test_list_taught_includes_taught_and_corrected_but_not_auto(self):
        manager = LearningManager(_store())
        manager.teach("gioca a testa o croce", "FLIP_COIN", {})
        manager.correct("lancia una moneta", Command("UNKNOWN", {}), Command("FLIP_COIN", {}))
        manager.observe("apri il blocco note", Command("OPEN_APP", {"app": "blocco note"}), _success({"app": "blocco note"}), route="llm")
        manager.commit_pending()
        sources = sorted(example.source for example in manager.list_taught())
        self.assertEqual(sources, ["corrected", "taught"])

    def test_count_auto_counts_only_auto_sourced_examples(self):
        manager = LearningManager(_store())
        manager.teach("gioca a testa o croce", "FLIP_COIN", {})
        manager.observe("apri il blocco note", Command("OPEN_APP", {"app": "blocco note"}), _success({"app": "blocco note"}), route="llm")
        manager.commit_pending()
        self.assertEqual(manager.count_auto(), 1)


class NonLearnableIntentsTests(unittest.TestCase):
    def test_meta_and_highest_risk_intents_are_all_excluded(self):
        for intent in (
            "RUN_COMMAND", "SET_MODEL", "SYSTEM_POWER", "DELETE_PATH", "CREATE_SKILL",
            "UNKNOWN", "ASK_QUESTION", "CHITCHAT",
        ):
            self.assertIn(intent, NON_LEARNABLE_INTENTS)


def _success(data: dict):
    from core.skill_result import SkillResult
    return SkillResult(success=True, data=data)


def _failure(error: str, data: dict = None):
    from core.skill_result import SkillResult
    return SkillResult(success=False, data=data or {}, error=error)


if __name__ == "__main__":
    unittest.main()
