"""Stress deterministico della cancellazione del turno vocale ("Jake, basta" -> comando successivo).

Ogni iterazione: un primo comando resta bloccato dentro un componente VERO (client Ollama, visione,
skill dal registro, TaskAgent, PlanExecutor, oppure la voce di Jake che sta parlando), l'utente dice
"Jake, basta" e subito dopo un secondo comando. Poi si verificano, per ogni iterazione:

1. il secondo comando e' eseguito esattamente una volta;
2. mai due `JakeCore.answer()` insieme;
3. il risultato del primo turno e' scartato (mai pronunciato);
4. nessun effetto tardivo del primo turno, neanche dopo che le chiamate bloccate si sbloccano;
5. il KillSwitch globale resta spento;
6. nessuna perdita di ContextVar (evento di cancellazione, parlante, confidenza STT) fuori dal turno;
7. parlante e confidenza STT del secondo turno sono i suoi, non quelli del primo;
8. a fine iterazione nessun thread nato durante l'iterazione e' ancora vivo.

Finti soltanto microfono, altoparlanti, rete (urlopen) e schermo; nessun Ollama, nessun hardware.
I log di produzione vanno in una cartella temporanea e i componenti girano in modalita' privata:
nessuna scrittura in data/.

    python -m benchmarks.bench_turn_cancellation --iterations 102
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

SCENARIOS = ("model", "vision", "skill", "task_agent", "plan", "tts")
STOP_ACK = "Va bene, annullo."
SECOND_ANSWER = "Sono le dieci."
FIRST_ANSWER = "risposta del task lungo"
FIRST_COMMAND = "trova il pulsante blu e cliccalo"
SECOND_COMMAND = "che ore sono"
FIRST_TURN = ("davide", 0.91)
SECOND_TURN = (None, 0.42)
THREAD_SETTLE_SECONDS = 3.0


def _json_response(payload):
    response = mock.MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


@dataclass
class _Iteration:
    scenario: str
    tmp: Path
    release: threading.Event = field(default_factory=threading.Event)
    effects: list = field(default_factory=list)
    checks: list = field(default_factory=list)

    def blocking_urlopen(self, payload):
        def urlopen(*args, **kwargs):
            self.release.wait(10)
            return _json_response(payload)
        return urlopen


class _StressCore:
    """JakeCore ridotto a cio' che conta qui: il primo comando esegue l'azione lunga dello scenario,
    gli altri rispondono subito. Come `JakeCore._answer_inner`, un turno annullato non consegna nulla
    (raise_if_cancelled dopo il lavoro). Registra il contesto visto da ogni answer()."""

    EXIT_SENTINEL = "__exit__"

    def __init__(self, first_action, unwind_delay: float):
        from core.kill_switch import KillSwitch

        self.conversation_state = SimpleNamespace(has_pending_action=lambda: False)
        self.kill_switch = KillSwitch()
        self.first_action = first_action
        self.unwind_delay = unwind_delay
        self.started = threading.Event()
        self.calls: list[str] = []
        self.contexts: list[tuple] = []
        self.completed: list[str] = []
        self.first_exited_at: float | None = None
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def answer(self, text):
        from core.request_context import current_speaker_profile_id, current_stt_confidence
        from core.turn_cancellation import current_turn_cancel_event, raise_if_cancelled

        text = text.strip(" ?!.").lower()
        event = current_turn_cancel_event()
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append(text)
            self.contexts.append((text, current_speaker_profile_id(), current_stt_confidence(),
                                  event is not None and not event.is_set()))
            first = len(self.calls) == 1
        try:
            if first:
                self.started.set()
                try:
                    if self.first_action is None:  # scenario tts: la risposta e' pronta subito
                        return FIRST_ANSWER
                    try:
                        self.first_action()
                    finally:
                        self.first_exited_at = time.perf_counter()
                    raise_if_cancelled()
                    self.completed.append(text)
                    return FIRST_ANSWER
                finally:
                    time.sleep(self.unwind_delay)  # chiusura non interrompibile del turno
            if text == SECOND_COMMAND:
                return SECOND_ANSWER
            return f"eseguito: {text}"
        finally:
            with self._lock:
                self.active -= 1


class _BlockingTts:
    """Voce finta: la risposta lunga "parla" finche' stop() non la ferma (o fino a 10 s)."""

    def __init__(self):
        self.spoken: list[str] = []
        self.speaking_long = threading.Event()
        self.long_returned_at: float | None = None
        self._stop = threading.Event()

    def speak(self, text):
        self.spoken.append(text)
        if text == FIRST_ANSWER:
            self._stop.clear()
            self.speaking_long.set()
            self._stop.wait(10)
            self.long_returned_at = time.perf_counter()

    def stop(self):
        self._stop.set()


