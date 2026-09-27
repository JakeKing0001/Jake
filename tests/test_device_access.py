"""F7.1.3: le capability per dispositivo erano solo in memoria del server - un riavvio restituiva a un dispositivo
limitato i permessi di default (comandi e approvazioni). Ora sono persistenti e l'utente le imposta a voce.
Server companion reale su loopback, database temporaneo."""
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from core.companion_guard import CompanionGuard
from core.companion_server import CompanionServer
from core.device_credential_store import DeviceCredentialStore
from core.response_formatter import format_skill_result
from skills.device_access import SetDeviceAccessSkill


class DeviceAccessTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = DeviceCredentialStore(db_path=Path(tmp.name) / "devices.db")
        self.addCleanup(self.store.close)
        self.store.register_device("phone-1", "Telefono di prova")
        self.token = self.store.issue_credential("phone-1").token
        self.server = self._start()

    def _start(self):
        server = CompanionServer(host="127.0.0.1", port=0, credential_store=self.store,
                                 guard=CompanionGuard(audit=None, capability_store=self.store),
                                 command_handler=lambda text: "ok")
        server.start()
        self.addCleanup(server.stop)
        return server

    def _request(self, server, method, path, body=None):
        request = urllib.request.Request(f"http://127.0.0.1:{server.port}{path}", method=method,
                                         data=json.dumps(body).encode() if body is not None else None)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_read_only_blocks_commands_and_survives_a_restart(self):
        self.assertEqual(self._request(self.server, "POST", "/command", {"text": "che ore sono"}), 200)
        core = SimpleNamespace(companion_server=self.server)
        result = SetDeviceAccessSkill(core).execute({"device": "telefono", "level": "sola lettura"})
        self.assertTrue(result.success, result)
        self.assertEqual(format_skill_result("SET_DEVICE_ACCESS", result),
                         'Fatto: Telefono di prova ora ha accesso "sola lettura".')
        self.assertEqual(self._request(self.server, "POST", "/command", {"text": "che ore sono"}), 403)
        self.assertEqual(self._request(self.server, "GET", "/status"), 200)

        self.server.stop()
        restarted = self._start()  # nuovo server e nuovo guard, stesso database
        self.assertEqual(self._request(restarted, "POST", "/command", {"text": "che ore sono"}), 403)

    def test_unknown_level_ambiguous_device_and_the_native_hud_are_refused(self):
        self.store.register_device("phone-2", "Telefono di lavoro")
        self.store.register_device("native-hud-local", "HUD nativo (questo PC)")
        skill = SetDeviceAccessSkill(SimpleNamespace(companion_server=self.server))
        self.assertEqual(skill.execute({"device": "telefono", "level": "tutto e subito"}).error, "INVALID_PARAMETERS")
        self.assertEqual(skill.execute({"device": "telefono", "level": "comandi"}).error, "AMBIGUOUS")
        self.assertEqual(skill.execute({"device": "hud nativo", "level": "comandi"}).error, "NOT_FOUND")

    def test_a_corrupted_capability_row_fails_closed(self):
        with self.store._lock:
            self.store._connection.execute(
                "INSERT INTO device_capabilities (device_id, classes, updated_at) VALUES ('phone-1', 'non json', 0)")
            self.store._connection.commit()
        self.assertEqual(CompanionGuard(audit=None, capability_store=self.store).capabilities_of("phone-1"), frozenset())


if __name__ == "__main__":
    unittest.main()
