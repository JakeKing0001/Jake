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


if __name__ == "__main__":
    unittest.main()
