"""F6.7: "avvisami quando finisce la build". Un processo vero (figlio sintetico) viene sorvegliato fino alla
fine e la notifica passa dalla pipeline unica di JakeCore (gate + presentatore), CLI o voce."""
import os
import subprocess
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import psutil

from core.response_formatter import format_skill_result
from skills.watch_process import MAX_WATCHES, WatchProcessSkill


class _Core:
    def __init__(self):
        self.notified = []
        self.done = threading.Event()

    def present_notification(self, kind, message, source=None):
        self.notified.append((kind, message))
        self.source = source
        self.done.set()
        return message


def _fake(pid, name, created, code=0, block=None):
    def wait():
        if block is not None:
            block.wait(5)
        return code
    return SimpleNamespace(pid=pid, info={"pid": pid, "name": name, "create_time": created}, wait=wait)


class WatchRealProcessTests(unittest.TestCase):
    def _watch_child(self, code: int):
        # il figlio resta in esecuzione finche' il test non lo lascia finire (una riga su stdin): cosi' e'
        # "gia' in esecuzione" quando lo si cerca, e finisce solo dopo che Jake ha iniziato a sorvegliarlo
        child = subprocess.Popen([sys.executable, "-c", f"import sys; sys.stdin.readline(); raise SystemExit({code})"],
                                 stdin=subprocess.PIPE)
        self.addCleanup(child.wait, 5)
        self.addCleanup(child.stdin.close)
        core = _Core()
        # solo il figlio sintetico: psutil vero, attesa vera, nessun altro processo della macchina
        skill = WatchProcessSkill(core, process_iter=lambda: [
            p for p in psutil.process_iter(["pid", "name", "create_time"]) if p.pid == child.pid])
        result = skill.execute({"process": "python.exe"})
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.data["pid"], child.pid)
        self.assertEqual(format_skill_result("WATCH_PROCESS", result), f"Ok, ti avviso quando {result.data['process']} finisce.")
        self.assertEqual(core.notified, [], "notificato prima della fine del processo")
        child.stdin.write(b"fine\n")
        child.stdin.flush()
        self.assertTrue(core.done.wait(10), "la fine del processo non e' stata notificata")
        self.assertEqual(skill.watching, {})
        return core.notified

    def test_a_process_that_ends_well_is_reported_as_completed(self):
        [(kind, message)] = self._watch_child(0)
        self.assertEqual(kind, "reminder")  # richiesta esplicita dell'utente: puntuale come un promemoria
        self.assertIn("e' finito dopo", message)
        self.assertIn("completato senza errori", message)

    def test_a_failing_process_is_reported_with_its_exit_code(self):
        [(_, message)] = self._watch_child(3)
        self.assertIn("terminato con un errore (codice 3)", message)


class WatchProcessChoiceTests(unittest.TestCase):
    def test_the_most_recent_match_is_watched_and_jake_itself_never(self):
        core = _Core()
        processes = [_fake(os.getpid(), "python.exe", 999), _fake(10, "python.exe", 1), _fake(11, "python.exe", 5)]
        result = WatchProcessSkill(core, process_iter=lambda: processes).execute({"process": "python"})
        self.assertEqual(result.data["pid"], 11)

    def test_missing_or_unknown_process_is_an_error_not_a_watch(self):
        skill = WatchProcessSkill(_Core(), process_iter=lambda: [_fake(10, "code.exe", 1)])
        self.assertEqual(skill.execute({}).error, "MISSING_PARAMETERS")
        missing = skill.execute({"process": "msbuild"})
        self.assertEqual(missing.error, "NOT_FOUND")
        self.assertEqual(format_skill_result("WATCH_PROCESS", missing),
                         "Non vedo nessun processo 'msbuild' in esecuzione da sorvegliare.")
        self.assertEqual(skill.watching, {})

    def test_watches_are_bounded(self):
        release = threading.Event()
        self.addCleanup(release.set)
        processes = [_fake(100 + i, f"job{i}.exe", i, block=release) for i in range(MAX_WATCHES + 1)]
        skill = WatchProcessSkill(_Core(), process_iter=lambda: processes)
        for i in range(MAX_WATCHES):
            self.assertTrue(skill.execute({"process": f"job{i}"}).success)
        self.assertEqual(skill.execute({"process": f"job{MAX_WATCHES}"}).error, "TOO_MANY_WATCHES")


class PresentNotificationTests(unittest.TestCase):
    def _core(self, gated):
        from core.jake_core import JakeCore

        core = JakeCore.__new__(JakeCore)
        core.notify = mock.MagicMock(return_value=gated)
        core.logger = mock.MagicMock()
        return core

    def test_goes_through_the_gate_and_to_the_session_presenter(self):
        core = self._core("build finita")
        core.notification_presenter = mock.MagicMock()
        self.assertEqual(core.present_notification("reminder", "build finita"), "build finita")
        core.notify.assert_called_once_with("reminder", "build finita", source=None)
        core.notification_presenter.assert_called_once_with("reminder", "build finita")

    def test_a_gated_notification_is_not_presented(self):
        core = self._core(None)
        core.notification_presenter = mock.MagicMock()
        self.assertIsNone(core.present_notification("advisory", "x"))
        core.notification_presenter.assert_not_called()

    def test_without_a_session_it_is_printed(self):
        core = self._core("build finita")
        with mock.patch("builtins.print") as printed:
            core.present_notification("reminder", "build finita")
        self.assertIn("build finita", printed.call_args.args[0])


class SessionPresenterTests(unittest.TestCase):
    def _session(self, busy):
        from core.voice.wake_word_session import WakeWordSession

        session = WakeWordSession.__new__(WakeWordSession)
        session._speaking = lambda: busy
        session._command_busy = lambda: False
        session._logger = mock.MagicMock()
        session.jake_core = SimpleNamespace(notification_center=mock.MagicMock())
        session._set_state = mock.MagicMock()
        session.speak = mock.MagicMock()
        return session

    def test_an_explicit_request_is_spoken_even_while_jake_talks(self):
        session = self._session(busy=True)
        session._present_notification("reminder", "La build e' finita.")
        session.speak.assert_called_once_with("La build e' finita.")

    def test_an_unrequested_notification_waits_while_jake_talks(self):
        session = self._session(busy=True)
        session._present_notification("advisory", "Batteria bassa.")
        session.speak.assert_not_called()
        session.jake_core.notification_center.defer.assert_called_once_with("advisory", "Batteria bassa.")


if __name__ == "__main__":
    unittest.main()
