"""Test unitari per core/device_registry.py: nessuna suite esisteva finora. Pura logica in
memoria (nessun I/O), coerente con il docstring del modulo.

F7 (indiretto): buco reale trovato e corretto in questa sessione. register() (chiamato da
claim() a OGNI richiesta, core/companion_server.py::_handle_claim, col nome che il client manda
in quel momento) usava dict.setdefault(), che fissa il nome alla PRIMA registrazione per sempre:
un dispositivo rinominato nell'app companion (claim successivo con un nome diverso) restava
mostrato con il nome vecchio in list_devices() a tempo indeterminato."""
import unittest

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


if __name__ == "__main__":
    unittest.main()
