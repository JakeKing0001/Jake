"""Test unitari per plugins/example_coin_flip.py: nessuna suite esisteva finora, nessun bug
trovato. E' il plugin di esempio del sistema Skill Forge (caricato in automatico all'avvio come
qualunque altro file in plugins/), non solo un file usa e getta: vale la stessa copertura minima
di qualunque skill reale."""
import unittest
from unittest import mock

from plugins.example_coin_flip import CoinFlipSkill, register


class CoinFlipSkillTests(unittest.TestCase):
    def test_execute_returns_one_of_the_two_outcomes(self):
        result = CoinFlipSkill().execute({})
        self.assertTrue(result.success)
        self.assertIn(result.data["result"], ["testa", "croce"])

    def test_format_result_mentions_the_outcome(self):
        skill = CoinFlipSkill()
        with mock.patch("random.choice", return_value="testa"):
            result = skill.execute({})
        self.assertEqual(skill.format_result(result), "È uscito testa!")


class RegisterTests(unittest.TestCase):
    def test_register_adds_the_coin_flip_skill(self):
        registry = mock.MagicMock()
        register(registry)
        registry.register_skill.assert_called_once()
        intent, skill = registry.register_skill.call_args.args
        self.assertEqual(intent, "COIN_FLIP")
        self.assertIsInstance(skill, CoinFlipSkill)


if __name__ == "__main__":
    unittest.main()
