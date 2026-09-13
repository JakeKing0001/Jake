"""Test unitari per core/conversation_state.py: nessuna suite dedicata esisteva finora (solo
copertura indiretta tramite tests/test_jake_core_*.py).

F1.8.1 ("definire ownership della sessione... per azioni concorrenti"): buco reale, riprodotto
per davvero prima del fix - has_pending_action()/get_pending_action()/clear_pending_action()
erano tre chiamate SEPARATE, senza alcuna sincronizzazione tra loro. Due thread concorrenti
(JakeCore.answer() e' l'ingresso condiviso sia dal loop voce sia da core/companion_server.py, un
ThreadingHTTPServer - ogni richiesta gira sul proprio thread) potevano osservare ENTRAMBI la
stessa azione ancora in sospeso prima che uno dei due la ripulisse, ed eseguirla due volte."""
import threading
import time
import unittest

from core.conversation_state import ConversationStateManager
from core.request_context import reset_current_device_id, set_current_device_id


class TakePendingActionTests(unittest.TestCase):
    def test_returns_none_when_nothing_is_pending(self):
        state = ConversationStateManager()
        self.assertIsNone(state.take_pending_action())

    def test_returns_and_clears_the_pending_action(self):
        state = ConversationStateManager()
        state.set_pending_action({"intent": "DELETE_PATH", "parameters": {"path": "x"}})

        taken = state.take_pending_action()

        self.assertEqual(taken["intent"], "DELETE_PATH")
        self.assertFalse(state.has_pending_action())

    def test_a_second_take_after_the_first_returns_none(self):
        """L'essenza della correttezza: una data azione puo' essere 'presa' una volta sola."""
        state = ConversationStateManager()
        state.set_pending_action({"intent": "DELETE_PATH", "parameters": {}})

        first = state.take_pending_action()
        second = state.take_pending_action()

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_take_returns_a_copy_not_a_live_reference(self):
        state = ConversationStateManager()
        original = {"intent": "DELETE_PATH", "parameters": {"path": "x"}}
        state.set_pending_action(original)

        taken = state.take_pending_action()
        taken["intent"] = "MUTATO"

        self.assertEqual(original["intent"], "DELETE_PATH", "mutare il valore restituito non deve toccare l'originale")


