"""Test unitari per il riconoscimento della wake word (core/voice/wake_word_session.py), in
particolare il risveglio dalla pausa in posizione naturale (v4.1, Voice Natural 2.0). Nessun
vero microfono/audio: VAD, STT e TTS sono tutti finti, e non si aspetta mai un vero thread di
sintesi vocale (usa un TTS finto che ritorna all'istante).

Sezione aggiunta in questa sessione (dal blocco `_edit_distance` in poi): copertura del resto del
modulo, che non aveva alcun test (distanza di edit, dettatura, pausa/risveglio generico,
finestre di comando/follow-up/conferma, _process_command/_respond). jake_core/stt_provider/
tts_provider/vad_listener sono MagicMock qui (a differenza delle Fake sopra, gia' presenti prima
di questa sessione) per poter controllare/asserire le chiamate in modo piu' granulare.
'keyboard' e' un pacchetto vero installato: si patcha keyboard.write direttamente."""
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from core.voice.wake_word_session import WakeWordSession, _edit_distance


class FakeSttProvider:
    def __init__(self, text: str):
        self.text = text

    def transcribe(self, utterance, sample_rate):
        return self.text


class FakeTtsProvider:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)

    def stop(self):
        pass


class FakeJakeCore:
    EXIT_SENTINEL = "__exit__"

    def __init__(self):
        self.conversation_state = SimpleNamespace(has_pending_action=lambda: False)

    def answer(self, text):
        return "risposta"


def _session(text: str) -> WakeWordSession:
    session = WakeWordSession(
        FakeJakeCore(), FakeSttProvider(text), FakeTtsProvider(),
        vad_listener=SimpleNamespace(on_level=None, muted=False, SAMPLE_RATE=16000),
    )
    return session


class MatchWakeWordTests(unittest.TestCase):
    def test_wake_word_at_start_returns_remainder(self):
        session = _session("")
        self.assertEqual(session._match_wake_word("Jake apri spotify"), "apri spotify")

    def test_greeting_prefix_is_skipped(self):
        session = _session("")
        self.assertEqual(session._match_wake_word("ehi Jake che ore sono"), "che ore sono")

    def test_no_wake_word_returns_none(self):
        session = _session("")
        self.assertIsNone(session._match_wake_word("apri spotify"))

    def test_fuzzy_transcription_variant_is_accepted(self):
        session = _session("")
        self.assertEqual(session._match_wake_word("geek che tempo fa"), "che tempo fa")


class ContainsWakeWordAnywhereTests(unittest.TestCase):
    def test_true_when_wake_word_is_in_the_middle(self):
        session = _session("")
        self.assertTrue(session._contains_wake_word_anywhere("scusa jake torna operativo", session._is_close_to_wake_word))

    def test_true_when_wake_word_is_at_the_end(self):
        session = _session("")
        self.assertTrue(session._contains_wake_word_anywhere("riprendi pure jake", session._is_close_to_wake_word))

    def test_false_without_wake_word(self):
        session = _session("")
        self.assertFalse(session._contains_wake_word_anywhere("continua pure", session._is_close_to_wake_word))


class PauseWakeUpTests(unittest.TestCase):
    def test_wakes_up_when_wake_word_is_not_the_first_word(self):
        """Prima del v4.1 il risveglio richiedeva 'Jake' come prima parola: 'scusa se ti
        disturbo, Jake, svegliati' non funzionava."""
        session = _session("scusa se ti disturbo jake svegliati")
        session.paused_until = time.time() + 60

        session._handle_utterance(object())

        self.assertEqual(session.paused_until, 0.0, "resume_listening() doveva azzerare la pausa")

    def test_stays_paused_without_a_wake_up_phrase(self):
        session = _session("jake continua a dormire per favore")  # nome presente, ma nessuna frase di risveglio
        session.paused_until = time.time() + 60
        original_pause = session.paused_until

        session._handle_utterance(object())

        self.assertEqual(session.paused_until, original_pause)

    def test_stays_paused_without_the_wake_word_at_all(self):
        session = _session("svegliati per favore")  # frase di risveglio, ma senza il nome
        session.paused_until = time.time() + 60
        original_pause = session.paused_until

        session._handle_utterance(object())

        self.assertEqual(session.paused_until, original_pause)


# ---- resto del modulo, senza copertura prima di questa sessione ------------------------------