class _ScriptedStt:
    """Come WhisperSttProvider: testo E confidenza della frase appena detta."""

    def __init__(self):
        self.next_text = ""
        self.next_confidence = None

    def transcribe(self, audio, sample_rate):
        return self.next_text

    def transcribe_detailed(self, audio, sample_rate):
        return self.next_text, self.next_confidence


# ---- azioni lunghe, una per scenario ----------------------------------------------------------


def _model_action(it: _Iteration):
    from core.ollama_client import OllamaClient

    def action():
        OllamaClient(base_url="http://127.0.0.1:9").chat("m", [{"role": "user", "content": FIRST_COMMAND}])
        it.effects.append("model: risposta usata dopo il basta")
    patch = mock.patch("core.ollama_client.request.urlopen",
                       side_effect=it.blocking_urlopen({"message": {"content": "tardi"}}))
    return action, [patch]


def _vision_action(it: _Iteration):
    from core.vision_provider import VisionProvider
    from skills.screen_click import ClickElementSkill

    agent = mock.MagicMock()
    agent.locate_text.return_value = None
    agent.click_point.side_effect = lambda *a, **k: it.effects.append("vision: click")
    skill = ClickElementSkill(VisionProvider(), computer_agent=agent)
    image = mock.MagicMock(size=(800, 600))
    image.save.side_effect = lambda path: Path(path).write_bytes(b"\x89PNG fake")

    def action():
        result = skill.execute({"description": "il pulsante blu"})
        if result.success:
            it.effects.append("vision: skill riuscita dopo il basta")
    patches = [
        mock.patch("urllib.request.urlopen",
                   side_effect=it.blocking_urlopen({"message": {"content": '{"found": true, "x": 10, "y": 10}'}})),
        mock.patch("core.vision.screen.capture_screenshot_image", return_value=image),
        mock.patch("core.vision.screen.SCREENSHOTS_DIR", it.tmp),
    ]
    return action, patches


class _SlowNoteSkill:
    """Skill dal registro VERO: attende una risorsa lenta (interrompibile), poi produce l'effetto."""

    metadata = {"intent": "ADD_NOTE", "description": "x", "parameters": {}}

    def __init__(self, it: _Iteration):
        self.it = it

    def execute(self, parameters=None):
        from core.skill_result import SkillResult
        from core.turn_cancellation import cancellable_call

        cancellable_call(self.it.release.wait, 10)
        self.it.effects.append("skill: nota scritta dopo il basta")
        return SkillResult(success=True, data={})


def _bare_registry(skills: dict):
    from core.resource_lock import ResourceLockManager
    from core.skill_registry import SkillRegistry
    from core.action_snapshot import SnapshotStore

    registry = SkillRegistry.__new__(SkillRegistry)
    registry.skills = dict(skills)
    registry.logger = logging.getLogger("jake.bench.turn_cancellation")
    registry._forged_intents = {}
    registry._sandbox_worker = None
    registry._plugin_violation_counts = {}
    registry._quarantined_plugins = set()
    registry._resource_locks = ResourceLockManager()
    registry.snapshot_store = SnapshotStore()
    return registry


def _skill_action(it: _Iteration):
    from core.policy_engine import PolicyEngine

    registry = _bare_registry({"ADD_NOTE": _SlowNoteSkill(it)})

    def action():
        result = registry.execute("ADD_NOTE", {"text": "x"}, policy_engine=PolicyEngine(), private=True)
        if result.success:
            it.effects.append("skill: risultato consegnato dopo il basta")
    return action, []


