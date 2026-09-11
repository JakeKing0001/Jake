"""Test unitari per skills/personal_utils.py: nessuna suite esisteva finora, nessun bug trovato."""
import unittest
from unittest import mock

from skills.personal_utils import CalculateAgeSkill, CalculateBmiSkill, CalculateDiscountSkill, CalculateTipSkill


class CalculateBmiTests(unittest.TestCase):
    def test_missing_parameters_fails(self):
        result = CalculateBmiSkill().execute({"weight_kg": 70})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_zero_height_fails(self):
        result = CalculateBmiSkill().execute({"weight_kg": 70, "height_cm": 0})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_normal_weight_bmi(self):
        result = CalculateBmiSkill().execute({"weight_kg": 70, "height_cm": 175})
        self.assertTrue(result.success)
        self.assertEqual(result.data["bmi"], 22.9)
        self.assertEqual(result.data["category"], "normopeso")

    def test_an_obese_bmi(self):
        result = CalculateBmiSkill().execute({"weight_kg": 120, "height_cm": 170})
        self.assertEqual(result.data["category"], "obesita'")


class CalculateTipTests(unittest.TestCase):
    def test_missing_amount_fails(self):
        result = CalculateTipSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_defaults_to_ten_percent_and_one_person(self):
        result = CalculateTipSkill().execute({"amount": 100})
        self.assertTrue(result.success)
        self.assertEqual(result.data["tip"], 10.0)
        self.assertEqual(result.data["total"], 110.0)
        self.assertEqual(result.data["people"], 1)

    def test_splits_between_people(self):
        result = CalculateTipSkill().execute({"amount": 100, "percent": 20, "people": 4})
        self.assertEqual(result.data["total"], 120.0)
        self.assertEqual(result.data["per_person"], 30.0)

    def test_zero_or_negative_people_falls_back_to_one(self):
        result = CalculateTipSkill().execute({"amount": 100, "people": 0})
        self.assertEqual(result.data["people"], 1)


class CalculateAgeTests(unittest.TestCase):
    def test_missing_birth_date_fails(self):
        result = CalculateAgeSkill().execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_invalid_date_fails(self):
        result = CalculateAgeSkill().execute({"birth_date": "non una data"})
        self.assertEqual(result.error, "INVALID_DATE")

    def test_a_birthday_already_passed_this_year(self):
        with mock.patch("skills.personal_utils.date") as fake_date:
            fake_date.today.return_value.year = 2026
            fake_date.today.return_value.month = 6
            fake_date.today.return_value.day = 15
            result = CalculateAgeSkill().execute({"birth_date": "01/01/2000"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["age"], 26)

    def test_a_birthday_not_yet_reached_this_year(self):
        with mock.patch("skills.personal_utils.date") as fake_date:
            fake_date.today.return_value.year = 2026
            fake_date.today.return_value.month = 1
            fake_date.today.return_value.day = 1
            result = CalculateAgeSkill().execute({"birth_date": "31/12/2000"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["age"], 25)


class CalculateDiscountTests(unittest.TestCase):
    def test_missing_parameters_fails(self):
        result = CalculateDiscountSkill().execute({"price": 100})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_known_discount(self):
        result = CalculateDiscountSkill().execute({"price": 100, "discount_percent": 25})
        self.assertTrue(result.success)
        self.assertEqual(result.data["savings"], 25.0)
        self.assertEqual(result.data["final_price"], 75.0)


if __name__ == "__main__":
    unittest.main()
