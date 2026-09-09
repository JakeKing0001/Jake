"""Test unitari per core/embedding_provider.py. cosine_similarity() e' la funzione su cui si
basa tutto il dedup semantico e il recupero per similarita' di F5 (core/memory_manager.py,
costruito/esteso in questa sessione) - un bug qui avrebbe minato silenziosamente tutto quel
lavoro. embed() mocka urllib.request.urlopen, stesso confine gia' usato per HomeAssistantClient/
NestClient/OllamaClient (vedi tests/test_home_assistant_client.py): nessuna vera chiamata a
Ollama in un test automatico."""
import json
import unittest
from unittest import mock
from urllib import error

from core.embedding_provider import EmbeddingProvider


def _fake_response(payload) -> mock.MagicMock:
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    response = mock.MagicMock()
    response.read.return_value = body
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class CosineSimilarityTests(unittest.TestCase):
    def test_identical_vectors_have_similarity_one(self):
        self.assertAlmostEqual(EmbeddingProvider.cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]), 1.0)

    def test_orthogonal_vectors_have_similarity_zero(self):
        self.assertAlmostEqual(EmbeddingProvider.cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_opposite_vectors_have_similarity_minus_one(self):
        self.assertAlmostEqual(EmbeddingProvider.cosine_similarity([1.0, 2.0], [-1.0, -2.0]), -1.0)

    def test_scale_does_not_affect_similarity(self):
        """Il coseno e' invariante alla scala: un vettore e il suo doppio hanno similarita' 1."""
        self.assertAlmostEqual(EmbeddingProvider.cosine_similarity([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]), 1.0)

    def test_empty_vectors_return_zero_not_a_crash(self):
        self.assertEqual(EmbeddingProvider.cosine_similarity([], []), 0.0)
        self.assertEqual(EmbeddingProvider.cosine_similarity(None, [1.0]), 0.0)
        self.assertEqual(EmbeddingProvider.cosine_similarity([1.0], None), 0.0)

    def test_mismatched_lengths_return_zero_not_a_crash(self):
        self.assertEqual(EmbeddingProvider.cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0]), 0.0)

    def test_zero_vector_returns_zero_not_a_division_by_zero(self):
        self.assertEqual(EmbeddingProvider.cosine_similarity([0.0, 0.0], [1.0, 2.0]), 0.0)
        self.assertEqual(EmbeddingProvider.cosine_similarity([0.0, 0.0], [0.0, 0.0]), 0.0)


class EmbedTests(unittest.TestCase):
    def test_successful_response_returns_the_embedding_vector(self):
        provider = EmbeddingProvider()

        with mock.patch("urllib.request.urlopen", return_value=_fake_response({"embeddings": [[0.1, 0.2, 0.3]]})):
            result = provider.embed("qualche testo")

        self.assertEqual(result, [0.1, 0.2, 0.3])

    def test_sends_the_model_and_input_in_the_request_body(self):
        provider = EmbeddingProvider(model="nomic-embed-text")

        with mock.patch(
            "urllib.request.urlopen", return_value=_fake_response({"embeddings": [[0.1]]}),
        ) as urlopen:
            provider.embed("ciao")

        sent_request = urlopen.call_args[0][0]
        body = json.loads(sent_request.data.decode("utf-8"))
        self.assertEqual(body["model"], "nomic-embed-text")
        self.assertEqual(body["input"], "ciao")

    def test_missing_embeddings_field_returns_none(self):
        provider = EmbeddingProvider()

        with mock.patch("urllib.request.urlopen", return_value=_fake_response({})):
            self.assertIsNone(provider.embed("x"))

    def test_empty_embeddings_list_returns_none(self):
        provider = EmbeddingProvider()

        with mock.patch("urllib.request.urlopen", return_value=_fake_response({"embeddings": []})):
            self.assertIsNone(provider.embed("x"))

    def test_malformed_embeddings_shape_returns_none_not_a_crash(self):
        """embeddings presente ma non una lista di liste (es. Ollama cambia formato, o risponde
        con un errore strutturato inatteso): non deve sollevare, solo degradare a None."""
        provider = EmbeddingProvider()

        with mock.patch("urllib.request.urlopen", return_value=_fake_response({"embeddings": "non una lista"})):
            self.assertIsNone(provider.embed("x"))
        with mock.patch("urllib.request.urlopen", return_value=_fake_response({"embeddings": [0.1, 0.2]})):
            self.assertIsNone(provider.embed("x"))

    def test_connection_failure_returns_none(self):
        provider = EmbeddingProvider()

        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("connessione rifiutata")):
            self.assertIsNone(provider.embed("x"))

    def test_timeout_returns_none(self):
        provider = EmbeddingProvider()

        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            self.assertIsNone(provider.embed("x"))

    def test_invalid_json_response_returns_none(self):
        provider = EmbeddingProvider()
        response = mock.MagicMock()
        response.read.return_value = b"non e' json valido"
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with mock.patch("urllib.request.urlopen", return_value=response):
            self.assertIsNone(provider.embed("x"))


if __name__ == "__main__":
    unittest.main()
