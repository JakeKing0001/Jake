"""F7.4.3 ("lease con timeout; niente ownership eterna dopo crash"): il dispositivo attivo lo resta solo finche' da'
segni di vita. Registro con orologio finto; poi server companion reale su loopback con credenziali reali."""
import time
import unittest

from core.device_registry import DeviceRegistry
from tests import test_device_access as _access  # solo helper: importarne le classi le rieseguirebbe qui


class LeaseTests(unittest.TestCase):
    def test_a_silent_active_device_expires_once_and_a_sign_of_life_renews_it(self):
        now, expired = [0.0], []
        registry = DeviceRegistry(lease_s=10, clock=lambda: now[0], on_expired=expired.append)
        registry.claim("phone-1")
        now[0] = 8
        self.assertTrue(registry.renew("phone-1"))
        self.assertFalse(registry.renew("tablet"), "solo il dispositivo attivo rinnova")
        now[0] = 17
        self.assertEqual(registry.active_device_id, "phone-1", "rinnovato a 8: scade a 18")
        now[0] = 18
        self.assertIsNone(registry.active_device_id)
        self.assertEqual(registry.list_devices(), [{"id": "phone-1", "name": "", "active": False}])
        self.assertEqual(expired, ["phone-1"], "avvisato una volta sola")
        self.assertEqual(registry.claim("phone-1")[0], None, "reclamare dopo la scadenza non e' un handoff da se stesso")


class ServerLeaseTests(unittest.TestCase):
    setUp = _access.DeviceAccessTests.setUp
    _start = _access.DeviceAccessTests._start
    _request = _access.DeviceAccessTests._request
    _open_stream = _access.OpenStreamRevocationTests._open_stream
    _wait_for = _access.OpenStreamRevocationTests._wait_for

    def _short_lease(self, seconds):
        self.server.devices = DeviceRegistry(lease_s=seconds, on_expired=self.server._active_device_expired)

    def test_a_phone_that_goes_silent_stops_being_the_active_device(self):
        self._short_lease(0.5)
        events = self.server.event_bus.subscribe()
        self.assertEqual(self._request(self.server, "POST", "/devices/phone-1/claim", {"name": "Telefono di prova"}), 200)
        time.sleep(0.8)  # nessuna richiesta: il telefono si e' bloccato
        self.assertIsNone(self.server.devices.active_device_id)
        handoffs = []
        while not events.empty():
            event = events.get_nowait()
            if event.type.value == "DEVICE_HANDOFF":
                handoffs.append(event.payload)
        self.assertIn({"from": "phone-1", "to": ""}, handoffs)

    def test_an_open_event_stream_keeps_the_phone_active_and_revocation_releases_it(self):
        self._short_lease(1.5)
        self._request(self.server, "POST", "/devices/phone-1/claim", {"name": "Telefono di prova"})
        _, closed = self._open_stream(self.token)
        time.sleep(2.5)  # oltre il lease: solo lo stream aperto lo tiene vivo
        self.assertEqual(self.server.devices.active_device_id, "phone-1")
        self.store.revoke("phone-1")
        self.assertTrue(closed.wait(4))
        self.assertTrue(self._wait_for(lambda: self.server.devices.active_device_id is None, 3))


if __name__ == "__main__":
    unittest.main()
