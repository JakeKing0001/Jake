"""Test unitari per skills/build_semantic_index.py: nessuna suite esisteva finora, nessun bug
trovato. nest_client e' sempre un MagicMock (mai una vera chiamata a NEST)."""
import unittest
from unittest import mock

from core.nest_client import NestError
from skills.build_semantic_index import BuildSemanticIndexSkill


class BuildSemanticIndexTests(unittest.TestCase):
    def test_nest_unavailable_fails(self):
        nest_client = mock.MagicMock()
        nest_client.is_available.return_value = False
        result = BuildSemanticIndexSkill(nest_client).execute({})
        self.assertEqual(result.error, "NEST_UNAVAILABLE")
        nest_client.build_semantic_index.assert_not_called()

    def test_a_successful_build_returns_the_summary(self):
        nest_client = mock.MagicMock()
        nest_client.is_available.return_value = True
        nest_client.build_semantic_index.return_value = "1200 file indicizzati."
        result = BuildSemanticIndexSkill(nest_client).execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["summary"], "1200 file indicizzati.")

    def test_a_nest_error_is_reported(self):
        nest_client = mock.MagicMock()
        nest_client.is_available.return_value = True
        nest_client.build_semantic_index.side_effect = NestError("indice corrotto")
        result = BuildSemanticIndexSkill(nest_client).execute({})
        self.assertEqual(result.error, "NEST_ERROR")
        self.assertEqual(result.data["message"], "indice corrotto")


if __name__ == "__main__":
    unittest.main()
