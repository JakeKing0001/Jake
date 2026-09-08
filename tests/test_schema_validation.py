"""Test unitari per la validazione della busta di conferma/autenticazione (F1, Trustworthy
Agent Core 3.0 - vedi core/schema_validation.py)."""
import unittest

from core.schema_validation import validate_confirm_envelope


class ValidEnvelopeTests(unittest.TestCase):
    def test_minimal_valid_envelope_has_no_problems(self):
        problems = validate_confirm_envelope({"message": "Confermi?", "confirm_parameters": {"confirmed": True}})
        self.assertEqual(problems, [])

    def test_confirm_intent_is_optional_but_valid_when_present(self):
        problems = validate_confirm_envelope({
            "message": "Confermi?", "confirm_parameters": {}, "confirm_intent": "DELETE_PATH",
        })
        self.assertEqual(problems, [])

    def test_extra_unrelated_fields_are_ignored(self):
        problems = validate_confirm_envelope({
            "message": "Confermi?", "confirm_parameters": {}, "path": "c:/qualcosa.txt",
        })
        self.assertEqual(problems, [])


class InvalidEnvelopeTests(unittest.TestCase):
    def test_not_a_dict_is_reported(self):
        problems = validate_confirm_envelope(["non", "e'", "un", "dict"])
        self.assertEqual(len(problems), 1)
        self.assertIn("dict", problems[0])

    def test_missing_message_is_reported(self):
        problems = validate_confirm_envelope({"confirm_parameters": {}})
        self.assertIn("campo obbligatorio mancante: 'message'", problems)

    def test_missing_confirm_parameters_is_reported(self):
        problems = validate_confirm_envelope({"message": "Confermi?"})
        self.assertIn("campo obbligatorio mancante: 'confirm_parameters'", problems)

    def test_wrong_type_for_message_is_reported(self):
        problems = validate_confirm_envelope({"message": 123, "confirm_parameters": {}})
        self.assertTrue(any("'message'" in p for p in problems))

    def test_wrong_type_for_confirm_parameters_is_reported(self):
        problems = validate_confirm_envelope({"message": "Confermi?", "confirm_parameters": "non un dict"})
        self.assertTrue(any("'confirm_parameters'" in p for p in problems))

    def test_wrong_type_for_confirm_intent_is_reported(self):
        problems = validate_confirm_envelope({"message": "Confermi?", "confirm_parameters": {}, "confirm_intent": 42})
        self.assertTrue(any("'confirm_intent'" in p for p in problems))

    def test_empty_dict_reports_both_missing_fields(self):
        problems = validate_confirm_envelope({})
        self.assertEqual(len(problems), 2)

    def test_never_raises_on_odd_input(self):
        for value in (None, 42, "stringa", 3.14, object()):
            problems = validate_confirm_envelope(value)
            self.assertIsInstance(problems, list)
            self.assertTrue(problems)


if __name__ == "__main__":
    unittest.main()
