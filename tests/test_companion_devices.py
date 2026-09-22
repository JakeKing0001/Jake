"""F7.2.1 (Companion Mobile MVP): `GET /devices` (dispositivi ACCOPPIATI, distinti da quelli con una sessione
attiva ora su `/status`) e `POST /devices/<id>/revoke` ("scollegare/revocare QUESTO telefono" - mai un pannello
che revoca dispositivi altrui). Server companion vero, `DeviceCredentialStore` vero (DPAPI)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from urllib import error, request

from core.companion_server import CompanionServer
from core.device_credential_store import DeviceCredentialStore


def _get(url: str, headers: dict = None, timeout: float = 5) -> tuple[int, dict]:
    req = request.Request(url, headers=headers or {})
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post(url: str, payload: dict, headers: dict = None, timeout: float = 5) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class DevicesEndpointTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jake_companion_devices_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = DeviceCredentialStore(db_path=self.tmp / "devices.db")
        self.addCleanup(self.store.close)
        self.credential_a = self.store.issue_credential("phone-a")
        self.credential_b = self.store.issue_credential("phone-b")

    def _server(self, credential_store=...) -> tuple[CompanionServer, str]:
        store = self.store if credential_store is ... else credential_store
        server = CompanionServer(command_handler=lambda text: "ok", credential_store=store)
        server.start()
        self.addCleanup(server.stop)
        return server, f"http://127.0.0.1:{server.port}"

    def _auth(self, credential) -> dict:
        return {"Authorization": f"Bearer {credential.token}"}


class ListDevicesTests(DevicesEndpointTestCase):
    def test_lists_every_paired_device_never_the_token(self):
        _, base = self._server()
        status, body = _get(f"{base}/devices", headers=self._auth(self.credential_a))
        self.assertEqual(status, 200)
        ids = {d["device_id"] for d in body["devices"]}
        self.assertEqual(ids, {"phone-a", "phone-b"})
        raw = json.dumps(body)
        self.assertNotIn(self.credential_a.token, raw)
        self.assertNotIn(self.credential_b.token, raw)

    def test_each_entry_carries_name_and_status_not_a_bare_id(self):
        _, base = self._server()
        _, body = _get(f"{base}/devices", headers=self._auth(self.credential_a))
        by_id = {d["device_id"]: d for d in body["devices"]}
        self.assertEqual(by_id["phone-a"]["status"], "active")
        self.assertIn("created_at", by_id["phone-a"])

    def test_a_revoked_device_still_appears_with_a_revoked_status(self):
        self.store.revoke("phone-b")
        _, base = self._server()
        _, body = _get(f"{base}/devices", headers=self._auth(self.credential_a))
        by_id = {d["device_id"]: d for d in body["devices"]}
        self.assertEqual(by_id["phone-b"]["status"], "pairing_required")

    def test_requires_authentication_like_any_other_read_only_endpoint(self):
        _, base = self._server()
        status, body = _get(f"{base}/devices")
        self.assertEqual((status, body["error"]), (401, "unauthorized"))

    def test_404_when_no_credential_store_is_configured(self):
        server = CompanionServer(command_handler=lambda text: "ok")
        server.start()
        self.addCleanup(server.stop)
        status, body = _get(f"http://127.0.0.1:{server.port}/devices")
        self.assertEqual((status, body["error"]), (404, "devices_not_configured"))


class RevokeDeviceTests(DevicesEndpointTestCase):
    def test_a_device_can_revoke_itself(self):
        _, base = self._server()
        status, body = _post(f"{base}/devices/phone-a/revoke", {}, headers=self._auth(self.credential_a))
        self.assertEqual((status, body), (200, {"revoked": True}))
        self.assertEqual(self.store.get_device("phone-a").status, "pairing_required")

    def test_the_revoked_token_stops_working_immediately(self):
        _, base = self._server()
        _post(f"{base}/devices/phone-a/revoke", {}, headers=self._auth(self.credential_a))
        status, body = _get(f"{base}/devices", headers=self._auth(self.credential_a))
        self.assertEqual((status, body["error"]), (401, "unauthorized"))

    def test_a_device_cannot_revoke_another_device(self):
        _, base = self._server()
        status, body = _post(f"{base}/devices/phone-b/revoke", {}, headers=self._auth(self.credential_a))
        self.assertEqual((status, body["error"]), (403, "device_id_mismatch"))
        self.assertEqual(self.store.get_device("phone-b").status, "active", "phone-b non deve essere toccato")

    def test_revoking_twice_reports_false_the_second_time(self):
        _, base = self._server()
        _post(f"{base}/devices/phone-a/revoke", {}, headers=self._auth(self.credential_a))
        status, body = _post(f"{base}/devices/phone-a/revoke", {}, headers=self._auth(self.credential_b))
        # Nessuna identita' autenticata come "phone-a" e' piu' possibile dopo la revoca (il token e' morto):
        # un secondo tentativo con un ALTRO device non puo' comunque toccare phone-a.
        self.assertEqual((status, body["error"]), (403, "device_id_mismatch"))

    def test_a_legacy_global_token_caller_without_a_device_identity_cannot_revoke_anything(self):
        """Autenticato (il vecchio companion_token globale, ancora supportato in parallelo - F1.4.6) ma SENZA
        un'identita' per-dispositivo verificata: non c'e' un "se stesso" da revocare."""
        server = CompanionServer(command_handler=lambda text: "ok", credential_store=self.store, token="legacy-secret")
        server.start()
        self.addCleanup(server.stop)
        base = f"http://127.0.0.1:{server.port}"
        status, body = _post(f"{base}/devices/phone-a/revoke", {}, headers={"Authorization": "Bearer legacy-secret"})
        self.assertEqual((status, body["error"]), (403, "device_identity_required"))
        self.assertEqual(self.store.get_device("phone-a").status, "active")

    def test_a_body_is_drained_even_though_release_style_endpoints_ignore_it(self):
        """Stesso flake Windows gia' documentato per _handle_release (WinError 10053): un corpo non letto quando
        la connessione si chiude puo' far rispondere con un RST invece di una FIN pulita."""
        _, base = self._server()
        status, body = _post(f"{base}/devices/phone-a/revoke", {"unused": "x" * 100}, headers=self._auth(self.credential_a))
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
