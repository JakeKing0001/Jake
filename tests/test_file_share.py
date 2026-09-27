"""F7.2.5: il telefono invia un file al PC. Server companion reale su loopback, credenziale reale, cartella
temporanea: serve la capability FILE (non di default), il nome viene ripulito, niente eseguibili, niente
sovrascritture, l'HUD riceve la notifica."""
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from core.companion_guard import CompanionGuard, EndpointClass
from core.companion_server import CompanionServer
from core.device_credential_store import DeviceCredentialStore


class FileShareTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.inbox = Path(tmp.name) / "Dal telefono"
        self.store = DeviceCredentialStore(db_path=Path(tmp.name) / "devices.db")
        self.addCleanup(self.store.close)
        self.store.register_device("phone-1", "Telefono di prova")
        self.token = self.store.issue_credential("phone-1").token
        self.server = CompanionServer(host="127.0.0.1", port=0, credential_store=self.store,
                                      guard=CompanionGuard(audit=None, capability_store=self.store),
                                      files_dir=self.inbox)
        self.server.start()
        self.addCleanup(self.server.stop)

    def _send(self, name, data: bytes):
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.port}/files/{name}", data=data, method="POST")
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Content-Type", "application/octet-stream")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_without_the_file_permission_nothing_is_saved(self):
        status, _ = self._send("foto.jpg", b"\xff\xd8dati")
        self.assertEqual(status, 403)
        self.assertFalse(self.inbox.exists())

    def test_a_granted_phone_sends_a_file_into_its_own_folder_without_overwriting(self):
        self.server.guard.set_capabilities("phone-1", {EndpointClass.READ_ONLY, EndpointClass.FILE})
        events = self.server.event_bus.subscribe()
        self.assertEqual(self._send("preventivo%20Rossi.pdf", b"%PDF-1.7 prova"),
                         (200, {"saved_as": "preventivo Rossi.pdf", "bytes": 14}))
        self.assertEqual(self._send("preventivo%20Rossi.pdf", b"seconda")[1]["saved_as"], "preventivo Rossi (1).pdf")
        self.assertEqual((self.inbox / "phone-1" / "preventivo Rossi.pdf").read_bytes(), b"%PDF-1.7 prova")
        self.assertIn("Ricevuto dal telefono: preventivo Rossi.pdf", events.get_nowait().payload["text"])

    def test_paths_hidden_names_and_executables_are_refused(self):
        self.server.guard.set_capabilities("phone-1", {EndpointClass.FILE})
        # cinque: la classe FILE ha un limite di frequenza (la sesta richiesta sarebbe 429)
        for name in ("..%2F..%2Fesci.txt", "..%5Cwin.ini", ".bashrc", "virus.PS1", "CON.txt"):
            with self.subTest(name=name):
                self.assertEqual(self._send(name, b"x")[0], 400)
        self.assertFalse(any(self.inbox.rglob("*")) if self.inbox.exists() else False)


if __name__ == "__main__":
    unittest.main()
