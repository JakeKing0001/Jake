"""Test unitari per core/voice/push_to_talk.py: nessuna suite esisteva finora, nessun bug
trovato. 'keyboard' e' importato dentro run(): mock.patch.dict su sys.modules. jake_core/
stt_provider/tts_provider/microphone sono sempre MagicMock (mai hardware audio vero).

run() ha un while True: nei test si usa jake_core.EXIT_SENTINEL per uscire in modo pulito dopo
un giro, oppure keyboard.wait con side_effect che solleva un'eccezione di controllo al secondo
giro per fermare il test senza un vero loop infinito."""
import unittest
from unittest import mock

import numpy as np

from core.voice.push_to_talk import PushToTalkSession


class _StopTestLoop(Exception):
    """Eccezione di controllo per uscire da un while True durante un test."""


def _session(**kwargs):
    jake_core = kwargs.pop("jake_core", None) or mock.MagicMock(EXIT_SENTINEL="ESCI")
    stt_provider = kwargs.pop("stt_provider", None) or mock.MagicMock()
    tts_provider = kwargs.pop("tts_provider", None) or mock.MagicMock()
    microphone = kwargs.pop("microphone", None) or mock.MagicMock()
    return PushToTalkSession(jake_core, stt_provider, tts_provider, microphone=microphone, **kwargs)


class SpeakAsyncTests(unittest.TestCase):
    def test_speaks_the_given_text_in_a_background_thread(self):
        tts = mock.MagicMock()
        session = _session(tts_provider=tts)
        session._speak_async("ciao")
        session._tts_thread.join(timeout=2)
        tts.speak.assert_called_once_with("ciao")

    def test_interrupts_a_currently_speaking_thread_before_starting_a_new_one(self):
        import threading

        still_speaking = threading.Event()
        release = threading.Event()

        def slow_speak(text):
            still_speaking.set()
            release.wait(timeout=2)

        tts = mock.MagicMock()
        tts.speak.side_effect = slow_speak
        tts.stop.side_effect = lambda: release.set()

        session = _session(tts_provider=tts)
        session._speak_async("prima frase, molto lunga da parlare")
        still_speaking.wait(timeout=2)
        session._speak_async("seconda frase")
        session._tts_thread.join(timeout=2)
        tts.stop.assert_called_once()


class InterruptSpeechTests(unittest.TestCase):
    def test_no_active_thread_does_nothing(self):
        tts = mock.MagicMock()
        session = _session(tts_provider=tts)
        session._interrupt_speech()
        tts.stop.assert_not_called()


class RunTests(unittest.TestCase):
    def test_no_microphone_available_exits_immediately(self):
        microphone = mock.MagicMock()
        microphone.is_available.return_value = False
        fake_keyboard = mock.MagicMock()
        session = _session(microphone=microphone)
        with mock.patch.dict("sys.modules", {"keyboard": fake_keyboard}):
            session.run()
        fake_keyboard.wait.assert_not_called()

    def test_a_full_successful_turn_speaks_the_response(self):
        microphone = mock.MagicMock()
        microphone.is_available.return_value = True
        microphone.sample_rate = 16000
        microphone.record_while.return_value = np.array([0.1, 0.2], dtype="float32")

        stt = mock.MagicMock()
        stt.transcribe.return_value = "che ore sono"

        jake_core = mock.MagicMock(EXIT_SENTINEL="ESCI")
        jake_core.answer.return_value = "Sono le dieci."

        tts = mock.MagicMock()
        fake_keyboard = mock.MagicMock()
        fake_keyboard.is_pressed.return_value = False
        fake_keyboard.wait.side_effect = [None, _StopTestLoop()]

        session = _session(jake_core=jake_core, stt_provider=stt, tts_provider=tts, microphone=microphone)
        with mock.patch.dict("sys.modules", {"keyboard": fake_keyboard}):
            with self.assertRaises(_StopTestLoop):
                session.run()

        jake_core.answer.assert_called_once_with("che ore sono")
        session._tts_thread.join(timeout=2)
        tts.speak.assert_called_once_with("Sono le dieci.")

    def test_the_exit_sentinel_response_ends_the_loop(self):
        microphone = mock.MagicMock()
        microphone.is_available.return_value = True
        microphone.sample_rate = 16000
        microphone.record_while.return_value = np.array([0.1], dtype="float32")

        stt = mock.MagicMock()
        stt.transcribe.return_value = "esci"

        jake_core = mock.MagicMock(EXIT_SENTINEL="ESCI")
        jake_core.answer.return_value = "ESCI"

        fake_keyboard = mock.MagicMock()
        fake_keyboard.is_pressed.return_value = False
        fake_keyboard.wait.side_effect = [None, _StopTestLoop()]

        session = _session(jake_core=jake_core, stt_provider=stt, microphone=microphone)
        with mock.patch.dict("sys.modules", {"keyboard": fake_keyboard}):
            session.run()  # deve tornare senza sollevare _StopTestLoop: il loop finisce prima

        fake_keyboard.wait.assert_called_once()

    def test_empty_audio_is_skipped_without_calling_the_stt(self):
        microphone = mock.MagicMock()
        microphone.is_available.return_value = True
        microphone.sample_rate = 16000
        microphone.record_while.return_value = np.zeros((0,), dtype="float32")

        stt = mock.MagicMock()
        fake_keyboard = mock.MagicMock()
        fake_keyboard.is_pressed.return_value = False
        fake_keyboard.wait.side_effect = [None, _StopTestLoop()]

        session = _session(stt_provider=stt, microphone=microphone)
        with mock.patch.dict("sys.modules", {"keyboard": fake_keyboard}):
            with self.assertRaises(_StopTestLoop):
                session.run()
        stt.transcribe.assert_not_called()

    def test_a_microphone_error_is_reported_and_the_loop_continues(self):
        from core.voice.microphone import MicrophoneError

        microphone = mock.MagicMock()
        microphone.is_available.return_value = True
        microphone.record_while.side_effect = MicrophoneError("nessun dispositivo")

        fake_keyboard = mock.MagicMock()
        fake_keyboard.is_pressed.return_value = False
        fake_keyboard.wait.side_effect = [None, _StopTestLoop()]

        session = _session(microphone=microphone)
        with mock.patch.dict("sys.modules", {"keyboard": fake_keyboard}):
            with self.assertRaises(_StopTestLoop):
                session.run()

    def test_a_transcription_failure_is_reported_and_the_loop_continues(self):
        microphone = mock.MagicMock()
        microphone.is_available.return_value = True
        microphone.sample_rate = 16000
        microphone.record_while.return_value = np.array([0.1], dtype="float32")

        stt = mock.MagicMock()
        stt.transcribe.side_effect = RuntimeError("whisper e' andato in errore")

        fake_keyboard = mock.MagicMock()
        fake_keyboard.is_pressed.return_value = False
        fake_keyboard.wait.side_effect = [None, _StopTestLoop()]

        session = _session(stt_provider=stt, microphone=microphone)
        with mock.patch.dict("sys.modules", {"keyboard": fake_keyboard}):
            with self.assertRaises(_StopTestLoop):
                session.run()


if __name__ == "__main__":
    unittest.main()
