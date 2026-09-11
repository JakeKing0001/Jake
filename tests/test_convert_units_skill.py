"""Test unitari per skills/convert_units.py: nessuna suite esisteva finora, nessun bug trovato."""
import unittest

from skills.convert_units import ConvertUnitsSkill


class ConvertUnitsTests(unittest.TestCase):
    def test_missing_parameters_fails(self):
        result = ConvertUnitsSkill().execute({"value": 1, "from_unit": "km"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_unknown_unit_fails(self):
        result = ConvertUnitsSkill().execute({"value": 1, "from_unit": "km", "to_unit": "boh"})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_length_conversion(self):
        result = ConvertUnitsSkill().execute({"value": 1, "from_unit": "km", "to_unit": "m"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["result"], 1000.0)

    def test_weight_conversion_with_italian_alias(self):
        result = ConvertUnitsSkill().execute({"value": 2, "from_unit": "chili", "to_unit": "g"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["result"], 2000.0)

    def test_temperature_conversion(self):
        result = ConvertUnitsSkill().execute({"value": 0, "from_unit": "celsius", "to_unit": "fahrenheit"})
        self.assertTrue(result.success)
        self.assertEqual(result.data["result"], 32.0)

    def test_incompatible_categories_fail(self):
        result = ConvertUnitsSkill().execute({"value": 1, "from_unit": "km", "to_unit": "kg"})
        self.assertEqual(result.error, "INCOMPATIBLE_UNITS")


if __name__ == "__main__":
    unittest.main()