class _FileRegistry:
    """CREATE_PATH/DELETE_PATH toccano davvero il disco (la verifica e la compensazione guardano lo
    stato reale); ADD_NOTE e' la skill lenta."""

    def __init__(self, it: _Iteration):
        self.it = it
        self.calls: list[str] = []

    def list_capabilities(self):
        return [{"intent": "ADD_NOTE", "description": "Aggiunge un appunto.",
                 "parameters": {"text": {"type": "string", "required": True, "description": "Testo."}}},
                {"intent": "CREATE_PATH", "description": "Crea un file.",
                 "parameters": {"path": {"type": "string", "required": True, "description": "Percorso."}}}]

    def execute(self, intent, parameters=None, policy_engine=None, *, action_id=None, private=False):
        from core.skill_result import SkillResult

        parameters = parameters or {}
        self.calls.append(intent)
        if intent == "CREATE_PATH":
            Path(parameters["path"]).touch()
            return SkillResult(success=True, data={"path": parameters["path"]})
        if intent == "DELETE_PATH":
            Path(parameters["path"]).unlink(missing_ok=True)
            return SkillResult(success=True, data={"path": parameters["path"]})
        if intent == "ADD_NOTE":
            return _SlowNoteSkill(self.it).execute(parameters)
        raise AssertionError(f"intent non atteso: {intent}")


class _ScriptedModel:
    """Primo passo: crea il file. Secondo: il modello non risponde finche' non viene rilasciato."""

    def __init__(self, it: _Iteration, target: Path):
        self.it = it
        self.target = target
        self.calls = 0

    def chat(self, model, messages, format=None, options=None, timeout=None):
        self.calls += 1
        if self.calls == 1:
            turn = {"thought": "Creo il file", "final_answer": "", "ask_user": "",
                    "action": {"intent": "CREATE_PATH", "parameters": {"path": str(self.target)}}}
        else:
            self.it.release.wait(10)
            turn = {"thought": "Poi una nota", "final_answer": "", "ask_user": "",
                    "action": {"intent": "ADD_NOTE", "parameters": {"text": "mai"}}}
        return {"message": {"content": json.dumps(turn)}}


def _task_agent_action(it: _Iteration):
    from core.action_ledger import ActionLedger
    from core.agent import TaskAgent
    from core.policy_engine import PolicyEngine

    target = it.tmp / "agente.txt"
    registry = _FileRegistry(it)

    class _Retriever:
        def retrieve(self, request, max_capabilities=22, max_examples=0):
            return SimpleNamespace(capabilities=[{"intent": "ADD_NOTE"}, {"intent": "CREATE_PATH"}])

    agent = TaskAgent(registry, _Retriever(), _ScriptedModel(it, target), model_provider=lambda: "m",
                      format_result=lambda intent, result: str(result.data), policy_engine=PolicyEngine(),
                      action_ledger=ActionLedger(it.tmp / "ledger.jsonl"))

    def action():
        outcome = agent.run("crea un file e poi una nota", private=True)
        if outcome.error != "CANCELLED":
            it.effects.append(f"task_agent: esito {outcome.error!r} invece di CANCELLED")

    def check():
        if target.exists():
            it.effects.append("task_agent: file del passo annullato non compensato")
        if "ADD_NOTE" in registry.calls:
            it.effects.append("task_agent: passo eseguito dopo il basta")
    it.checks.append(check)
    return action, []


def _plan_action(it: _Iteration):
    from core.action_ledger import ActionLedger
    from core.plan_executor import PlanExecutor
    from core.planner import Plan, PlanStep
    from core.policy_engine import PolicyEngine

    target = it.tmp / "piano.txt"
    registry = _FileRegistry(it)
    plan = Plan(steps=[
        PlanStep(intent="CREATE_PATH", parameters={"path": str(target)}),
        PlanStep(intent="ADD_NOTE", parameters={"text": "lenta"}),
        PlanStep(intent="ADD_NOTE", parameters={"text": "mai"}),
    ])
    executor = PlanExecutor(registry, action_ledger=ActionLedger(it.tmp / "ledger.jsonl"))

    def action():
        outcome = executor.execute(plan, policy_engine=PolicyEngine(), private=True)
        stopped = outcome.stopped_step
        if stopped is None or stopped.result.error != "CANCELLED":
            it.effects.append("plan: il piano non si e' fermato come CANCELLED")

    def check():
        if target.exists():
            it.effects.append("plan: file del passo annullato non compensato")
        if registry.calls.count("ADD_NOTE") > 1:
            it.effects.append("plan: passo successivo eseguito dopo il basta")
    it.checks.append(check)
    return action, []


_ACTIONS = {"model": _model_action, "vision": _vision_action, "skill": _skill_action,
            "task_agent": _task_agent_action, "plan": _plan_action}


# ---- una iterazione ---------------------------------------------------------------------------


def _hear(session, stt, clock, text, confidence=None):
    clock["t"] += 2.0  # una frase detta dura piu' del cooldown anti-doppia-attivazione (1,5 s)
    stt.next_text, stt.next_confidence = text, confidence
    session._handle_utterance(np.zeros(320, dtype=np.float32))


