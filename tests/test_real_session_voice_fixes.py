"""Regressioni della prova reale del 27/09/2026 (HUD nativo + voce continua).

1. "Jake, io ero sono." (trascrizione rotta) era diventata una chiacchiera inventata: ora, con una confidenza STT bassa
   e nessun comando deterministico, Jake chiede di ripetere - senza chiamare il modello. "Che ore sono" resta veloce.
2. "Spiegami come e' fatto un processore": Ollama rispondeva ma era lentissimo (GPU contesa); il turno ha provato
   classificatore, agente e risposta diretta in cascata (quasi 3 minuti) e ha detto "non riesco a contattare Ollama".
   Ora il primo timeout vale per tutto il turno, il motivo e' distinto (lento / spento / modello mancante) e detto.
Finto Ollama HTTP locale controllabile, core reale."""
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import model_health
from core.command import Command
from core.ollama_client import OllamaClient, OllamaModelMissing, OllamaTimeout, OllamaUnavailable
from core.request_context import reset_current_stt_confidence, set_current_stt_confidence
from core.response_formatter import format_skill_result
from skills.ask_question import AskQuestionSkill
from tests.test_jake_core_pipeline import FakeExample, FakeExampleStore, FakeRegistry, FakeRouter, FakeSkill, _bare_core


class _FakeOllama(BaseHTTPRequestHandler):
    mode = "slow"          # "slow" | "missing" | "ok"
    chat_calls = 0

    def log_message(self, *args):
        pass

    def _json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/version":
            return self._json(200, {"version": "fake"})
        if self.path == "/api/ps":  # modello caricato solo in parte sulla GPU, come con la memoria video contesa
            return self._json(200, {"models": [{"name": "qwen2.5:7b", "size": 5_000_000_000, "size_vram": 3_000_000_000}]})
        return self._json(404, {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        type(self).chat_calls += 1
        if self.mode == "missing":
            return self._json(404, {"error": "model 'qwen2.5:7b' not found"})
        if self.mode == "slow":
            time.sleep(1.5)
        try:
            self._json(200, {"message": {"role": "assistant", "content": "Un processore esegue istruzioni."}})
        except OSError:
            pass


class FakeOllamaCase(unittest.TestCase):
    def setUp(self):
        _FakeOllama.chat_calls = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllama)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        token = model_health.begin_turn()
        self.addCleanup(model_health.end_turn, token)


class ModelFailureTests(FakeOllamaCase):
    def test_a_slow_model_is_a_timeout_with_a_cause_and_the_turn_does_not_wait_again(self):
        _FakeOllama.mode = "slow"
        client = OllamaClient(base_url=self.url, timeout=0.4)
        started = time.monotonic()
        with self.assertRaises(OllamaTimeout) as caught:
            client.chat("qwen2.5:7b", [{"role": "user", "content": "classifica"}])
        self.assertIn("solo in parte sulla GPU (60%)", str(caught.exception))
        # stesso turno: l'agente e la risposta diretta non ripetono l'attesa
        ask = AskQuestionSkill(base_url=self.url, timeout=5)
        result = ask.execute({"question": "spiegami come e' fatto un processore"})
        self.assertLess(time.monotonic() - started, 3.0, "nessuna cascata di attese")
        self.assertEqual(result.error, "MODEL_TIMEOUT")
        self.assertEqual(_FakeOllama.chat_calls, 1)
        reply = format_skill_result("ASK_QUESTION", result)
        self.assertIn("troppo lento", reply)
        self.assertIn("riprova", reply)
        self.assertNotIn("contattare", reply)

    def test_a_new_turn_starts_clean_and_a_healthy_model_answers(self):
        _FakeOllama.mode = "slow"
        with self.assertRaises(OllamaTimeout):
            OllamaClient(base_url=self.url, timeout=0.4).chat("qwen2.5:7b", [{"role": "user", "content": "x"}])
        token = model_health.begin_turn()
        try:
            _FakeOllama.mode = "ok"
            result = AskQuestionSkill(base_url=self.url, timeout=5).execute({"question": "cos'e' una CPU?"})
            self.assertTrue(result.success, result)
        finally:
            model_health.end_turn(token)

    def test_missing_model_and_server_off_are_told_apart(self):
        _FakeOllama.mode = "missing"
        with self.assertRaises(OllamaModelMissing):
            OllamaClient(base_url=self.url, timeout=2).chat("qwen2.5:7b", [{"role": "user", "content": "x"}])
        missing = AskQuestionSkill(base_url=self.url, timeout=2).execute({"question": "ciao?"})
        self.assertIn("ollama pull", format_skill_result("ASK_QUESTION", missing))

        self.server.shutdown()
        self.server.server_close()
        with self.assertRaises(OllamaUnavailable) as caught:
            OllamaClient(base_url=self.url, timeout=2).chat("qwen2.5:7b", [{"role": "user", "content": "x"}])
        self.assertNotIsInstance(caught.exception, OllamaTimeout)


class UnclearTranscriptTests(unittest.TestCase):
    def _core(self, exact=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.chitchat = FakeSkill()
        self.time = FakeSkill()
        return _bare_core(ledger_path=Path(tmp.name) / "ledger.jsonl",
                          skill_registry=FakeRegistry({"CHITCHAT": self.chitchat, "GET_TIME": self.time}),
                          router=FakeRouter(Command("CHITCHAT", {"text": "io ero sono"})),
                          example_store=FakeExampleStore(exact))

    def _say(self, core, text, confidence):
        token = set_current_stt_confidence(confidence)
        try:
            return core.answer(text)
        finally:
            reset_current_stt_confidence(token)

    def test_a_garbled_uncertain_phrase_asks_to_repeat_without_inventing_a_meaning(self):
        core = self._core()
        self.assertEqual(self._say(core, "io ero sono", 0.48), "Non ho capito bene, puoi ripetere?")
        self.assertEqual(self.chitchat.calls, [], "nessuna chiacchiera inventata")
        self._say(core, "io ero sono", 0.9)
        self.assertEqual(len(self.chitchat.calls), 1, "con una trascrizione sicura si procede normalmente")

    def test_the_deterministic_lane_stays_fast_even_with_a_modest_confidence(self):
        core = self._core(exact=FakeExample("GET_TIME", source="builtin"))
        core.router = FakeRouter(Command("GET_TIME", {}), route="exact")  # come la corsia esatta del Router vero
        self._say(core, "che ore sono", 0.4)
        self.assertEqual(len(self.time.calls), 1)

    def test_long_normal_speech_with_ordinary_confidence_is_not_rejected(self):
        core = self._core()
        self._say(core, "spiegami come e' fatto un processore moderno", 0.58)
        self.assertEqual(len(self.chitchat.calls), 1)


if __name__ == "__main__":
    unittest.main()


class WhisperGpuMemoryTests(unittest.TestCase):
    def test_on_a_shared_8gb_gpu_whisper_uses_half_the_memory(self):
        from core.voice.stt_provider import WhisperSttProvider

        # misurato qui: float16 2061 MB, int8_float16 1118 MB con large-v3-turbo
        self.assertEqual(WhisperSttProvider._cuda_compute_type(8188), "int8_float16")
        self.assertEqual(WhisperSttProvider._cuda_compute_type(24576), "float16")
        self.assertEqual(WhisperSttProvider._cuda_compute_type(None) in ("float16", "int8_float16"), True)
