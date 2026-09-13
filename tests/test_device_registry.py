"""Test unitari per core/device_registry.py: nessuna suite esisteva finora. Pura logica in
memoria (nessun I/O), coerente con il docstring del modulo.

F7 (indiretto): buco reale trovato e corretto in questa sessione. register() (chiamato da
claim() a OGNI richiesta, core/companion_server.py::_handle_claim, col nome che il client manda
in quel momento) usava dict.setdefault(), che fissa il nome alla PRIMA registrazione per sempre:
un dispositivo rinominato nell'app companion (claim successivo con un nome diverso) restava
mostrato con il nome vecchio in list_devices() a tempo indeterminato."""
import threading
import time
import unittest
from unittest import mock

from core.device_registry import DeviceRegistry


class RegisterTests(unittest.TestCase):
    def test_registering_a_new_device_makes_it_appear_inactive(self):
        registry = DeviceRegistry()
        registry.register("phone1", "Il mio telefono")
        self.assertEqual(registry.list_devices(), [{"id": "phone1", "name": "Il mio telefono", "active": False}])

    def test_registering_twice_with_a_different_name_updates_it(self):
        """Il buco reale trovato e corretto in questa sessione."""
        registry = DeviceRegistry()
        registry.register("phone1", "nome vecchio")
        registry.register("phone1", "nome nuovo")
        [device] = registry.list_devices()
        self.assertEqual(device["name"], "nome nuovo")

    def test_registering_again_with_an_empty_name_does_not_erase_the_known_name(self):
        registry = DeviceRegistry()
        registry.register("phone1", "nome noto")
        registry.register("phone1", "")
        [device] = registry.list_devices()
        self.assertEqual(device["name"], "nome noto")

    def test_registering_again_with_the_same_name_is_a_no_op(self):
        registry = DeviceRegistry()
        registry.register("phone1", "stesso nome")
        registry.register("phone1", "stesso nome")
        self.assertEqual(len(registry.list_devices()), 1)


class ClaimTests(unittest.TestCase):
    def test_claiming_a_new_device_makes_it_active(self):
        registry = DeviceRegistry()
        registry.claim("phone1", "telefono")
        self.assertEqual(registry.active_device_id, "phone1")

    def test_claiming_returns_none_when_nothing_was_active_before(self):
        registry = DeviceRegistry()
        self.assertIsNone(registry.claim("phone1"))

    def test_claiming_returns_the_previously_active_device_for_handoff(self):
        registry = DeviceRegistry()
        registry.claim("pc")
        previous = registry.claim("phone1")
        self.assertEqual(previous, "pc")
        self.assertEqual(registry.active_device_id, "phone1")

    def test_claiming_the_same_device_again_returns_none_not_itself(self):
        registry = DeviceRegistry()
        registry.claim("phone1")
        self.assertIsNone(registry.claim("phone1"))

    def test_claim_also_registers_the_device_with_its_name(self):
        registry = DeviceRegistry()
        registry.claim("phone1", "il mio telefono")
        [device] = registry.list_devices()
        self.assertEqual(device["name"], "il mio telefono")

    def test_claim_updates_the_name_of_an_already_known_device(self):
        registry = DeviceRegistry()
        registry.claim("phone1", "nome vecchio")
        registry.claim("phone1", "nome nuovo")
        [device] = registry.list_devices()
        self.assertEqual(device["name"], "nome nuovo")


class ReleaseTests(unittest.TestCase):
    def test_releasing_the_active_device_clears_it_and_returns_true(self):
        registry = DeviceRegistry()
        registry.claim("phone1")
        self.assertTrue(registry.release("phone1"))
        self.assertIsNone(registry.active_device_id)

    def test_releasing_a_device_that_is_not_active_returns_false_and_changes_nothing(self):
        registry = DeviceRegistry()
        registry.claim("phone1")
        self.assertFalse(registry.release("phone2"))
        self.assertEqual(registry.active_device_id, "phone1")

    def test_releasing_when_nothing_is_active_returns_false(self):
        registry = DeviceRegistry()
        self.assertFalse(registry.release("phone1"))


class ListDevicesTests(unittest.TestCase):
    def test_lists_every_registered_device_marking_the_active_one(self):
        registry = DeviceRegistry()
        registry.register("pc")
        registry.claim("phone1")
        devices = {device["id"]: device["active"] for device in registry.list_devices()}
        self.assertEqual(devices, {"pc": False, "phone1": True})

    def test_empty_registry_lists_nothing(self):
        self.assertEqual(DeviceRegistry().list_devices(), [])

    def test_preserves_registration_order(self):
        registry = DeviceRegistry()
        registry.register("third")
        registry.register("first")
        registry.register("second")
        self.assertEqual([d["id"] for d in registry.list_devices()], ["third", "first", "second"])


class ConcurrentClaimTests(unittest.TestCase):
    """F1.8.7 ("testare race su handoff"): claim() legge e scrive self._active_device_id in due
    passi separati. Con lo scheduler standard di sys.setswitchinterval() questa finestra e' troppo
    stretta perche' il GIL ci si infili quasi mai (0 perdite osservate su 500 prove isolate + uno
    stress da 20 esecuzioni x 5000 iterazioni), ma allargandola artificialmente - stesso
    espediente gia' usato in questa sessione per la regressione di ConversationStateManager - la
    corsa si riproduce sempre su codice senza lock: due claim() concorrenti vedono lo stesso
    "previous" e una notifica di handoff (EventType.DEVICE_HANDOFF) va persa. Questo test verifica
    che il lock aggiunto a claim() la elimini anche in quella finestra allargata, non solo nel
    caso comune."""

    def test_two_concurrent_claims_never_lose_a_handoff_notification_even_with_a_widened_window(self):
        registry = DeviceRegistry()
        registry.claim("device-0", "iniziale")

        real_swap_locked = registry._swap_active_device_locked

        def _slow_swap_locked(device_id):
            # Allarga artificialmente la finestra ESATTA della corsa originale: tra la lettura di
            # _active_device_id e la sua scrittura, non prima (un ritardo messo prima di questo
            # punto - es. dentro register() - non riproduce il bug: il lock lo terrebbe comunque
            # fuori dalla sezione critica, quindi non proverebbe nulla sulla corsa reale). Prima
            # della correzione, con questo identico ritardo qui, 20 prove su 20 perdevano una
            # notifica di handoff (due claim() vedevano lo stesso "previous").
            time.sleep(0.02)
            return real_swap_locked(device_id)

        results = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def _claim(device_id):
            barrier.wait()
            previous = registry.claim(device_id, device_id)
            with results_lock:
                results.append((device_id, previous))

        with mock.patch.object(registry, "_swap_active_device_locked", side_effect=_slow_swap_locked):
            threads = [threading.Thread(target=_claim, args=(f"device-{i}",)) for i in (1, 2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        seen_previous = {previous for _, previous in results}
        self.assertEqual(
            len(seen_previous), 2,
            f"entrambi i claim concorrenti hanno visto lo stesso 'previous' ({results}): "
            "una notifica di handoff sarebbe andata persa",
        )


if __name__ == "__main__":
    unittest.main()