def _mock_session(**kwargs):
    jake_core = kwargs.pop("jake_core", None)
    if jake_core is None:
        jake_core = mock.MagicMock(EXIT_SENTINEL="ESCI")
        jake_core.conversation_state.has_pending_action.return_value = False
    stt_provider = kwargs.pop("stt_provider", None) or mock.MagicMock()
    tts_provider = kwargs.pop("tts_provider", None) or mock.MagicMock()
    vad_listener = kwargs.pop("vad_listener", None) or mock.MagicMock(on_level=None)
    return WakeWordSession(jake_core, stt_provider, tts_provider, vad_listener=vad_listener, **kwargs)


class EditDistanceTests(unittest.TestCase):
    def test_identical_strings_have_zero_distance(self):
        self.assertEqual(_edit_distance("jake", "jake"), 0)

    def test_one_substitution(self):
        self.assertEqual(_edit_distance("jake", "jeke"), 1)

    def test_one_insertion(self):
        self.assertEqual(_edit_distance("jak", "jake"), 1)

    def test_one_deletion(self):
        self.assertEqual(_edit_distance("jake", "jak"), 1)

    def test_completely_different_words_have_a_larger_distance(self):
        self.assertGreater(_edit_distance("jake", "pizza"), 1)


class MatchWakeWordEdgeCaseTests(unittest.TestCase):
    def test_the_wake_word_alone_returns_an_empty_remainder(self):
        session = _mock_session()
        self.assertEqual(session._match_wake_word("Jake"), "")

    def test_repeated_wake_words_are_all_skipped(self):
        session = _mock_session()
        self.assertEqual(session._match_wake_word("Jake Jake apri Chrome"), "apri chrome")

    def test_a_close_mistranscription_is_still_recognized(self):
        session = _mock_session()
        self.assertEqual(session._match_wake_word("geic apri Chrome"), "apri chrome")

    def test_empty_text_returns_none(self):
        session = _mock_session()
        self.assertIsNone(session._match_wake_word(""))

    def test_a_lone_courtesy_word_is_not_mistaken_for_the_wake_word(self):
        session = _mock_session()
        self.assertIsNone(session._match_wake_word("ehi"))


class TypeDictationTests(unittest.TestCase):
    def test_spoken_punctuation_is_converted_to_symbols(self):
        with mock.patch("keyboard.write") as write:
            WakeWordSession._type_dictation("ciao virgola come stai punto interrogativo")
        written_text = write.call_args.args[0]
        self.assertEqual(written_text.strip(), "ciao, come stai?")

    def test_a_capo_becomes_a_newline(self):
        with mock.patch("keyboard.write") as write:
            WakeWordSession._type_dictation("prima riga a capo seconda riga")
        written_text = write.call_args.args[0]
        self.assertIn("\n", written_text)

    def test_plain_text_without_spoken_punctuation_is_typed_as_is(self):
        with mock.patch("keyboard.write") as write:
            WakeWordSession._type_dictation("scrivi questo testo normale")
        written_text = write.call_args.args[0]
        self.assertEqual(written_text, "scrivi questo testo normale ")


class PauseResumeTests(unittest.TestCase):
    def test_pause_listening_sets_a_future_deadline(self):
        session = _mock_session()
        before = time.time()
        session.pause_listening(minutes=5)
        self.assertGreater(session.paused_until, before + 4 * 60)
        self.assertEqual(session.state, "paused")

    def test_resume_listening_clears_the_pause(self):
        session = _mock_session()
        session.pause_listening(minutes=5)
        session.resume_listening()
        self.assertEqual(session.paused_until, 0.0)
        self.assertEqual(session.state, "idle")


