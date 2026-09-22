"""F7.1.2 (Companion Mobile MVP): pairing end-to-end - un dispositivo NUOVO (nessuna credenziale) apre una
richiesta HTTP vera, Jake la mostra sul canale locale con lo STESSO meccanismo di conferma gia' usato per ogni
azione ADMIN (un "si'" scritto in chat, non un endpoint HTTP separato che il companion potrebbe raggiungere da
solo), e il companion ritira l'esito con un poll. Server companion vero su porta effimera, `PairingService` e
`DeviceCredentialStore` veri (DPAPI), `JakeCore` "spoglio" ma con la pipeline di conferma REALE (F6/F7.6 inclusi,
tramite `_bare_core` - vedi tests/test_jake_core_pipeline.py)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from urllib import error, request

from core.auth_gate import AuthGate
from core.companion_server import CompanionServer
from core.device_credential_store import DeviceCredentialStore
from core.policy_engine import PolicyEngine
from core.sync_crypto import DeviceKeys
from skills.pairing import ApprovePairingSkill
from tests.test_jake_core_pipeline import FakeRegistry, _bare_core


def _get(url: str, timeout: float = 5) -> tuple[int, dict]:
    try:
        with request.urlopen(url, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post(url: str, payload: dict, timeout: float = 5) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _sync_public_key_payload(keys: DeviceKeys) -> dict:
    return keys.public.to_dict()


class PairingEndToEndTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jake_companion_pairing_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = DeviceCredentialStore(db_path=self.tmp / "devices.db")
        self.addCleanup(self.store.close)
        self.prints: list[str] = []

    def _core(self, **overrides):
        overrides.setdefault("ledger_path", self.tmp / "ledger.jsonl")
        overrides.setdefault("device_credential_store", self.store)
        core = _bare_core(**overrides)
        core.skill_registry = overrides.get(
            "skill_registry", FakeRegistry({"APPROVE_PAIRING": ApprovePairingSkill(core.pairing_service, core.sync_keyring)}),
        )
        # print() reale non e' osservabile facilmente nei test: stesso identico contenuto passa da
        # self.notify() -> gated -> stampato, quindi lo si intercetta li' senza duplicare la logica di notify().
        return core

    def _start_server(self, core) -> str:
        server = CompanionServer(
            command_handler=core.answer, credential_store=self.store, pairing_service=core.pairing_service,
            conversation_state=core.conversation_state, on_pairing_requested=core._on_pairing_requested,
        )
        server.start()
        self.addCleanup(server.stop)
        return f"http://127.0.0.1:{server.port}"


class HappyPathTests(PairingEndToEndTestCase):
    def test_start_is_reachable_with_no_credentials_at_all(self):
        core = self._core()
        base = self._start_server(core)
        status, body = _post(f"{base}/pairing/start", {"requested_name": "Telefono di Davide"})
        self.assertEqual(status, 200)
        self.assertIn("challenge_id", body)
        self.assertIn("expires_at", body)

    def test_starting_sets_a_local_pending_confirmation_with_a_friendly_message(self):
        core = self._core()
        base = self._start_server(core)
        _post(f"{base}/pairing/start", {"requested_name": "Telefono di Davide"})
        action = core.conversation_state.get_pending_action()
        self.assertEqual(action["intent"], "APPROVE_PAIRING")
        self.assertEqual(action["parameters"]["requested_name"], "Telefono di Davide")

    def test_saying_yes_approves_and_the_phone_retrieves_a_real_working_token(self):
        core = self._core()
        base = self._start_server(core)
        _, start_body = _post(f"{base}/pairing/start", {"requested_name": "Telefono di Davide"})
        challenge_id = start_body["challenge_id"]

        core.answer("sì")  # nessuna asserzione sul testo esatto della risposta: solo che il pairing risulti approvato

        status, poll_body = _get(f"{base}/pairing/{challenge_id}")
        self.assertEqual((status, poll_body["status"]), (200, "approved"))
        token, device_id = poll_body["token"], poll_body["device_id"]
        self.assertTrue(token)

        # Il token appena consegnato funziona DAVVERO per autenticarsi sul companion server.
        req = request.Request(f"{base}/status", headers={"Authorization": f"Bearer {token}"})
        with request.urlopen(req, timeout=5) as opened:
            self.assertEqual(opened.status, 200)
        self.assertEqual(self.store.get_device(device_id).status, "active")

    def test_a_second_poll_after_pickup_reports_already_delivered_not_the_token_again(self):
        core = self._core()
        base = self._start_server(core)
        _, start_body = _post(f"{base}/pairing/start", {})
        challenge_id = start_body["challenge_id"]
        core.answer("sì")
        _get(f"{base}/pairing/{challenge_id}")
        status, body = _get(f"{base}/pairing/{challenge_id}")
        self.assertEqual((status, body), (200, {"status": "already_delivered"}))

    def test_an_unknown_challenge_id_is_404(self):
        core = self._core()
        base = self._start_server(core)
        status, body = _get(f"{base}/pairing/{'x' * 22}")
        self.assertEqual((status, body["error"]), (404, "unknown_challenge"))

    def test_polling_before_any_answer_reports_pending(self):
        core = self._core()
        base = self._start_server(core)
        _, start_body = _post(f"{base}/pairing/start", {})
        status, body = _get(f"{base}/pairing/{start_body['challenge_id']}")
        self.assertEqual((status, body), (200, {"status": "pending"}))


class RejectionTests(PairingEndToEndTestCase):
    def test_saying_no_rejects_immediately_the_phone_does_not_wait_for_the_5_minute_expiry(self):
        core = self._core()
        base = self._start_server(core)
        _, start_body = _post(f"{base}/pairing/start", {"requested_name": "Sconosciuto"})
        challenge_id = start_body["challenge_id"]

        core.answer("no")

        status, body = _get(f"{base}/pairing/{challenge_id}")
        self.assertEqual((status, body), (200, {"status": "rejected"}))
        self.assertEqual(self.store.list_devices(), [], "un pairing rifiutato non crea alcun dispositivo")

    def test_a_rejected_pairing_grants_no_credential_ever(self):
        core = self._core()
        base = self._start_server(core)
        _, start_body = _post(f"{base}/pairing/start", {})
        core.answer("no")
        self.assertEqual(self.store.list_devices(), [])


class AdminAuthGateTests(PairingEndToEndTestCase):
    """APPROVE_PAIRING e' ADMIN (core/risk.py): se l'utente ha configurato una passphrase, pairing un nuovo
    dispositivo la richiede DAVVERO, esattamente come ogni altra azione ADMIN - nessuna eccezione per questo
    intent (stesso gate centrale, non un percorso a parte)."""

    def _core_with_passphrase(self):
        auth_gate = AuthGate(passphrase="apri sesamo")
        return self._core(auth_gate=auth_gate, policy_engine=PolicyEngine(
            auth_gate=auth_gate, blocked_intents=[], always_confirm_intents=set(), require_auth_intents={"APPROVE_PAIRING"},
        ))

    def test_a_bare_yes_is_not_enough_when_a_passphrase_is_configured(self):
        core = self._core_with_passphrase()
        base = self._start_server(core)
        _, start_body = _post(f"{base}/pairing/start", {"requested_name": "Telefono"})
        challenge_id = start_body["challenge_id"]

        response = core.answer("sì")
        self.assertIn("passphrase", response.lower())
        status, body = _get(f"{base}/pairing/{challenge_id}")
        self.assertEqual(body["status"], "pending", "non ancora approvato: serve la passphrase")

    def test_the_correct_passphrase_completes_the_pairing(self):
        core = self._core_with_passphrase()
        base = self._start_server(core)
        _, start_body = _post(f"{base}/pairing/start", {"requested_name": "Telefono"})
        challenge_id = start_body["challenge_id"]

        core.answer("sì")
        core.answer("apri sesamo")

        status, body = _get(f"{base}/pairing/{challenge_id}")
        self.assertEqual(body["status"], "approved")


class SyncKeyRegistrationTests(PairingEndToEndTestCase):
    """F7.6 (chiude il gap dichiarato "scambio delle chiavi pubbliche dentro il pairing")."""

    def test_a_submitted_sync_public_key_is_registered_in_the_keyring_on_approval(self):
        core = self._core()
        base = self._start_server(core)
        device_keys = DeviceKeys.generate()
        _post(f"{base}/pairing/start", {"requested_name": "Telefono", "sync_public_key": _sync_public_key_payload(device_keys)})

        core.answer("sì")

        action_device_id = None
        for peer in core.sync_keyring.active():
            action_device_id = peer.device_id
        self.assertIsNotNone(action_device_id, "la chiave pubblica di sync doveva essere registrata")
        registered = core.sync_keyring.get(action_device_id)
        self.assertEqual(registered.keys.agree, device_keys.public.agree)
        self.assertEqual(registered.keys.sign, device_keys.public.sign)
        self.assertEqual(registered.profiles, set(), "nessun profilo concesso di default")

    def test_no_sync_public_key_means_nothing_is_registered(self):
        core = self._core()
        base = self._start_server(core)
        _post(f"{base}/pairing/start", {"requested_name": "Telefono"})
        core.answer("sì")
        self.assertEqual(core.sync_keyring.active(), [])

    def test_a_malformed_sync_public_key_is_rejected_before_anything_else(self):
        core = self._core()
        base = self._start_server(core)
        status, body = _post(f"{base}/pairing/start", {"sync_public_key": {"sign": "not b64!!", "agree": 5}})
        self.assertEqual((status, body["error"]), (400, "invalid_sync_public_key"))

    def test_a_valid_shaped_but_undecodable_key_does_not_block_the_pairing_itself(self):
        core = self._core()
        base = self._start_server(core)
        _post(f"{base}/pairing/start", {"sync_public_key": {"sign": "not-valid-base64!", "agree": "also-not-b64!"}})
        response = core.answer("sì")
        self.assertNotIn("errore", response.lower())
        self.assertEqual(core.sync_keyring.active(), [])
        self.assertEqual(len(self.store.list_devices()), 1, "il pairing deve riuscire comunque")


class RateLimitAndValidationTests(PairingEndToEndTestCase):
    def test_pairing_is_rate_limited_by_address_even_without_any_identity(self):
        core = self._core()
        base = self._start_server(core)
        statuses = [_post(f"{base}/pairing/start", {})[0] for _ in range(10)]
        self.assertIn(429, statuses)

    def test_an_oversized_requested_name_is_rejected(self):
        core = self._core()
        base = self._start_server(core)
        status, body = _post(f"{base}/pairing/start", {"requested_name": "x" * 500})
        self.assertEqual((status, body["error"]), (400, "requested_name_too_long"))

    def test_pairing_endpoints_are_404_when_the_server_has_no_pairing_service(self):
        server = CompanionServer()
        server.start()
        self.addCleanup(server.stop)
        base = f"http://127.0.0.1:{server.port}"
        status, body = _post(f"{base}/pairing/start", {})
        self.assertEqual((status, body["error"]), (404, "pairing_not_configured"))
        status, body = _get(f"{base}/pairing/anything")
        self.assertEqual((status, body["error"]), (404, "pairing_not_configured"))


if __name__ == "__main__":
    unittest.main()