def _wait(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def run_iteration(scenario: str, rng: random.Random) -> dict:
    from core.request_context import current_speaker_profile_id, current_stt_confidence
    from core.turn_cancellation import current_turn_cancel_event
    from core.voice.speaker_profile import SpeakerHint
    from core.voice.wake_word_session import WakeWordSession

    tmp = Path(tempfile.mkdtemp(prefix="jake_bench_cancel_"))
    it = _Iteration(scenario, tmp)
    threads_before = set(threading.enumerate())
    stop_delay = rng.uniform(0.0, 0.04)
    unwind_delay = rng.choice((0.0, 0.0, 0.05, 0.15)) if scenario != "tts" else 0.0
    failures: list[str] = []
    patches = []
    session = None
    started = time.monotonic()
    try:
        if scenario == "tts":
            action = None
        else:
            action, patches = _ACTIONS[scenario](it)
        for patch in patches:
            patch.start()
        core = _StressCore(action, unwind_delay)
        stt, tts = _ScriptedStt(), _BlockingTts()
        vad = SimpleNamespace(on_level=None, muted=False, SAMPLE_RATE=16000)
        hints = {1: SpeakerHint(profile_id=FIRST_TURN[0], confidence="high"),
                 2: SpeakerHint(profile_id=None, confidence="none")}
        identify_calls = {"n": 0}

        def identify(features, profiles):
            identify_calls["n"] += 1
            return hints.get(identify_calls["n"], SpeakerHint(profile_id=None, confidence="none"))

        with mock.patch("core.voice.wake_word_session.extract_features", return_value=object()), \
                mock.patch("core.voice.wake_word_session.identify", side_effect=identify):
            session = WakeWordSession(core, stt, tts, vad_listener=vad, speaker_store=mock.MagicMock(),
                                      follow_up_seconds=0)
            clock = {"t": 1000.0}
            session.wake_cooldown._clock = lambda: clock["t"]
            spoken: list[str] = []
            original_respond = session._respond

            def respond(text):
                spoken.append(text)
                original_respond(text)
            session._respond = respond

            _hear(session, stt, clock, f"Jake, {FIRST_COMMAND}", confidence=FIRST_TURN[1])
            if not core.started.wait(3):
                failures.append("il primo turno non e' partito")
            if scenario == "tts" and not tts.speaking_long.wait(3):
                failures.append("la risposta lunga non ha iniziato a parlare")
            time.sleep(stop_delay)
            stop_at = time.perf_counter()
            _hear(session, stt, clock, "Jake, basta")
            _hear(session, stt, clock, f"Jake, {SECOND_COMMAND}?", confidence=SECOND_TURN[1])
            if not session.wait_for_commands(5):
                failures.append("un turno e' rimasto appeso")
            # la voce della seconda risposta finisce da sola (non e' la risposta lunga)
            _wait(lambda: session._tts_thread is None or not session._tts_thread.is_alive(), 3)

        # le chiamate bloccate del primo turno ora "arrivano": nessun effetto deve seguirne
        it.release.set()
        tts.stop()
        _wait(lambda: not (set(threading.enumerate()) - threads_before), THREAD_SETTLE_SECONDS)
        for check in it.checks:
            check()

        # latenza vera dello stop: dal "basta" a quando il lavoro bloccato (o la voce) viene abbandonato
        stopped_at = tts.long_returned_at if scenario == "tts" else core.first_exited_at
        cancel_latency = (stopped_at - stop_at) if stopped_at is not None else float("inf")
        if cancel_latency > 0.5:
            failures.append(f"stop lento: {cancel_latency * 1000:.0f} ms")
        expected_calls = [FIRST_COMMAND, SECOND_COMMAND]
        if core.calls != expected_calls:
            failures.append(f"comandi eseguiti {core.calls} invece di {expected_calls}")
        if core.max_active != 1:
            failures.append(f"{core.max_active} answer() concorrenti")
        # tts: la risposta era gia' consegnata, "basta" ferma solo la voce (nessun "annullo", mai ripetuta)
        expected_spoken = [FIRST_ANSWER, SECOND_ANSWER] if scenario == "tts" else [STOP_ACK, SECOND_ANSWER]
        if spoken != expected_spoken:
            failures.append(f"risposte {spoken} invece di {expected_spoken}")
        if tts.spoken != expected_spoken:
            failures.append(f"voce {tts.spoken} invece di {expected_spoken}")
        if core.completed:
            failures.append("il primo turno ha consegnato un risultato dopo il basta")
        failures.extend(it.effects)
        if core.kill_switch.is_active():
            failures.append("KillSwitch attivato da un basta")
        if current_turn_cancel_event() is not None or current_speaker_profile_id() is not None \
                or current_stt_confidence() is not None:
            failures.append("ContextVar del turno visibile fuori dal turno")
        second = [c for c in core.contexts if c[0] == SECOND_COMMAND]
        if not second or second[0][1:] != (*SECOND_TURN, True):
            failures.append(f"contesto del secondo turno sbagliato: {second}")
        first = core.contexts[0] if core.contexts else None
        if first is None or first[1:3] != FIRST_TURN:
            failures.append(f"contesto del primo turno sbagliato: {first}")
        if vad.muted:
            failures.append("microfono rimasto in mute dopo la fine della voce")
        if session.state not in ("idle", "listening"):
            failures.append(f"stato finale {session.state!r}")
        leaked = [t.name for t in set(threading.enumerate()) - threads_before if t.is_alive()]
        if leaked:
            failures.append(f"thread ancora vivi: {sorted(leaked)}")
        return {"scenario": scenario, "ok": not failures, "failures": failures,
                "cancel_ms": round(cancel_latency * 1000, 1), "stop_delay_ms": round(stop_delay * 1000, 1),
                "unwind_ms": round(unwind_delay * 1000), "total_s": round(time.monotonic() - started, 3)}
    finally:
        it.release.set()
        for patch in reversed(patches):
            patch.stop()
        if session is not None:
            session.wait_for_commands(5)
        shutil.rmtree(tmp, ignore_errors=True)


def run(iterations: int = 102, seed: int = 20260926) -> dict:
    """Esegue `iterations` iterazioni ripartite a rotazione fra gli scenari."""
    import core.action_ledger
    import core.logger
    import core.session_recorder

    rng = random.Random(seed)
    log_dir = Path(tempfile.mkdtemp(prefix="jake_bench_cancel_logs_"))
    saved = (core.logger.DEFAULT_LOG_PATH, core.logger.DEFAULT_ACTION_LOG_PATH,
             core.session_recorder.DEFAULT_PATH, core.action_ledger.DEFAULT_LEDGER_PATH)
    core.logger.DEFAULT_LOG_PATH = log_dir / "jake.log"
    core.logger.DEFAULT_ACTION_LOG_PATH = log_dir / "jake_actions.jsonl"
    core.session_recorder.DEFAULT_PATH = log_dir / "jake_sessions.jsonl"
    core.action_ledger.DEFAULT_LEDGER_PATH = log_dir / "jake_ledger.jsonl"
    results = []
    try:
        for index in range(iterations):
            results.append(run_iteration(SCENARIOS[index % len(SCENARIOS)], rng))
    finally:
        (core.logger.DEFAULT_LOG_PATH, core.logger.DEFAULT_ACTION_LOG_PATH,
         core.session_recorder.DEFAULT_PATH, core.action_ledger.DEFAULT_LEDGER_PATH) = saved
        shutil.rmtree(log_dir, ignore_errors=True)
    by_scenario = {}
    for scenario in SCENARIOS:
        rows = [r for r in results if r["scenario"] == scenario]
        cancel = sorted(r["cancel_ms"] for r in rows)
        by_scenario[scenario] = {
            "iterations": len(rows), "passed": sum(r["ok"] for r in rows),
            "cancel_ms_max": cancel[-1] if cancel else None,
        }
    failed = [r for r in results if not r["ok"]]
    return {"iterations": len(results), "passed": len(results) - len(failed), "seed": seed,
            "by_scenario": by_scenario, "failures": failed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--iterations", type=int, default=102)
    parser.add_argument("--seed", type=int, default=20260926)
    args = parser.parse_args(argv)
    report = run(args.iterations, args.seed)
    for scenario, row in report["by_scenario"].items():
        print(f"{scenario:<11} {row['passed']:>3}/{row['iterations']:<3} cancel max {row['cancel_ms_max']} ms")
    for failure in report["failures"][:20]:
        print(f"FAIL {failure['scenario']}: {failure['failures']}")
    print(f"\nTotale: {report['passed']}/{report['iterations']} iterazioni corrette")
    return 0 if report["passed"] == report["iterations"] else 1


if __name__ == "__main__":
    sys.exit(main())
