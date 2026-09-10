"""Test unitari per il classificatore a regole di riserva (v0, core/intent_provider.py): nessuna
suite esisteva finora per RuleBasedProvider, nonostante sia il percorso REALE preso quando
Ollama non e' disponibile (core/router.py, fallback_provider) - non codice morto. Riprodotto per
davvero, prima della correzione in questa sessione, che i trigger corti coincidenti con radici
verbali italiane (es. "cancella", "elimina", "apri") scattavano anche come sottostringa dentro
una parola piu' lunga con un significato completamente diverso ("cancellare", "eliminato",
"aprile"): "vorrei cancellare tutto" veniva misclassificato DELETE_PATH (un intent DESTRUCTIVE)
con un percorso spazzatura. Questa suite blocca esplicitamente quella classe di bug."""
import unittest

from core.intent_provider import RuleBasedProvider


class SubstringFalsePositiveRegressionTests(unittest.TestCase):
    """F1 (indirettamente): un trigger corto che e' anche una radice verbale italiana non deve
    scattare come sottostringa di una parola piu' lunga con un significato diverso."""

    def setUp(self):
        self.provider = RuleBasedProvider()

    def test_delete_trigger_does_not_fire_inside_a_longer_unrelated_word(self):
        for text in ("vorrei cancellare tutto", "ho eliminato il file ieri", "l'eliminazione e' avvenuta"):
            self.assertEqual(self.provider.detect_intent(text).intent, "UNKNOWN", msg=text)

    def test_open_trigger_does_not_fire_inside_a_month_name(self):
        self.assertEqual(
            self.provider.detect_intent("il 3 aprile e' il mio compleanno").intent, "UNKNOWN",
        )

    def test_open_search_result_requires_both_words_as_whole_words(self):
        self.assertEqual(
            self.provider.detect_intent(
                "il 3 aprile e' il mio compleanno, dimmi il risultato della partita",
            ).intent,
            "UNKNOWN",
        )

    def test_recall_bare_trigger_does_not_fire_inside_a_longer_word(self):
        self.assertEqual(self.provider.detect_intent("la ricordissima gita").intent, "UNKNOWN")

    def test_close_app_no_longer_offers_the_ambiguous_termina_trigger(self):
        """'termina' e' anche la forma indicativa di 'terminare' ('il contratto termina...'),
        non solo l'imperativo ('termina Spotify'): un confine di parola da solo non basta a
        distinguerli, quindi il trigger e' stato tolto invece di tentare un'euristica fragile."""
        for text in ("quando termina la partita", "il contratto termina a dicembre", "non voglio che termini cosi'"):
            self.assertEqual(self.provider.detect_intent(text).intent, "UNKNOWN", msg=text)

    def test_legitimate_delete_command_still_works(self):
        command = self.provider.detect_intent("cancella il file appunti.txt")
        self.assertEqual(command.intent, "DELETE_PATH")
        self.assertEqual(command.parameters["path"], "il file appunti.txt")

    def test_legitimate_close_app_command_still_works_via_chiudi(self):
        command = self.provider.detect_intent("chiudi spotify")
        self.assertEqual(command.intent, "CLOSE_APP")
        self.assertEqual(command.parameters["name"], "spotify")

    def test_legitimate_open_search_result_still_works(self):
        command = self.provider.detect_intent("apri il risultato 2")
        self.assertEqual(command.intent, "OPEN_SEARCH_RESULT")
        self.assertEqual(command.parameters["index"], 2)

    def test_legitimate_remember_with_a_month_name_still_works(self):
        command = self.provider.detect_intent("ricorda che il mio compleanno e' il 3 aprile")
        self.assertEqual(command.intent, "REMEMBER")

    def test_legitimate_recall_still_works(self):
        command = self.provider.detect_intent("ricordi cosa ti ho detto ieri")
        self.assertEqual(command.intent, "RECALL")
        self.assertEqual(command.parameters["query"], "cosa ti ho detto ieri")


class BasicRoutingTests(unittest.TestCase):
    """Copertura di base per una manciata di percorsi rappresentativi (uno per famiglia di
    trigger: apertura, promemoria con orario relativo/assoluto, aritmetica, tempo)."""

    def setUp(self):
        self.provider = RuleBasedProvider()

    def test_open_app(self):
        command = self.provider.detect_intent("apri il blocco note")
        self.assertEqual(command.intent, "OPEN_APP")
        self.assertEqual(command.parameters["app"], "il blocco note")

    def test_open_in_editor_wins_over_open_app_for_the_same_apri_trigger(self):
        command = self.provider.detect_intent("apri in vscode main.py")
        self.assertEqual(command.intent, "OPEN_IN_EDITOR")
        self.assertEqual(command.parameters["path"], "main.py")

    def test_reminder_with_relative_minutes(self):
        command = self.provider.detect_intent("ricordami di chiamare mamma tra 10 minuti")
        self.assertEqual(command.intent, "SET_REMINDER")
        self.assertEqual(command.parameters["in_minutes"], 10)
        self.assertEqual(command.parameters["text"], "chiamare mamma")

    def test_reminder_with_absolute_time(self):
        command = self.provider.detect_intent("ricordami di uscire alle 18:30")
        self.assertEqual(command.intent, "SET_REMINDER")
        self.assertEqual(command.parameters["at_time"], "18:30")

    def test_reminder_without_a_time_reference_falls_through_to_unknown(self):
        # _parse_reminder torna None senza riferimento orario: nessun altro trigger successivo
        # matcha "ricordami di comprare il latte", quindi il risultato finale e' UNKNOWN.
        command = self.provider.detect_intent("ricordami di comprare il latte")
        self.assertEqual(command.intent, "UNKNOWN")

    def test_calculate_normalizes_italian_math_words(self):
        command = self.provider.detect_intent("quanto fa 3 piu 4")
        self.assertEqual(command.intent, "CALCULATE")
        self.assertEqual(command.parameters["expression"], "3 + 4")

    def test_convert_units(self):
        command = self.provider.detect_intent("converti 10 km in miglia")
        self.assertEqual(command.intent, "CONVERT_UNITS")
        self.assertEqual(command.parameters, {"value": 10.0, "from_unit": "km", "to_unit": "miglia"})

    def test_get_time(self):
        self.assertEqual(self.provider.detect_intent("che ore sono").intent, "GET_TIME")

    def test_get_date(self):
        self.assertEqual(self.provider.detect_intent("che giorno e' oggi").intent, "GET_DATE")

    def test_completely_unrelated_text_is_unknown(self):
        self.assertEqual(self.provider.detect_intent("blah blah qualcosa senza senso").intent, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
