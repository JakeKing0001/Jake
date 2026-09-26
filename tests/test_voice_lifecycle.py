"""Ciclo di vita delle risorse vocali: stop/chiusura non lasciano thread permanenti, non bloccano
l'uscita del processo, non fanno parlare risposte vecchie e non rimettono il microfono in mute.

Bug reale trovato con una misura, non ipotizzato: i provider TTS usavano un ThreadPoolExecutor, i cui
thread NON daemon vengono aspettati dall'interprete all'uscita. Una conversione RVC (timeout HTTP 30 s)
o una sintesi Edge (6 s) gia' abbandonata da stop() teneva il processo in vita fino alla sua fine
(8,3 s misurati con una conversione di 8 s). In piu' main.py non chiamava mai WakeWordSession.stop().
I thread si confrontano PRIMA/DOPO (quelli nati durante il test), mai per numero assoluto."""
import subprocess
import sys
import textwrap
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from core.turn_cancellation import cancellable_call
from core.voice.daemon_executor import DaemonExecutor
from tests.voice_session_support import VoiceSessionTestCase, track

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _new_threads(before):
    return [t for t in threading.enumerate() if t not in before and t.is_alive()]


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class DaemonExecutorTests(unittest.TestCase):
    def test_results_and_errors_are_delivered_through_the_future(self):
        executor = DaemonExecutor(max_workers=2)
        self.addCleanup(executor.shutdown)
        self.assertEqual(executor.submit(lambda a, b: a + b, 2, b=3).result(1), 5)
        with self.assertRaises(ZeroDivisionError):
            executor.submit(lambda: 1 / 0).result(1)

    def test_workers_are_daemon_and_concurrency_is_bounded(self):
        executor = DaemonExecutor(max_workers=2, thread_name_prefix="jake-test-exec")
        release = threading.Event()
        self.addCleanup(release.set)
        running = []
        lock = threading.Lock()
        peak = {"n": 0}

        def task():
            with lock:
                running.append(1)
                peak["n"] = max(peak["n"], len(running))
            release.wait(2)
            with lock:
                running.pop()

        futures = [executor.submit(task) for _ in range(4)]
        self.assertTrue(_wait_until(lambda: len(running) == 2, 1))
        time.sleep(0.05)
        self.assertEqual(peak["n"], 2, "mai piu' di max_workers compiti insieme")
        workers = [t for t in threading.enumerate() if t.name.startswith("jake-test-exec")]
        self.assertTrue(workers and all(t.daemon for t in workers))
        release.set()
        for future in futures:
            future.result(2)
        executor.shutdown(wait=True)
        self.assertEqual([t for t in threading.enumerate() if t.name.startswith("jake-test-exec")], [])

    def test_shutdown_cancels_waiting_tasks_and_refuses_new_ones(self):
        executor = DaemonExecutor(max_workers=1)
        release = threading.Event()
        self.addCleanup(release.set)
        ran = []
        busy = executor.submit(lambda: (release.wait(2), ran.append("primo")))
        waiting = executor.submit(lambda: ran.append("in coda"))
        time.sleep(0.05)
        executor.shutdown(wait=False, cancel_futures=True)
        self.assertTrue(waiting.cancelled())
        with self.assertRaises(RuntimeError):
            executor.submit(lambda: None)
        release.set()
        busy.result(2)
        time.sleep(0.05)
        self.assertEqual(ran, ["primo"], "il compito in coda annullato non parte piu'")


class ProcessExitTests(unittest.TestCase):
    """Un processo che chiude la voce mentre una conversione e' bloccata deve uscire subito."""

    PROBE = textwrap.dedent("""
        import sys, threading, time
        sys.path.insert(0, {root!r})
        from unittest import mock
        from core.voice.{module} import {cls}
        {setup}
        thread = threading.Thread(target=provider.speak, args=("Una frase lunga.",), daemon=True)
        thread.start()
        time.sleep(0.3)
        provider.stop()
        provider.close()
        thread.join(2)
        print("speak tornato" if not thread.is_alive() else "speak bloccato", flush=True)
    """)

    def _exit_seconds(self, module, cls, setup):
        code = self.PROBE.format(root=str(_REPO_ROOT), module=module, cls=cls, setup=textwrap.dedent(setup))
        started = time.monotonic()
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        elapsed = time.monotonic() - started
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("speak tornato", completed.stdout)
        return elapsed

    def test_a_blocked_rvc_conversion_does_not_hold_the_process_open(self):
        elapsed = self._exit_seconds("character_tts_provider", "CharacterTtsProvider", """
            base = mock.MagicMock(); manager = mock.MagicMock(); manager.ensure_running.return_value = True
            manager.client.convert.side_effect = lambda data: (time.sleep(20), b"")[1]
            provider = CharacterTtsProvider(base, manager)
            provider._synthesize_to_bytes = lambda *a, **k: b"wav"
        """)
        self.assertLess(elapsed, 10, "l'uscita aspettava la conversione abbandonata (20 s)")

    def test_a_blocked_edge_synthesis_does_not_hold_the_process_open(self):
        elapsed = self._exit_seconds("edge_tts_provider", "EdgeTtsProvider", """
            mock.patch("core.voice.edge_tts_provider.is_online", return_value=True).start()
            provider = EdgeTtsProvider()
            provider._synthesize = lambda text: (time.sleep(20), None)[1]
        """)
        self.assertLess(elapsed, 10, "l'uscita aspettava la sintesi abbandonata (20 s)")


