"""Test unitari del riconoscimento pattern estratto da JakeCore (v3.2): nessuna dipendenza
da Ollama, microfono o GUI. Esecuzione: .venv\\Scripts\\python.exe -m unittest discover -s tests -t . -v"""
import unittest

from core import intent_patterns


class ExitDetectionTests(unittest.TestCase):
    def test_bare_exit_words(self):
        for text in ("esci", "usci", "chiudi jake", "spegniti", "jake spegniti"):
            self.assertTrue(intent_patterns.is_exit(text))

    def test_exit_with_trailing_words(self):
        self.assertTrue(intent_patterns.is_exit("esci pure"))
        self.assertTrue(intent_patterns.is_exit("usci ora"))

    def test_not_exit(self):
        self.assertFalse(intent_patterns.is_exit("apri spotify"))
        self.assertFalse(intent_patterns.is_exit("uscita di sicurezza"))


class MultiStepDetectionTests(unittest.TestCase):
    def test_detects_sequential_marker(self):
        self.assertTrue(intent_patterns.is_multi_step_request("apri opera e poi cerca gatti"))

    def test_detects_conjunction_before_action_verb(self):
        self.assertTrue(intent_patterns.is_multi_step_request("apri blocco note e elimina il file vecchio"))

    def test_conjunction_inside_single_command_is_not_multi_step(self):
        self.assertFalse(intent_patterns.is_multi_step_request("cerca ricette pasta e ceci"))

    def test_workflow_definition_is_not_multi_step(self):
        self.assertFalse(intent_patterns.is_multi_step_request("quando dico buonanotte spegni il pc e chiudi tutto"))

    def test_browser_combo_is_not_multi_step(self):
        self.assertFalse(intent_patterns.is_multi_step_request("apri youtube e cerca gatti"))


class QuestionDetectionTests(unittest.TestCase):
    def test_wh_question(self):
        self.assertTrue(intent_patterns.is_question("come si chiama il gatto"))

    def test_question_mark(self):
        self.assertTrue(intent_patterns.is_question("funziona?"))

    def test_ordinary_command_is_not_question(self):
        self.assertFalse(intent_patterns.is_question("apri spotify"))


class MetaCommandDetectionTests(unittest.TestCase):
    def test_learn_command(self):
        command = intent_patterns.match_meta_command("quando dico buonanotte spegni il pc", has_last_exchange=False)
        self.assertEqual(command.intent, "LEARN_COMMAND")
        self.assertEqual(command.parameters["phrase"], "buonanotte")
        self.assertEqual(command.parameters["request"], "spegni il pc")

    def test_correction_requires_last_exchange(self):
        self.assertIsNone(intent_patterns.match_meta_command("no, intendevo aprire opera", has_last_exchange=False))
        command = intent_patterns.match_meta_command("no, intendevo aprire opera", has_last_exchange=True)
        self.assertEqual(command.intent, "CORRECT_LAST")
        self.assertEqual(command.parameters["request"], "aprire opera")

    def test_ordinary_text_matches_nothing(self):
        self.assertIsNone(intent_patterns.match_meta_command("apri spotify", has_last_exchange=True))


class AnswerDetectionTests(unittest.TestCase):
    def test_positive_answers(self):
        for text in ("si", "sì", "ok", "va bene", "procedi"):
            self.assertTrue(intent_patterns.is_positive_answer(text))
        self.assertFalse(intent_patterns.is_positive_answer("no"))

    def test_negative_answers(self):
        for text in ("no", "annulla", "lascia stare", "stop"):
            self.assertTrue(intent_patterns.is_negative_answer(text))
        self.assertFalse(intent_patterns.is_negative_answer("si"))


class PronounResolutionTests(unittest.TestCase):
    def test_aprilo_uses_last_path(self):
        self.assertEqual(
            intent_patterns.resolve_pronouns("aprilo", {"path": "C:/note.txt"}), "apri C:/note.txt",
        )

    def test_chiudilo_uses_last_app(self):
        self.assertEqual(
            intent_patterns.resolve_pronouns("chiudilo", {"app": "spotify"}), "chiudi spotify",
        )

    def test_no_entities_leaves_text_untouched(self):
        self.assertEqual(intent_patterns.resolve_pronouns("aprilo", {}), "aprilo")

    def test_does_not_fire_inside_longer_sentence(self):
        text = "aprilo con calma piu tardi"
        self.assertEqual(intent_patterns.resolve_pronouns(text, {"path": "C:/note.txt"}), text)


if __name__ == "__main__":
    unittest.main()
