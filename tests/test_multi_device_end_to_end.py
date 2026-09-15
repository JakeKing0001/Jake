"""Test end-to-end multi-device (F1.4/F1.8.1, fase 10/10 - ULTIMA del piano concordato con
l'utente, vedi ROADMAP_EXECUTION.md sezione F1.4 per il testo completo della specifica).

Due scenari distinti, deliberatamente non mescolati:

1. `TwoDevicesPairThenActIndependentlyTests` compone i pezzi GIA' cablati in produzione -
   pairing (fase 4, `core/pairing_service.py`), credenziali per-dispositivo (fase 2,
   `core/device_credential_store.py`), autenticazione per-dispositivo su `companion_server.py`
   (fase 6) - in un unico scenario realistico con DUE dispositivi VERI, richieste HTTP vere
   (non simulate), fino all'isolamento delle azioni in sospeso per canale (F1.8.1, gia'
   esistente da prima di questa sessione) messo alla prova con credenziali AUTENTICATE, non
   solo un device_id auto-dichiarato nel body come prima della fase 6.

2. `ComposedMechanismsWorkTogetherTests` compone `ResourceLockManager` (fase 7) e
   `TaskRiskBudget` (fase 8), che restano deliberatamente NON cablati in un chokepoint di
   produzione (`TaskAgent`/`PlanExecutor` - passo successivo dichiarato in ciascuna delle due
   fasi) - qui si dimostra che i due meccanismi INTEROPERANO correttamente per un task simulato
   con piu' passi, non che l'agente reale li usa gia' oggi. Onesto per costruzione: nessuna
   affermazione di un collegamento che non esiste."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from urllib import error, request

from core.companion_server import CompanionServer
from core.conversation_state import ConversationStateManager
from core.device_credential_store import DeviceCredentialStore
from core.pairing_service import PairingService
from core.request_context import current_device_id
from core.resource_lock import ResourceLockManager
from core.risk import RiskLevel
from core.task_risk_budget import TaskRiskBudget


def _post(url: str, payload: dict, headers: dict = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    req = request.Request(url, data=body, headers=all_headers, method="POST")
    try:
        with request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TwoDevicesPairThenActIndependentlyTests(unittest.TestCase):
    def setUp(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_e2e_multi_device_test_"))
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        self.store = DeviceCredentialStore(db_path=tmp_dir / "devices.db")
        self.addCleanup(self.store.close)
        self.pairing = PairingService(self.store)
        self.conversation_state = ConversationStateManager()

        def _command_handler(text: str) -> str:
            if text.startswith("SET_PENDING:"):
                self.conversation_state.set_pending_action({"text": text, "device_id": current_device_id()})
                return "in attesa di conferma"
            if text == "TAKE_PENDING":
                return json.dumps(self.conversation_state.take_pending_action())
            return "ok"

        self.server = CompanionServer(command_handler=_command_handler, credential_store=self.store)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.base_url = f"http://127.0.0.1:{self.server.port}"

    def _pair_a_device(self, name: str):
        """In produzione questo e' un utente che approva sul PC dopo aver scansionato il QR
        (fasi 4/6) - il collegamento a un endpoint HTTP dedicato resta un passo successivo
        dichiarato, invocato qui direttamente: e' comunque il servizio di pairing GIA' reale a
        emettere la credenziale, non un doppio."""
        challenge = self.pairing.start_pairing(requested_name=name)
        return self.pairing.approve(challenge.challenge_id)

    def test_two_devices_pair_and_authenticate_independently(self):
        credential_a = self._pair_a_device("Telefono di Davide")
        credential_b = self._pair_a_device("Tablet")

        status_a, _ = _post(f"{self.base_url}/command", {"text": "ok"}, headers=_auth_header(credential_a.token))
        status_b, _ = _post(f"{self.base_url}/command", {"text": "ok"}, headers=_auth_header(credential_b.token))

        self.assertEqual(status_a, 200)
        self.assertEqual(status_b, 200)

    def test_device_b_cannot_see_or_confirm_device_as_pending_action(self):
        """Il risultato finale delle fasi 2/4/6 messe insieme: un'azione in sospeso creata da un
        dispositivo non e' mai visibile/confermabile da un ALTRO, ora con VERE credenziali
        per-dispositivo autenticate - non solo un device_id auto-dichiarato nel body (il buco
        reale chiuso in fase 6)."""
        credential_a = self._pair_a_device("Telefono di Davide")
        credential_b = self._pair_a_device("Tablet")

        _post(
            f"{self.base_url}/command", {"text": "SET_PENDING: cancella tutto"},
            headers=_auth_header(credential_a.token),
        )

        _, body_b = _post(
            f"{self.base_url}/command", {"text": "TAKE_PENDING"}, headers=_auth_header(credential_b.token),
        )
        self.assertIsNone(json.loads(body_b["response"]), "device-b ha visto l'azione in sospeso di device-a")

        _, body_a = _post(
            f"{self.base_url}/command", {"text": "TAKE_PENDING"}, headers=_auth_header(credential_a.token),
        )
        action = json.loads(body_a["response"])
        self.assertIsNotNone(action, "device-a deve poter confermare la PROPRIA azione")
        self.assertEqual(action["device_id"], credential_a.device_id)

    def test_revoking_device_b_does_not_affect_device_a(self):
        credential_a = self._pair_a_device("Telefono di Davide")
        credential_b = self._pair_a_device("Tablet")

        self.assertTrue(self.store.revoke(credential_b.device_id))

        status_b, _ = _post(f"{self.base_url}/command", {"text": "ok"}, headers=_auth_header(credential_b.token))
        status_a, _ = _post(f"{self.base_url}/command", {"text": "ok"}, headers=_auth_header(credential_a.token))

        self.assertEqual(status_b, 401, "il token revocato di device-b deve smettere di funzionare")
        self.assertEqual(status_a, 200, "revocare device-b non deve toccare device-a")

    def test_a_rejected_pairing_creates_no_device_and_grants_no_access(self):
        challenge = self.pairing.start_pairing()
        self.assertTrue(self.pairing.reject(challenge.challenge_id))
        self.assertEqual(self.store.list_devices(), [])


class ComposedMechanismsWorkTogetherTests(unittest.TestCase):
    """ResourceLockManager (fase 7) e TaskRiskBudget (fase 8): non ancora cablati in TaskAgent/
    PlanExecutor - qui si dimostra che COMPONGONO correttamente per un task simulato con piu'
    passi, non che l'agente reale li usa gia' oggi."""

    def test_a_simulated_multi_step_task_serializes_writes_and_flags_escalation(self):
        lock_manager = ResourceLockManager()
        budget = TaskRiskBudget(max_authorized_risk=RiskLevel.READ_ONLY)

        # Passo 1: leggi gli appunti (EXTERNAL_CONTENT_INTENTS) - una lettura, altre letture sulla
        # stessa risorsa potrebbero procedere in parallelo (fase 7).
        with lock_manager.acquire_read("clipboard:default"):
            budget.record_step("CLIPBOARD_READ", resource_keys=("clipboard:default",))

        # Passo 2: il task simulato vorrebbe ora inviare quel contenuto fuori - il risk budget lo
        # segnala PRIMA di eseguirlo (fase 8), indipendentemente da cosa deciderebbe PolicyEngine
        # per SEND_EMAIL da solo (non gated di default, il buco reale di F1.5.8).
        reason = budget.escalation_reason("SEND_EMAIL")
        self.assertIsNotNone(reason, "leggere gli appunti e poi inviarli fuori deve essere segnalato come escalation")

        # Se il chiamante decide comunque di procedere (dopo una conferma fresca, non simulata
        # qui - la specifica richiede solo che IL SEGNALE arrivi prima dell'esecuzione), la
        # scrittura sulla risorsa "app mail" resta comunque serializzata rispetto ad altre azioni
        # mutative sulla STESSA risorsa (fase 7).
        with lock_manager.acquire_write("app:mail_client"):
            budget.record_step("SEND_EMAIL", resource_keys=("app:mail_client",))

        self.assertEqual(budget.resources_touched, {"clipboard:default", "app:mail_client"})

    def test_two_simulated_devices_do_not_corrupt_a_shared_resource(self):
        """Stesso principio del test sopra, ma con DUE 'dispositivi' (thread) che tentano di
        mutare la STESSA risorsa nello stesso istante - thread veri, stesso principio gia' usato
        in tests/test_resource_lock.py."""
        import threading

        lock_manager = ResourceLockManager()
        log = []

        def _device_writes(device_name):
            with lock_manager.acquire_write("filesystem:/condiviso.txt"):
                log.append(f"{device_name}_start")
                log.append(f"{device_name}_end")

        threads = [threading.Thread(target=_device_writes, args=(name,)) for name in ("device-a", "device-b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # Ciascun dispositivo deve completare START+END senza intreccio (mai A_start, B_start,
        # A_end, B_end - solo A_start,A_end,B_start,B_end o l'ordine inverso).
        self.assertIn(log, (
            ["device-a_start", "device-a_end", "device-b_start", "device-b_end"],
            ["device-b_start", "device-b_end", "device-a_start", "device-a_end"],
        ))


if __name__ == "__main__":
    unittest.main()
