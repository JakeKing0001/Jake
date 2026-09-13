"""Test unitari per core/request_context.py (F1.2.3/F1.8.1, fondamenta): il device_id del
dispositivo companion mittente, propagato per thread invece che come parametro esplicito.
L'isolamento tra thread e' la proprieta' di sicurezza che conta qui - senza di essa, due
richieste companion concorrenti da dispositivi diversi (core/companion_server.py, un
ThreadingHTTPServer) potrebbero vedersi a vicenda il device_id, esattamente la classe di buco
gia' trovata e corretta piu' volte in questa sessione per altro stato condiviso (F1.8.1, F1.8.7)."""
import threading
import unittest

from core.request_context import current_device_id, reset_current_device_id, set_current_device_id


class DefaultTests(unittest.TestCase):
    def test_default_is_none_when_never_set(self):
        self.assertIsNone(current_device_id())


class SetAndResetTests(unittest.TestCase):
    def test_set_makes_the_value_visible_on_this_thread(self):
        token = set_current_device_id("phone1")
        try:
            self.assertEqual(current_device_id(), "phone1")
        finally:
            reset_current_device_id(token)

    def test_reset_restores_the_previous_value(self):
        outer_token = set_current_device_id("phone1")
        inner_token = set_current_device_id("phone2")
        reset_current_device_id(inner_token)
        try:
            self.assertEqual(current_device_id(), "phone1")
        finally:
            reset_current_device_id(outer_token)

    def test_reset_restores_none_when_nothing_was_set_before(self):
        token = set_current_device_id("phone1")
        reset_current_device_id(token)
        self.assertIsNone(current_device_id())


class ThreadIsolationTests(unittest.TestCase):
    """La proprieta' di sicurezza che conta: un dispositivo non deve mai vedere l'identita' di
    un altro, anche con richieste concorrenti reali su thread diversi."""

    def test_concurrent_threads_never_see_each_others_device_id(self):
        observed = {}
        barrier = threading.Barrier(2)

        def _handle_request(name, device_id, delay_before_read):
            token = set_current_device_id(device_id)
            try:
                barrier.wait()
                if delay_before_read:
                    import time
                    time.sleep(0.02)
                observed[name] = current_device_id()
            finally:
                reset_current_device_id(token)

        threads = [
            threading.Thread(target=_handle_request, args=("phone", "phone1", True)),
            threading.Thread(target=_handle_request, args=("tablet", "tablet1", False)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(observed, {"phone": "phone1", "tablet": "tablet1"})

    def test_a_brand_new_thread_starts_from_the_default_not_from_the_spawning_threads_value(self):
        """Un ThreadingHTTPServer crea un thread NUOVO per ogni richiesta: verifica che quel
        thread non erediti per sbaglio il device_id impostato dal thread che lo ha creato (il
        thread principale, qui) - altrimenti una richiesta senza device_id nel body vedrebbe
        quello di una richiesta precedente gestita da un altro thread."""
        token = set_current_device_id("phone1")
        try:
            seen = {}

            def _fresh_thread():
                seen["value"] = current_device_id()

            t = threading.Thread(target=_fresh_thread)
            t.start()
            t.join()

            self.assertIsNone(seen["value"])
        finally:
            reset_current_device_id(token)


if __name__ == "__main__":
    unittest.main()
