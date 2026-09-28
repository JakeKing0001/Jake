"""Prova reale del 28/09/2026: con la GPU piena (55 MB liberi) ogni turno nuovo ripagava i ~25 s del classificatore
d'intenti prima di ripiegare sulle regole - "che ore sono?" con una coda confusa ha risposto dopo 28 s. Dopo un
timeout del modello, e finche' il modello non risponde di nuovo, l'instradamento usa un'attesa corta."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core import model_health
from core.nlu.llm_classifier import OllamaProvider
from core.ollama_client import OllamaClient
from tests.test_llm_classifier_history import FakeRegistry, RecordingClient

SLOW = model_health.ModelFailure(model_health.TIMEOUT, "Ollama risponde ma il modello e' troppo lento")


class _RecordingTimeoutClient(RecordingClient):
    def chat(self, model, messages, format=None, options=None, timeout=None):
        self.last_timeout = timeout
        return super().chat(model, messages, format, options, timeout)


class _OllamaStub(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps({"message": {"content": "ok"}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class RoutingAfterTimeoutTests(unittest.TestCase):
    def setUp(self):
        model_health.succeeded()
        self.addCleanup(model_health.succeeded)

    def test_after_a_timeout_routing_waits_briefly_until_the_model_answers_again(self):
        self.assertEqual(model_health.routing_timeout(25), 25)
        model_health.record(SLOW)   # fuori da un turno: vale per i turni successivi
        self.assertEqual(model_health.routing_timeout(25), model_health.SHORT_ROUTING_TIMEOUT_S)
        model_health.succeeded()
        self.assertEqual(model_health.routing_timeout(25), 25)

    def test_the_short_wait_expires_by_itself(self):
        import time

        model_health.record(SLOW)
        later = time.monotonic() + model_health.RECENT_TIMEOUT_S + 1
        self.assertEqual(model_health.routing_timeout(25, now=later), 25)

    def test_a_server_that_is_off_does_not_shorten_anything(self):
        model_health.record(model_health.ModelFailure(model_health.OFFLINE))
        self.assertEqual(model_health.routing_timeout(25), 25, "connessione rifiutata: fallisce gia' subito")

    def test_the_intent_classifier_passes_the_short_wait_to_the_model(self):
        client = _RecordingTimeoutClient({"intent": "GET_WEATHER", "parameters": {"city": "Roma"}})
        provider = OllamaProvider(FakeRegistry(), client=client)

        provider.detect_intent("che tempo fa a Roma")
        self.assertEqual(client.last_timeout, provider.timeout)
        model_health.record(SLOW)
        provider.detect_intent("che tempo fa a Roma")
        self.assertEqual(client.last_timeout, model_health.SHORT_ROUTING_TIMEOUT_S)

    def test_a_real_answer_from_the_model_ends_the_short_wait(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaStub)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        model_health.record(SLOW)

        OllamaClient(base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=5).chat("m", [])

        self.assertEqual(model_health.routing_timeout(25), 25)


if __name__ == "__main__":
    unittest.main()
