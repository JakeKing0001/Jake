"""Test unitari per la ricerca file su NEST (v4.5, NEST Deep Integration): la logica condivisa
in core/nest_search.py, e HYBRID_SEARCH_FILES (skills/hybrid_search_files.py) che la usa per
esporre NestClient.hybrid_search, prima implementato ma mai richiamato da nessuna skill.
Nessuna vera CLI di NEST: un client finto."""
import unittest

from core.nest_client import NestError, NestSearchResult
from core.nest_search import run_nest_search
from skills.hybrid_search_files import HybridSearchFilesSkill
from skills.search_files import SearchFilesSkill
from skills.semantic_search_files import SemanticSearchFilesSkill


class FakeConversationState:
    def __init__(self):
        self.last_results = None

    def set_last_search_results(self, results):
        self.last_results = results


class FakeNestClient:
    def __init__(self, available=True, results=None, error=None):
        self._available = available
        self.results = results if results is not None else []
        self.error = error
        self.calls = {"search": 0, "semantic_search": 0, "hybrid_search": 0}

    def is_available(self):
        return self._available

    def _call(self, name, query):
        self.calls[name] += 1
        if self.error is not None:
            raise self.error
        return self.results

    def search(self, query):
        return self._call("search", query)

    def semantic_search(self, query):
        return self._call("semantic_search", query)

    def hybrid_search(self, query):
        return self._call("hybrid_search", query)


class RunNestSearchTests(unittest.TestCase):
    def test_unavailable_client_is_reported(self):
        client = FakeNestClient(available=False)
        result = run_nest_search(client, FakeConversationState(), "tesi", client.search)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NEST_UNAVAILABLE")

    def test_nest_error_is_reported(self):
        client = FakeNestClient(error=NestError("boom"))
        result = run_nest_search(client, FakeConversationState(), "tesi", client.search)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NEST_ERROR")

    def test_no_results_is_not_found(self):
        client = FakeNestClient(results=[])
        result = run_nest_search(client, FakeConversationState(), "tesi", client.search)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")

    def test_results_are_structured_and_remembered(self):
        client = FakeNestClient(results=[NestSearchResult(path="tesi.pdf", snippet="...", score=0.9)])
        state = FakeConversationState()

        result = run_nest_search(client, state, "tesi", client.search)

        self.assertTrue(result.success)
        self.assertEqual(result.data["results"], [{"path": "tesi.pdf", "snippet": "...", "score": 0.9}])
        self.assertEqual(state.last_results, result.data["results"])


class HybridSearchFilesSkillTests(unittest.TestCase):
    def test_calls_hybrid_search_specifically(self):
        client = FakeNestClient(results=[NestSearchResult(path="a.txt", snippet="", score=0.5)])
        skill = HybridSearchFilesSkill(client, FakeConversationState())

        result = skill.execute({"query": "appunti"})

        self.assertTrue(result.success)
        self.assertEqual(client.calls, {"search": 0, "semantic_search": 0, "hybrid_search": 1})

    def test_missing_query(self):
        skill = HybridSearchFilesSkill(FakeNestClient(), FakeConversationState())
        result = skill.execute({})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "MISSING_PARAMETERS")


class ExistingSkillsStillUseTheirOwnMethodTests(unittest.TestCase):
    """Il refactor verso la logica condivisa non deve far scambiare i metodi tra le skill."""

    def test_search_files_uses_search_not_hybrid(self):
        client = FakeNestClient(results=[NestSearchResult(path="a.txt", snippet="", score=0.5)])
        SearchFilesSkill(client, FakeConversationState()).execute({"query": "a"})
        self.assertEqual(client.calls, {"search": 1, "semantic_search": 0, "hybrid_search": 0})

    def test_semantic_search_files_uses_semantic_search(self):
        client = FakeNestClient(results=[NestSearchResult(path="a.txt", snippet="", score=0.5)])
        SemanticSearchFilesSkill(client, FakeConversationState()).execute({"query": "a"})
        self.assertEqual(client.calls, {"search": 0, "semantic_search": 1, "hybrid_search": 0})


if __name__ == "__main__":
    unittest.main()