class ConcurrentTakePendingActionTests(unittest.TestCase):
    """Il cuore del fix: take_pending_action() rende get+clear un'unica operazione atomica, cosi'
    al massimo UN chiamante concorrente puo' mai vincere una data azione in sospeso."""

    def test_many_concurrent_takers_only_one_wins(self):
        state = ConversationStateManager()
        state.set_pending_action({"intent": "DELETE_PATH", "parameters": {"path": "x"}})

        THREAD_COUNT = 50
        barrier = threading.Barrier(THREAD_COUNT)
        results: list = []
        lock = threading.Lock()

        def _take():
            barrier.wait()  # massimizza la sovrapposizione reale, non affidata al caso
            taken = state.take_pending_action()
            with lock:
                results.append(taken)

        threads = [threading.Thread(target=_take) for _ in range(THREAD_COUNT)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        winners = [r for r in results if r is not None]
        self.assertEqual(len(winners), 1, "esattamente un thread deve ricevere l'azione, gli altri None")

    def test_the_old_three_call_pattern_is_still_racy_even_with_each_call_individually_locked(self):
        """Prova di regressione concettuale: bloccare has_pending_action()/get_pending_action()/
        clear_pending_action() SINGOLARMENTE (fatto comunque, per coerenza) NON basta a chiudere
        il buco, perche' la race e' tra le tre chiamate, non dentro ciascuna - dimostra perche'
        take_pending_action() (una singola operazione atomica) fosse davvero necessario, non
        solo un'alternativa piu' comoda."""
        state = ConversationStateManager()
        state.set_pending_action({"intent": "DELETE_PATH", "parameters": {"path": "x"}})

        executions = []
        exec_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def _old_style_confirm():
            barrier.wait()
            if state.has_pending_action():
                action = state.get_pending_action()
                time.sleep(0.02)  # la finestra di gara reale: lavoro fatto tra lettura e pulizia
                state.clear_pending_action()
                with exec_lock:
                    executions.append(action["intent"])

        threads = [threading.Thread(target=_old_style_confirm) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(executions), 2, "il vecchio pattern a tre chiamate esegue ancora due volte")


class PerChannelPendingActionTests(unittest.TestCase):
    """F1.8.1 (chiusura, uno slot per canale): prima di questa correzione esisteva UN solo slot
    globale - due dispositivi companion CIASCUNO con una propria conferma pendente nello stesso
    istante si sovrascrivevano a vicenda. Il canale e' core.request_context.current_device_id()
    (None per la voce locale)."""

    def setUp(self):
        self.state = ConversationStateManager()
        self._tokens = []
        self.addCleanup(self._reset_all)

    def _reset_all(self):
        for token in reversed(self._tokens):
            reset_current_device_id(token)

    def _as_device(self, device_id):
        self._tokens.append(set_current_device_id(device_id))

    def test_two_devices_each_get_their_own_pending_action_slot(self):
        self._as_device("phone1")
        self.state.set_pending_action({"intent": "DELETE_PATH", "parameters": {"path": "telefono.txt"}})
        self._as_device("tablet1")
        self.state.set_pending_action({"intent": "DELETE_PATH", "parameters": {"path": "tablet.txt"}})

        self._as_device("phone1")
        phone_action = self.state.get_pending_action()
        self._as_device("tablet1")
        tablet_action = self.state.get_pending_action()

        self.assertEqual(phone_action["parameters"]["path"], "telefono.txt")
        self.assertEqual(tablet_action["parameters"]["path"], "tablet.txt")

    def test_a_second_devices_confirmation_does_not_overwrite_the_first(self):
        """Il buco reale che questa correzione chiude: prima, il secondo set_pending_action()
        cancellava silenziosamente la richiesta del primo dispositivo."""
        self._as_device("phone1")
        self.state.set_pending_action({"intent": "DELETE_PATH", "parameters": {"path": "telefono.txt"}})
        self._as_device("tablet1")
        self.state.set_pending_action({"intent": "DELETE_PATH", "parameters": {"path": "tablet.txt"}})

        self._as_device("phone1")
        self.assertTrue(self.state.has_pending_action(), "la richiesta del primo dispositivo non deve essere andata persa")

    def test_taking_one_channels_action_does_not_consume_another_channels(self):
        self._as_device("phone1")
        self.state.set_pending_action({"intent": "DELETE_PATH", "parameters": {}})
        self._as_device("tablet1")
        self.state.set_pending_action({"intent": "RENAME_PATH", "parameters": {}})

        self._as_device("phone1")
        taken = self.state.take_pending_action()

        self.assertEqual(taken["intent"], "DELETE_PATH")
        self._as_device("tablet1")
        self.assertTrue(self.state.has_pending_action(), "prendere l'azione di phone1 non deve toccare quella di tablet1")

    def test_the_local_voice_channel_is_its_own_independent_slot(self):
        """Nessun device_id impostato (None, la voce locale) e' un canale a se' stante, distinto
        da qualunque dispositivo companion."""
        self.state.set_pending_action({"intent": "DELETE_PATH", "parameters": {"path": "voce.txt"}})
        self._as_device("phone1")
        self.state.set_pending_action({"intent": "RENAME_PATH", "parameters": {"path": "telefono.txt"}})

        self._as_device(None)
        local_action = self.state.get_pending_action()

        self.assertEqual(local_action["intent"], "DELETE_PATH")

    def test_clearing_one_channel_does_not_remove_another_channels_entry_from_the_dict(self):
        """Verifica anche l'igiene di memoria (pop(), non un valore None lasciato li'): dopo che
        un canale ha consumato/annullato la propria azione, la sua chiave sparisce del tutto,
        senza toccare le altre."""
        self._as_device("phone1")
        self.state.set_pending_action({"intent": "DELETE_PATH", "parameters": {}})
        self._as_device("tablet1")
        self.state.set_pending_action({"intent": "RENAME_PATH", "parameters": {}})
        self.state.clear_pending_action()

        self.assertNotIn("tablet1", self.state._pending_actions)
        self._as_device("phone1")
        self.assertIn("phone1", self.state._pending_actions)


if __name__ == "__main__":
    unittest.main()
