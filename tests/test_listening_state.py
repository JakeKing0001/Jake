"""Test per core/voice/listening_state.py (F2.3.3-F2.3.5) e core/voice/wake_election.py (F2.3.7).
Orologio finto ovunque: ogni scadenza si prova senza attese reali."""
import unittest

from core.device_registry import DeviceRegistry
from core.hud_protocol import EventType
from core.voice.listening_state import (
    EchoGuard, ListeningState, ListeningStateMachine, MicIndicator, RepeatGuard, WakeCooldown,
)
from core.voice.wake_election import WakeElection


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.changes = []
        self.m = ListeningStateMachine(self.clock, on_change=lambda a, b: self.changes.append((a, b)))

    def test_starts_waiting_for_the_wake_word(self):
        self.assertEqual(self.m.state, ListeningState.WAKE)
        self.assertFalse(self.m.accepts_without_wake_word())

    def test_a_bare_wake_word_arms_the_command_window_then_it_expires(self):
        self.m.arm_command()
        self.assertEqual(self.m.state, ListeningState.COMMAND)
        self.assertTrue(self.m.accepts_without_wake_word())
        self.clock.advance(7.9)
        self.assertEqual(self.m.state, ListeningState.COMMAND)
        self.clock.advance(0.2)
        self.assertEqual(self.m.state, ListeningState.WAKE)

    def test_follow_up_opens_after_a_response_and_expires(self):
        self.m.open_follow_up()
        self.assertEqual(self.m.state, ListeningState.FOLLOW_UP)
        self.assertTrue(self.m.accepts_without_wake_word())
        self.clock.advance(6.1)
        self.assertEqual(self.m.state, ListeningState.WAKE)

    def test_follow_up_disabled_with_zero_seconds(self):
        m = ListeningStateMachine(self.clock, follow_up_s=0)
        m.open_follow_up()
        self.assertEqual(m.state, ListeningState.WAKE)

    def test_a_pending_confirmation_extends_acceptance_beyond_the_follow_up_window(self):
        self.m.open_follow_up()
        self.m.set_pending_action(True)
        self.clock.advance(10)  # follow-up scaduto, conferma ancora nella sua finestra
        self.assertEqual(self.m.state, ListeningState.CONFIRMATION)
        self.assertTrue(self.m.accepts_without_wake_word())
        self.clock.advance(20)
        self.assertEqual(self.m.state, ListeningState.WAKE)

    def test_without_a_pending_action_there_is_no_confirmation_state(self):
        self.m.open_follow_up()
        self.clock.advance(10)
        self.assertEqual(self.m.state, ListeningState.WAKE)

    def test_dictation_writes_everything_so_it_does_not_accept_commands(self):
        self.m.start_dictation()
        self.assertEqual(self.m.state, ListeningState.DICTATION)
        self.assertFalse(self.m.accepts_without_wake_word())
        self.m.stop_dictation()
        self.assertEqual(self.m.state, ListeningState.WAKE)

    def test_sleep_beats_every_other_state_and_wake_up_ends_it(self):
        self.m.start_dictation()
        self.m.arm_command()
        self.m.sleep(10)
        self.assertEqual(self.m.state, ListeningState.SLEEP)
        self.assertFalse(self.m.accepts_without_wake_word())
        self.m.wake_up()
        self.assertEqual(self.m.state, ListeningState.DICTATION)  # la dettatura era rimasta attiva

    def test_sleep_expires_by_itself(self):
        self.m.sleep(1)
        self.clock.advance(61)
        self.assertEqual(self.m.state, ListeningState.WAKE)

    def test_sleep_is_at_least_one_minute(self):
        self.m.sleep(0)
        self.clock.advance(59)
        self.assertEqual(self.m.state, ListeningState.SLEEP)

    def test_command_consumed_closes_command_and_follow_up(self):
        self.m.arm_command()
        self.m.open_follow_up()
        self.m.command_consumed()
        self.assertEqual(self.m.state, ListeningState.WAKE)

    def test_on_change_reports_each_transition_once_including_timeouts(self):
        self.m.arm_command()
        self.m.arm_command()  # nessun cambio
        self.clock.advance(9)
        self.m.tick()
        self.assertEqual(self.changes, [
            (ListeningState.WAKE, ListeningState.COMMAND), (ListeningState.COMMAND, ListeningState.WAKE),
        ])

    def test_states_are_the_four_the_roadmap_names_plus_command_and_confirmation(self):
        self.assertTrue({"wake", "follow_up", "dictation", "sleep"} <= {s.value for s in ListeningState})


class EchoGuardTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.g = EchoGuard(self.clock)

    def test_what_jake_just_said_is_an_echo(self):
        self.g.note_spoken("Ho aperto Spotify e messo la tua playlist preferita")
        self.assertTrue(self.g.is_echo("ho aperto spotify e messo la tua playlist"))

    def test_a_new_request_is_not_an_echo(self):
        self.g.note_spoken("Ho aperto Spotify e messo la tua playlist preferita")
        self.assertFalse(self.g.is_echo("jake che ore sono adesso"))

    def test_short_phrases_are_never_judged_echo(self):
        self.g.note_spoken("Ciao sono Jake e sono qui")
        self.assertFalse(self.g.is_echo("jake"))
        self.assertFalse(self.g.is_echo("sono qui"))

    def test_the_echo_window_expires(self):
        self.g.note_spoken("Ho aperto Spotify e messo la tua playlist preferita")
        self.clock.advance(13)
        self.assertFalse(self.g.is_echo("ho aperto spotify e messo la tua playlist"))

    def test_nothing_spoken_means_no_echo(self):
        self.assertFalse(EchoGuard(self.clock).is_echo("qualunque frase abbastanza lunga qui"))

    def test_partial_overlap_below_threshold_is_not_echo(self):
        self.g.note_spoken("Sono le dieci e mezza del mattino")
        self.assertFalse(self.g.is_echo("sono le dieci ma apri chrome subito"))


class CooldownAndRepeatTests(unittest.TestCase):
    def test_cooldown_blocks_a_second_wake_within_the_window(self):
        clock = Clock()
        c = WakeCooldown(clock, seconds=1.5)
        self.assertTrue(c.allow())
        c.register()
        clock.advance(1.0)
        self.assertFalse(c.allow())
        clock.advance(0.6)
        self.assertTrue(c.allow())

    def test_a_rejected_wake_does_not_extend_the_cooldown(self):
        clock = Clock()
        c = WakeCooldown(clock, seconds=1.5)
        c.register()
        clock.advance(1.0)
        c.allow()  # solo una domanda, non un'attivazione
        clock.advance(0.6)
        self.assertTrue(c.allow())

    def test_an_identical_phrase_within_the_window_is_a_replay(self):
        clock = Clock()
        r = RepeatGuard(clock, window_s=2.5)
        self.assertFalse(r.is_replay("Jake, che ore sono?"))
        clock.advance(1.0)
        self.assertTrue(r.is_replay("jake che ore sono"))  # stessa frase, punteggiatura diversa

    def test_the_same_phrase_after_the_window_is_a_new_request(self):
        clock = Clock()
        r = RepeatGuard(clock, window_s=2.5)
        r.is_replay("jake che ore sono")
        clock.advance(5)
        self.assertFalse(r.is_replay("jake che ore sono"))

    def test_a_different_phrase_is_never_a_replay(self):
        clock = Clock()
        r = RepeatGuard(clock)
        r.is_replay("jake che ore sono")
        self.assertFalse(r.is_replay("jake apri spotify"))

    def test_empty_text_is_not_a_replay(self):
        r = RepeatGuard(Clock())
        r.is_replay("")
        self.assertFalse(r.is_replay(""))


class MicIndicatorTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.clock = Clock()
        self.mic = MicIndicator(self.events.append, self.clock)

    def test_publishes_on_change_with_a_typed_event(self):
        self.assertTrue(self.mic.update(True, "wake"))
        (event,) = self.events
        self.assertEqual(event.type, EventType.MIC_STATE)
        self.assertEqual(event.payload, {"open": True, "discarding": False, "reason": "wake", "since": 1000.0})

    def test_no_event_when_nothing_changed(self):
        self.mic.update(True, "wake")
        self.assertFalse(self.mic.update(True, "wake"))
        self.assertEqual(len(self.events), 1)

    def test_a_reason_change_is_published_again(self):
        self.mic.update(True, "wake")
        self.clock.advance(5)
        self.mic.update(True, "dictation")
        self.assertEqual([e.payload["reason"] for e in self.events], ["wake", "dictation"])
        self.assertEqual(self.mic.since, 1005.0)

    def test_open_but_discarding_is_reported_as_open(self):
        """Mentre Jake parla i frame si scartano ma lo stream e' aperto: mai "spento"."""
        self.mic.update(True, "speaking", discarding=True)
        self.assertTrue(self.events[0].payload["open"])
        self.assertTrue(self.events[0].payload["discarding"])

    def test_the_event_survives_the_wire_format(self):
        from core.hud_protocol import HudEvent

        self.mic.update(True, "wake")
        self.assertEqual(HudEvent.from_json(self.events[0].to_json()).payload["open"], True)

    def test_initial_state_is_closed(self):
        self.assertEqual(self.mic.payload()["open"], False)


class WakeElectionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def _election(self, **kwargs):
        return WakeElection(self.clock, window_s=0.5, **kwargs)

    def test_nobody_wins_until_the_window_closes(self):
        e = self._election()
        e.submit("pc", 0.6)
        self.assertIsNone(e.resolve())
        self.assertIsNone(e.should_respond("pc"))
        self.clock.advance(0.5)
        self.assertEqual(e.resolve(), "pc")

    def test_the_best_listener_wins_and_the_others_stay_silent(self):
        e = self._election()
        e.submit("pc", 0.4)
        e.submit("cucina", 0.9)
        self.clock.advance(0.6)
        self.assertTrue(e.should_respond("cucina"))
        self.assertFalse(e.should_respond("pc"))

    def test_exactly_one_winner_even_with_identical_scores(self):
        e = self._election()
        for device in ("b", "a", "c"):
            e.submit(device, 0.7)
        self.clock.advance(1)
        winners = [d for d in "abc" if e.should_respond(d)]
        self.assertEqual(winners, ["a"])  # deterministico: id piu' basso

    def test_a_near_tie_goes_to_the_already_active_device(self):
        e = self._election(active_device=lambda: "pc")
        e.submit("pc", 0.70)
        e.submit("telefono", 0.73)  # entro il margine: continuita' della conversazione
        self.clock.advance(1)
        self.assertEqual(e.resolve(), "pc")

    def test_a_clearly_better_device_beats_the_active_one(self):
        e = self._election(active_device=lambda: "pc")
        e.submit("pc", 0.30)
        e.submit("telefono", 0.90)
        self.clock.advance(1)
        self.assertEqual(e.resolve(), "telefono")

    def test_priority_breaks_a_tie_when_nobody_is_active(self):
        e = self._election(priority={"salotto": 5})
        e.submit("cucina", 0.8)
        e.submit("salotto", 0.8)
        self.clock.advance(1)
        self.assertEqual(e.resolve(), "salotto")

    def test_the_decision_is_stable_and_late_candidates_do_not_reopen_it(self):
        e = self._election()
        e.submit("pc", 0.5)
        self.clock.advance(0.6)
        self.assertEqual(e.resolve(), "pc")
        e.submit("telefono", 0.99)  # arrivata dopo la chiusura del round
        self.assertEqual(e.resolve(), "pc")

    def test_a_new_round_starts_after_the_window_when_a_new_wake_arrives(self):
        e = self._election()
        e.submit("pc", 0.5)
        self.clock.advance(0.6)
        e.resolve()
        self.clock.advance(5)
        e.submit("telefono", 0.6)
        self.clock.advance(0.6)
        self.assertEqual(e.resolve(), "telefono")

    def test_new_round_clears_everything(self):
        e = self._election()
        e.submit("pc", 0.5)
        self.clock.advance(1)
        e.resolve()
        e.new_round()
        self.assertIsNone(e.resolve())

    def test_the_same_device_keeps_its_best_score(self):
        e = self._election()
        e.submit("pc", 0.3)
        e.submit("pc", 0.9)
        e.submit("pc", 0.1)
        e.submit("telefono", 0.5)
        self.clock.advance(1)
        self.assertEqual(e.resolve(), "pc")

    def test_scores_are_clamped_to_the_valid_range(self):
        e = self._election()
        e.submit("a", 5.0)
        e.submit("b", -3.0)
        self.clock.advance(1)
        self.assertEqual(e.resolve(), "a")

    def test_no_candidates_no_winner(self):
        self.assertIsNone(self._election().resolve())

    def test_winner_takes_the_session_in_the_device_registry(self):
        registry = DeviceRegistry()
        registry.claim("pc", "PC")
        e = self._election(active_device=lambda: registry.active_device_id)
        e.submit("pc", 0.3)
        e.submit("telefono", 0.95)
        self.clock.advance(1)
        self.assertEqual(e.claim_winner(registry, {"telefono": "Telefono di Davide"}), "telefono")
        self.assertEqual(registry.active_device_id, "telefono")

    def test_claim_winner_before_the_window_closes_does_nothing(self):
        registry = DeviceRegistry()
        e = self._election()
        e.submit("pc", 0.9)
        self.assertIsNone(e.claim_winner(registry))
        self.assertIsNone(registry.active_device_id)


class PushToTalkFallbackTests(unittest.TestCase):
    def test_push_to_talk_needs_neither_the_wake_word_stack_nor_the_vad(self):
        """F2.3.6: e' il fallback deterministico se wake word/VAD sbagliano: non deve dipendere da
        nessuno dei due, quindi importarlo non puo' trascinare dentro webrtcvad ne' la sessione
        a wake word (verificato in un processo pulito, non nel processo dei test)."""
        import subprocess
        import sys

        code = (
            "import sys, core.voice.push_to_talk;"
            "bad=[m for m in ('webrtcvad','core.voice.vad_listener','core.voice.wake_word_session','core.voice.listening_state') if m in sys.modules];"
            "print(','.join(bad))"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
