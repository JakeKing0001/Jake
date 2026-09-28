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


class SlowTurnNoticeTests(FakeOllamaCase):
    def test_after_a_few_seconds_the_hud_says_what_the_turn_is_waiting_for(self):
        from core.event_bus import EventBus
        from core.hud_protocol import EventType

        _FakeOllama.mode = "slow"
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ask = AskQuestionSkill(base_url=self.url, timeout=5)
        core = _bare_core(ledger_path=Path(tmp.name) / "ledger.jsonl", skill_registry=FakeRegistry({"ASK_QUESTION": ask}),
                          router=FakeRouter(Command("ASK_QUESTION", {"question": "cos'e' una CPU?"})),
                          event_bus=EventBus())
        core.SLOW_TURN_NOTICE_S = 0.5
        events = core.event_bus.subscribe()
        core.answer("cos'e' una CPU?")
        statuses = []
        while not events.empty():
            event = events.get_nowait()
            if event.type == EventType.THINKING:
                statuses.append(event.payload.get("status"))
        self.assertIn("Sto aspettando il modello locale...", statuses)


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

    def test_an_unsupported_reduced_format_falls_back_to_float16_on_the_gpu(self):
        from unittest import mock

        from core.voice.stt_provider import WhisperSttProvider

        model = mock.MagicMock(side_effect=[ValueError("Requested int8_float16 compute type, but the target device "
                                                       "or backend do not support efficient int8_float16 computation."),
                                            mock.MagicMock()])
        with mock.patch.dict("sys.modules", {"faster_whisper": mock.MagicMock(WhisperModel=model)}), \
                mock.patch("core.voice.stt_provider._gpu_total_vram_mb", return_value=8188):
            provider = WhisperSttProvider(device="cuda")
        self.assertEqual((provider.device, provider.compute_type), ("cuda", "float16"))


class RecoverableTranscriptTests(unittest.TestCase):
    """Seconda prova reale (27/09/2026, 23:26): corrotta -> chiedere; imperfetta ma chiara -> correggere con prudenza.
    Esempi veri di Jake (training/intents.jsonl), core reale, router che registra la frase che riceve."""

    def _core(self):
        from core.nlu.examples import ExampleStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        routed = self.routed = []

        class Router(FakeRouter):
            def detect_intent(self, text):
                routed.append(text)
                return Command("GET_TIME", {}) if text == "che ore sono" else Command("ASK_QUESTION", {"question": text})

        self.time, self.ask = FakeSkill(), FakeSkill()
        return _bare_core(ledger_path=Path(tmp.name) / "ledger.jsonl", router=Router(),
                          skill_registry=FakeRegistry({"GET_TIME": self.time, "ASK_QUESTION": self.ask}),
                          example_store=ExampleStore(learned_path=Path(tmp.name) / "learned.jsonl"))

    def _say(self, core, text, confidence):
        token = set_current_stt_confidence(confidence)
        try:
            return core.answer(text)
        finally:
            reset_current_stt_confidence(token)

    def test_a_command_repeated_in_one_utterance_counts_once_on_the_exact_lane(self):
        """Prova reale del 28/09: "Jake, che ore sono? Jake, che ore sono? Jake." finiva al modello (GPU piena)."""
        core = self._core()
        self._say(core, "che ore sono? Jake, che ore sono? Jake.", 0.69)
        self.assertEqual(self.routed, ["che ore sono"])
        self.assertEqual(len(self.time.calls), 1)

    def test_room_chatter_after_a_read_only_command_does_not_hide_it(self):
        core = self._core()
        self._say(core, "che ore sono? Allora, è una vostra storica in realtà.", 0.9)
        self.assertEqual(self.routed, ["che ore sono"])

    def test_an_action_never_loses_part_of_the_request(self):
        from core.nlu.examples import ExampleStore
        from core.nlu.transcript_repair import first_exact_clause
        from core.risk import RiskLevel, risk_of

        store = ExampleStore(learned_path=Path(tempfile.mkdtemp()) / "learned.jsonl")
        action = next(e for e in store.all() if risk_of(e.intent) != RiskLevel.READ_ONLY and not e.parameters
                      and "." not in e.text and "?" not in e.text)
        read_only = lambda intent: risk_of(intent) == RiskLevel.READ_ONLY  # noqa: E731
        self.assertIsNone(first_exact_clause(f"{action.text}. e poi un'altra cosa", store.find_exact, read_only),
                          action.text)
        self.assertEqual(first_exact_clause(f"{action.text}. {action.text}", store.find_exact, read_only), action.text,
                         "ripetuta identica: una volta sola")

    def test_a_fused_word_in_a_harmless_command_is_repaired(self):
        core = self._core()
        self._say(core, "chiore sono", 0.66)
        self.assertEqual(self.routed, ["che ore sono"])
        self.assertEqual(len(self.time.calls), 1)

    def test_a_clear_question_with_one_foreign_looking_word_is_answered_about_the_right_thing(self):
        core = self._core()
        reply = self._say(core, "che cosa è un prozessor", 0.48)  # rifiutata nella prova reale: 0.48 < 0.50
        self.assertNotEqual(reply, core.UNCLEAR_REPLY)
        self.assertEqual(self.routed, ["che cosa è un processore"])

    def test_strongly_corrupted_or_garbled_uncertain_speech_still_asks_to_repeat(self):
        core = self._core()
        for text, confidence in (("cosaem procesora", 0.44), ("gerizono", 0.42), ("direi io le sono non", 0.4)):
            self.assertEqual(self._say(core, text, confidence), core.UNCLEAR_REPLY, text)
        self.assertEqual(self.routed, [], "nessun significato inventato")

    def test_no_fuzzy_shortcut_towards_a_sensitive_command(self):
        from core.nlu.examples import ExampleStore
        from core.nlu.transcript_repair import REPAIRED, TranscriptRepair
        from core.risk import RiskLevel, risk_of

        store = ExampleStore(learned_path=Path(tempfile.mkdtemp()) / "learned.jsonl")
        verdict = TranscriptRepair.from_examples(store.all()).assess(
            "spegni il pz", 0.7, store.find_exact, lambda intent: risk_of(intent) == RiskLevel.READ_ONLY)
        self.assertNotEqual(verdict.verdict, REPAIRED)
        self.assertEqual(verdict.text, "spegni il pz", "decide il router, e il PolicyEngine chiede conferma")


