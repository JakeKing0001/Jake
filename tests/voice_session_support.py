"""Supporto condiviso per i test che guidano `WakeWordSession`.

I comandi vocali girano su un worker (answer() non blocca il listener): un test che termina mentre
il worker e' ancora vivo lo lascerebbe parlare dentro il test successivo. `track()` registra la
sessione e `VoiceSessionTestCase.tearDown` aspetta che ogni turno sia finito."""
import unittest
import weakref

_LIVE_SESSIONS: "weakref.WeakSet" = weakref.WeakSet()


def track(session):
    _LIVE_SESSIONS.add(session)
    return session


class VoiceSessionTestCase(unittest.TestCase):
    def tearDown(self):
        sessions = list(_LIVE_SESSIONS)
        _LIVE_SESSIONS.clear()
        for session in sessions:
            self.assertTrue(session.wait_for_commands(5), "un turno vocale e' rimasto appeso oltre il test")
        super().tearDown()