class _ScriptedStt:
    def __init__(self):
        self.next_text = ""

    def transcribe(self, audio, sample_rate):
        return self.next_text


class _BlockedCore:
    EXIT_SENTINEL = "__exit__"

    def __init__(self):
        from core.kill_switch import KillSwitch

        self.conversation_state = SimpleNamespace(has_pending_action=lambda: False)
        self.kill_switch = KillSwitch()
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []

    def answer(self, text):
        self.calls.append(text)
        self.started.set()
        cancellable_call(self.release.wait, 10)  # una chiamata al modello che non risponde
        return "risposta arrivata dopo la chiusura"


class WakeWordSessionStopTests(VoiceSessionTestCase):
    def _session(self):
        from core.voice.character_tts_provider import CharacterTtsProvider
        from core.voice.wake_word_session import WakeWordSession

        core = _BlockedCore()
        self.addCleanup(core.release.set)
        convert_release = threading.Event()
        self.addCleanup(convert_release.set)
        base = mock.MagicMock()
        manager = mock.MagicMock()
        manager.ensure_running.return_value = True
        manager.client.convert.side_effect = lambda data: (convert_release.wait(10), b"")[1]
        tts = CharacterTtsProvider(base, manager)
        tts._synthesize_to_bytes = lambda *a, **k: b"wav"
        played = []
        tts._play = played.append
        vad = SimpleNamespace(on_level=None, muted=False, SAMPLE_RATE=16000)
        stt = _ScriptedStt()
        session = track(WakeWordSession(core, stt, tts, vad_listener=vad, partials="on", follow_up_seconds=0))
        return session, core, stt, vad, played

    def test_stop_leaves_no_permanent_thread_and_no_late_speech(self):
        before = set(threading.enumerate())
        session, core, stt, vad, played = self._session()
        self.assertIsNotNone(session.live_transcriber, "serve il worker dei partial nel ciclo di vita")
        # un comando e' fermo sul modello e intanto la voce sta convertendo una frase (bloccata in RVC)
        stt.next_text = "Jake, riassumi la pagina"
        session._handle_utterance(np.zeros(320, dtype=np.float32))
        self.assertTrue(core.started.wait(2))
        session._speak_async("Sto ancora lavorando alla pagina.")
        self.assertTrue(_wait_until(lambda: vad.muted, 2), "la voce non e' partita")

        started = time.monotonic()
        session.stop()
        self.assertLess(time.monotonic() - started, 3.0, "stop() non deve restare appeso")

        # entro poco restano vivi al massimo i thread daemon abbandonati (conversione, chiamata al
        # modello): nessun thread non daemon nuovo, nessun worker di sessione.
        session_workers = ("jake-voice-command", "live-transcriber")
        self.assertTrue(_wait_until(lambda: not [t for t in _new_threads(before) if t.name in session_workers], 3),
                        [t.name for t in _new_threads(before)])
        self.assertEqual([t.name for t in _new_threads(before) if not t.daemon], [])
        self.assertTrue(_wait_until(lambda: session._tts_thread is None or not session._tts_thread.is_alive(), 3))
        self.assertFalse(vad.muted, "dopo la chiusura il microfono non resta in mute")

        # le chiamate bloccate ora rispondono: nessuna frase deve piu' uscire
        core.release.set()
        self.assertTrue(session.wait_for_commands(3))
        session._speak_async("Una frase arrivata dopo stop().")
        time.sleep(0.3)
        self.assertEqual(played, [])
        self.assertTrue(_wait_until(lambda: _new_threads(before) == [], 5), [t.name for t in _new_threads(before)])


class MainStopsTheSessionTests(unittest.TestCase):
    def test_wake_word_mode_stops_the_session_even_when_run_fails(self):
        import main

        session = mock.MagicMock()
        session.run.side_effect = KeyboardInterrupt
        core = mock.MagicMock()
        core.config = {}
        manager = mock.MagicMock()
        with mock.patch.object(main, "JakeCore", return_value=core), \
                mock.patch.object(main, "_setup_voice", return_value=(mock.MagicMock(), mock.MagicMock(), manager)), \
                mock.patch.object(main, "_speaker_store", return_value=None), \
                mock.patch("core.voice.wake_word_session.WakeWordSession", return_value=session), \
                self.assertRaises(KeyboardInterrupt):
            main.run_wake_word_mode()
        session.stop.assert_called_once_with()
        manager.stop.assert_called_once_with()
        core.shutdown.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