class HandleUtteranceTests(unittest.TestCase):
    def test_an_empty_transcription_is_ignored(self):
        session = _mock_session()
        session.stt_provider.transcribe.return_value = "   "
        session._handle_utterance(object())
        session.jake_core.answer.assert_not_called()

    def test_while_paused_a_wake_up_phrase_resumes_listening(self):
        session = _mock_session()
        session.stt_provider.transcribe.return_value = "Jake svegliati"
        session.pause_listening(minutes=10)
        with mock.patch.object(session, "_respond") as respond:
            session._handle_utterance(object())
        self.assertEqual(session.paused_until, 0.0)
        respond.assert_called_once()

    def test_while_paused_an_unrelated_phrase_stays_paused(self):
        session = _mock_session()
        session.stt_provider.transcribe.return_value = "che tempo fa oggi"
        session.pause_listening(minutes=10)
        deadline = session.paused_until
        with mock.patch.object(session, "_respond") as respond:
            session._handle_utterance(object())
        self.assertEqual(session.paused_until, deadline)
        respond.assert_not_called()

    def test_dictation_types_the_spoken_text(self):
        session = _mock_session()
        session.stt_provider.transcribe.return_value = "scrivi questo"
        session.start_dictation()
        with mock.patch.object(session, "_type_dictation") as type_dictation:
            session._handle_utterance(object())
        type_dictation.assert_called_once_with("scrivi questo")

    def test_the_stop_dictation_phrase_ends_dictation(self):
        session = _mock_session()
        session.stt_provider.transcribe.return_value = "basta dettatura"
        session.start_dictation()
        with mock.patch.object(session, "_respond") as respond:
            session._handle_utterance(object())
        self.assertFalse(session.dictation_active)
        respond.assert_called_once_with("Dettatura terminata.")

    def test_the_wake_word_with_a_command_processes_it_immediately(self):
        session = _mock_session()
        session.stt_provider.transcribe.return_value = "Jake che ore sono"
        session.jake_core.answer.return_value = "Sono le dieci."
        with mock.patch.object(session, "_respond") as respond:
            session._handle_utterance(object())
        session.jake_core.answer.assert_called_once_with("che ore sono")
        respond.assert_called_once_with("Sono le dieci.")

    def test_the_bare_wake_word_arms_the_command_window_without_answering(self):
        session = _mock_session()
        session.stt_provider.transcribe.return_value = "Jake"
        session._handle_utterance(object())
        session.jake_core.answer.assert_not_called()
        self.assertGreater(session._awaiting_command_until, time.time())

    def test_a_command_inside_the_awaiting_window_is_processed_without_the_wake_word(self):
        session = _mock_session()
        session._awaiting_command_until = time.time() + 5
        session.stt_provider.transcribe.return_value = "che ore sono"
        session.jake_core.answer.return_value = "Sono le dieci."
        with mock.patch.object(session, "_respond") as respond:
            session._handle_utterance(object())
        session.jake_core.answer.assert_called_once_with("che ore sono")
        respond.assert_called_once_with("Sono le dieci.")

    def test_a_phrase_outside_every_window_and_without_a_wake_word_is_ignored(self):
        session = _mock_session()
        session.stt_provider.transcribe.return_value = "che ore sono"
        session._handle_utterance(object())
        session.jake_core.answer.assert_not_called()

    def test_speech_while_jake_is_talking_and_no_wake_word_is_ignored(self):
        session = _mock_session()
        session.stt_provider.transcribe.return_value = "qualcosa detto nel frattempo"
        session._tts_thread = mock.MagicMock()
        session._tts_thread.is_alive.return_value = True
        session._handle_utterance(object())
        session.jake_core.answer.assert_not_called()


class ProcessCommandTests(unittest.TestCase):
    def test_the_exit_sentinel_stops_the_session(self):
        session = _mock_session()
        session._running = True
        session.jake_core.answer.return_value = "ESCI"
        with mock.patch.object(session, "_respond") as respond:
            session._process_command("esci")
        self.assertFalse(session._running)
        self.assertEqual(session.state, "exit")
        respond.assert_not_called()

    def test_a_normal_response_is_spoken(self):
        session = _mock_session()
        session.jake_core.answer.return_value = "Fatto."
        with mock.patch.object(session, "_respond") as respond:
            session._process_command("fai una cosa")
        respond.assert_called_once_with("Fatto.")


class RespondTests(unittest.TestCase):
    def test_an_empty_response_opens_a_follow_up_window_without_speaking(self):
        session = _mock_session()
        with mock.patch.object(session, "_speak_async") as speak_async:
            session._respond("")
        speak_async.assert_not_called()
        self.assertGreater(session._follow_up_until, time.time())

    def test_a_non_empty_response_is_spoken(self):
        session = _mock_session()
        with mock.patch.object(session, "_speak_async") as speak_async:
            session._respond("Fatto.")
        speak_async.assert_called_once_with("Fatto.")


if __name__ == "__main__":
    unittest.main()