class HudStateChainTests(unittest.TestCase):
    """Seconda prova reale del 27/09/2026: "gli stati visivi non funzionano". Lo stato della sessione vocale non
    arrivava mai all'HUD nativo (solo al vecchio HUD PySide): l'orb restava in IDLE per quasi tutto il turno.
    Sessione vocale vera (STT/TTS finti), core vero, bus vero, e il riduttore di riferimento dell'HUD nativo
    (core/hud_view_state.py, stesse regole di HudEventReducer.cpp): la sequenza di stati che l'orb mostra."""

    def test_a_voice_command_goes_listening_transcribing_thinking_executing_speaking_idle(self):
        import json as _json
        from types import SimpleNamespace

        from core.event_bus import EventBus
        from core.hud_view_state import HudViewState
        from core.voice.wake_word_session import WakeWordSession
        from tests.voice_session_support import track

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bus = EventBus()
        core = _bare_core(ledger_path=Path(tmp.name) / "ledger.jsonl", event_bus=bus,
                          skill_registry=FakeRegistry({"GET_TIME": FakeSkill()}),
                          router=FakeRouter(Command("GET_TIME", {})))
        heard = iter(["Jake.", "che ore sono"])

        class Stt:
            def transcribe(self, utterance, sample_rate):
                return next(heard)

        class Tts:
            def speak(self, text):
                time.sleep(0.05)

            def stop(self):
                pass

        events = bus.subscribe()
        session = track(WakeWordSession(core, Stt(), Tts(), vad_listener=SimpleNamespace(
            on_level=None, muted=False, SAMPLE_RATE=16000, silence_frames_needed=23)))
        session._handle_utterance(object())   # "Jake." -> in ascolto del comando
        session._handle_utterance(object())   # il comando
        self.assertTrue(session.wait_for_commands(5))
        deadline = time.monotonic() + 3
        while session.state != "idle" and time.monotonic() < deadline:
            time.sleep(0.02)

        view, shown = HudViewState(), []
        while not events.empty():
            view.apply_line(events.get_nowait().to_json())
            if not shown or shown[-1] != view.state:
                shown.append(view.state)
        # IDLE fra EXECUTING e SPEAKING: la risposta testuale (JAKE_MESSAGE) un istante prima della voce, stesso thread;
        # l'HUD non la mostra (Main.qml aspetta prima di tornare a riposo)
        self.assertEqual(shown, ["IDLE", "LISTENING", "TRANSCRIBING", "THINKING", "EXECUTING", "IDLE", "SPEAKING", "IDLE"],
                         _json.dumps(shown))
