"""Test unitari per l'action ledger (F1, Trustworthy Agent Core 3.0 - vedi core/action_ledger.py
e la fase F1 in ROADMAP.md): a differenza di core/logger.log_action (F0), qui si verifica che
non ruoti mai e che authorization_of() derivi correttamente lo stato di autorizzazione dagli
stessi segnali gia' usati altrove, invece di essere dichiarato a mano da chi registra."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from core.action_ledger import (
    ACTION_RECEIPT_SCHEMA_VERSION, AUTHORIZATION_BLOCKED, AUTHORIZATION_CONFIRMED,
    AUTHORIZATION_DENIED, AUTHORIZATION_NONE,
    AUTHORIZATION_PASSPHRASE, AUTHORIZATION_PENDING, AUTHORIZATION_WINDOWS_HELLO,
    ERROR_CATEGORY_CONFLICT, ERROR_CATEGORY_DENIED, ERROR_CATEGORY_INVALID_INPUT, ERROR_CATEGORY_PENDING,
    ERROR_CATEGORY_SUCCESS, ERROR_CATEGORY_TIMEOUT, ERROR_CATEGORY_TRANSIENT,
    ERROR_CATEGORY_UNAVAILABLE, ERROR_CATEGORY_UNCATEGORIZED, ERROR_CATEGORY_USER_CANCELLED,
    ERROR_CATEGORY_VERIFICATION_FAILED, VERIFICATION_FAILED, VERIFICATION_UNVERIFIED,
    VERIFICATION_VERIFIED, ActionLedger, ActionReceipt, authorization_of, error_category_of,
    idempotency_key_of, new_action_id, validate_action_receipt, verification_status_of,
)


class NewActionIdTests(unittest.TestCase):
    def test_returns_a_non_empty_hex_string(self):
        action_id = new_action_id()
        self.assertTrue(action_id)
        int(action_id, 16)

    def test_two_calls_do_not_collide(self):
        self.assertNotEqual(new_action_id(), new_action_id())


class AuthorizationOfTests(unittest.TestCase):
    def test_no_authorization_needed_by_default(self):
        self.assertEqual(authorization_of("success", {}), AUTHORIZATION_NONE)

    def test_confirmed_parameter_means_confirmed(self):
        self.assertEqual(authorization_of("success", {"confirmed": True}), AUTHORIZATION_CONFIRMED)

    def test_authenticated_parameter_means_passphrase(self):
        """Senza authenticated_via (ricevute scritte prima che questo campo esistesse), il
        default resta "passphrase" per compatibilita' con quelle vecchie."""
        self.assertEqual(authorization_of("success", {"authenticated": True}), AUTHORIZATION_PASSPHRASE)

    def test_authenticated_via_windows_hello_is_distinguished_from_passphrase(self):
        self.assertEqual(
            authorization_of("success", {"authenticated": True, "authenticated_via": "windows_hello"}),
            AUTHORIZATION_WINDOWS_HELLO,
        )

    def test_authenticated_via_passphrase_is_explicit_passphrase(self):
        self.assertEqual(
            authorization_of("success", {"authenticated": True, "authenticated_via": "passphrase"}),
            AUTHORIZATION_PASSPHRASE,
        )

    def test_authenticated_wins_over_confirmed_when_both_present(self):
        """Un'azione ADMIN autenticata con passphrase ha anche 'confirmed' storicamente
        propagato da JakeCore._resolve_and_execute: passphrase e' il fattore piu' forte, deve
        vincere lei, non essere degradata a una semplice conferma si'/no."""
        self.assertEqual(
            authorization_of("success", {"confirmed": True, "authenticated": True}), AUTHORIZATION_PASSPHRASE,
        )

    def test_confirmation_required_is_pending_not_none(self):
        self.assertEqual(authorization_of("confirmation_required", {}), AUTHORIZATION_PENDING)
        self.assertEqual(authorization_of("auth_required", {}), AUTHORIZATION_PENDING)
        self.assertEqual(authorization_of("error:CONFIRMATION_REQUIRED", {"confirmed": True}), AUTHORIZATION_PENDING)
        self.assertEqual(authorization_of("error:AUTH_REQUIRED", {"authenticated": True}), AUTHORIZATION_PENDING)

    def test_blocked_by_policy_is_blocked(self):
        self.assertEqual(authorization_of("blocked_by_policy", {}), AUTHORIZATION_BLOCKED)
        self.assertEqual(authorization_of("policy_blocked", {}), AUTHORIZATION_BLOCKED)

    def test_prefixed_policy_block_overrides_even_verified_authorization_markers(self):
        for result in ("error:POLICY_BLOCKED", "POLICY_BLOCKED", " error:policy_blocked "):
            with self.subTest(result=result):
                self.assertEqual(authorization_of(result, {
                    "confirmed": True, "authenticated": True, "authenticated_via": "windows_hello",
                }), AUTHORIZATION_BLOCKED)

    def test_denied_results_are_denied_not_pending(self):
        """Un diniego vero (passphrase sbagliata, "no" a una conferma) e' distinto da
        AUTHORIZATION_PENDING: li' l'azione aspetta ancora una risposta, qui non partira' piu'
        per questo turno (vedi ROADMAP.md, F1: "un diniego e' comunque un evento di sicurezza
        degno di una ricevuta")."""
        self.assertEqual(authorization_of("denied_auth", {}), AUTHORIZATION_DENIED)
        self.assertEqual(authorization_of("denied_confirmation", {}), AUTHORIZATION_DENIED)
        self.assertEqual(authorization_of("error:DENIED_AUTH", {"authenticated": True}), AUTHORIZATION_DENIED)
        self.assertEqual(authorization_of("error:DENIED_CONFIRMATION", {"confirmed": True}), AUTHORIZATION_DENIED)

    def test_none_parameters_does_not_crash(self):
        self.assertEqual(authorization_of("success", None), AUTHORIZATION_NONE)

    def test_escalation_detected_is_pending_not_denied(self):
        """F1.5.8 (adozione): stessa categoria di CONFIRMATION_REQUIRED - una catena di passi ha
        reso il prossimo troppo rischioso senza una conferma FRESCA (core/task_risk_budget.py),
        concettualmente 'in attesa', non un diniego gia' avvenuto."""
        self.assertEqual(authorization_of("escalation_detected", {}), AUTHORIZATION_PENDING)
        self.assertEqual(authorization_of("error:ESCALATION_DETECTED", {}), AUTHORIZATION_PENDING)


class IdempotencyKeyOfTests(unittest.TestCase):
    def test_same_intent_and_parameters_produce_the_same_key(self):
        key1 = idempotency_key_of("OPEN_APP", {"name": "spotify"})
        key2 = idempotency_key_of("OPEN_APP", {"name": "spotify"})
        self.assertEqual(key1, key2)

    def test_parameter_order_does_not_matter(self):
        key1 = idempotency_key_of("CREATE_PATH", {"path": "x", "confirmed": True})
        key2 = idempotency_key_of("CREATE_PATH", {"confirmed": True, "path": "x"})
        self.assertEqual(key1, key2)

    def test_different_parameters_produce_different_keys(self):
        key1 = idempotency_key_of("OPEN_APP", {"name": "spotify"})
        key2 = idempotency_key_of("OPEN_APP", {"name": "discord"})
        self.assertNotEqual(key1, key2)

    def test_different_intents_produce_different_keys_even_with_the_same_parameters(self):
        key1 = idempotency_key_of("OPEN_APP", {"name": "x"})
        key2 = idempotency_key_of("CLOSE_APP", {"name": "x"})
        self.assertNotEqual(key1, key2)

    def test_none_parameters_does_not_crash(self):
        self.assertTrue(idempotency_key_of("HELP", None))


class ActionReceiptTests(unittest.TestCase):
    def test_to_json_omits_none_fields(self):
        receipt = ActionReceipt(
            action_id="a1", trace_id="t1", ts=123.0, intent="OPEN_APP", requested_by="user",
            risk_decision="local_reversible", authorization="none", result="success",
            idempotency_key="k1",
        )
        import json
        record = json.loads(receipt.to_json())
        self.assertNotIn("duration_ms", record)
        self.assertNotIn("model", record)
        self.assertNotIn("device_id", record)
        self.assertEqual(record["action_id"], "a1")

    def test_device_id_round_trips_through_to_json(self):
        """F1.2.3/F1.8.1 (fondamenta): il device_id del dispositivo companion mittente (vedi
        core/request_context.py) e' un campo puramente informativo del ledger, come
        policy_reason/duration_ms/model - additivo, mai obbligatorio."""
        receipt = ActionReceipt(
            action_id="a1", trace_id="t1", ts=123.0, intent="OPEN_APP", requested_by="user",
            risk_decision="local_reversible", authorization="none", result="success",
            idempotency_key="k1", device_id="phone1",
        )
        import json
        record = json.loads(receipt.to_json())
        self.assertEqual(record["device_id"], "phone1")

    def test_verified_defaults_to_unverified_and_is_never_omitted(self):
        """F1.3.3: prima di questa correzione verified era Optional[bool] = None e to_json()
        ometteva il campo quando None - una ricevuta "mai verificata" finiva nel ledger identica
        a una scritta da uno schema piu' vecchio senza questo campo. Ora e' sempre presente."""
        receipt = ActionReceipt(
            action_id="a1", trace_id="t1", ts=123.0, intent="OPEN_APP", requested_by="user",
            risk_decision="local_reversible", authorization="none", result="success",
            idempotency_key="k1",
        )
        self.assertEqual(receipt.verified, VERIFICATION_UNVERIFIED)
        import json
        record = json.loads(receipt.to_json())
        self.assertEqual(record["verified"], VERIFICATION_UNVERIFIED)


class VerificationStatusOfTests(unittest.TestCase):
    def test_none_means_unverified(self):
        self.assertEqual(verification_status_of(None), VERIFICATION_UNVERIFIED)

    def test_true_means_verified(self):
        self.assertEqual(verification_status_of(True), VERIFICATION_VERIFIED)

    def test_false_means_verification_failed(self):
        self.assertEqual(verification_status_of(False), VERIFICATION_FAILED)


class ErrorCategoryOfTests(unittest.TestCase):
    """F1.1.4 ("definire tassonomia errori"): error_category_of() deve capire i formati REALI
    usati dai quattro chokepoint che scrivono una ricevuta (vedi core/action_ledger.py,
    _KNOWN_RESULT_CATEGORIES) - non solo una forma canonica ipotetica."""

    def test_success_is_its_own_category(self):
        self.assertEqual(error_category_of("success"), ERROR_CATEGORY_SUCCESS)

    def test_confirmation_required_and_auth_required_are_pending(self):
        self.assertEqual(error_category_of("confirmation_required"), ERROR_CATEGORY_PENDING)
        self.assertEqual(error_category_of("auth_required"), ERROR_CATEGORY_PENDING)

    def test_escalation_detected_is_also_pending(self):
        """F1.5.8 (adozione): stessa categoria di CONFIRMATION_REQUIRED sopra - vedi
        AuthorizationOfTests.test_escalation_detected_is_pending_not_denied per il perche'."""
        self.assertEqual(error_category_of("escalation_detected"), ERROR_CATEGORY_PENDING)
        self.assertEqual(error_category_of("error:ESCALATION_DETECTED"), ERROR_CATEGORY_PENDING)

    def test_policy_and_auth_denials_are_denied(self):
        for result in ("blocked_by_policy", "policy_blocked", "denied_auth", "denied_confirmation"):
            self.assertEqual(error_category_of(result), ERROR_CATEGORY_DENIED, result)

    def test_error_prefix_is_stripped_and_case_preserved_from_the_skill_is_ignored(self):
        """PlanExecutor scrive 'error:VERIFICATION_FAILED' (maiuscolo, con prefisso), TaskAgent
        a volte 'missing_parameters' (minuscolo, senza prefisso) per lo stesso genere di errore -
        vedi il commento su _KNOWN_RESULT_CATEGORIES in core/action_ledger.py. Entrambi i formati
        devono risolvere alla stessa categoria."""
        self.assertEqual(error_category_of("error:VERIFICATION_FAILED"), ERROR_CATEGORY_VERIFICATION_FAILED)
        self.assertEqual(error_category_of("missing_parameters"), ERROR_CATEGORY_INVALID_INPUT)
        self.assertEqual(error_category_of("error:MISSING_PARAMETERS"), ERROR_CATEGORY_INVALID_INPUT)

    def test_retryable_errors_are_transient(self):
        """Le stesse due costanti di core/execution_safety.py::RETRYABLE_ERRORS."""
        self.assertEqual(error_category_of("error:OPERATION_FAILED"), ERROR_CATEGORY_TRANSIENT)
        self.assertEqual(error_category_of("error:NETWORK_UNAVAILABLE"), ERROR_CATEGORY_TRANSIENT)

    def test_timeout_is_its_own_category(self):
        self.assertEqual(error_category_of("error:TIMEOUT"), ERROR_CATEGORY_TIMEOUT)

    def test_ollama_and_planner_unavailability_are_unavailable(self):
        self.assertEqual(error_category_of("error:OLLAMA_UNAVAILABLE"), ERROR_CATEGORY_UNAVAILABLE)
        self.assertEqual(error_category_of("error:PLANNER_ERROR"), ERROR_CATEGORY_UNAVAILABLE)

    def test_killed_is_user_cancelled_not_a_system_error(self):
        self.assertEqual(error_category_of("error:KILLED"), ERROR_CATEGORY_USER_CANCELLED)

    def test_a_code_truly_never_seen_before_falls_back_to_uncategorized(self):
        """Un codice MAI usato da nessuna skill (non solo non ancora migrato - proprio inventato
        per questo test): dichiarato onestamente, non forzato in una categoria a caso."""
        self.assertEqual(error_category_of("error:UN_CODICE_MAI_VISTO_XYZ"), ERROR_CATEGORY_UNCATEGORIZED)

    def test_empty_result_falls_back_to_uncategorized(self):
        self.assertEqual(error_category_of(""), ERROR_CATEGORY_UNCATEGORIZED)


class BespokeSkillErrorCodesAreCategorizedTests(unittest.TestCase):
    """F1.1.7 (seconda meta': "migrare gli errori delle ~200 skill sulla tassonomia condivisa"):
    i 35 codici bespoke REALMENTE usati dalle skill (`grep -rhoE 'error="[A-Z_]+"' skills/*.py`,
    verificato uno per uno, non ipotizzato) e non ancora coperti da _KNOWN_RESULT_CATEGORIES prima
    di questo incremento. Nessun file di skill e' stato toccato: sono le stesse stringhe che
    result.error contiene gia' oggi, solo ora riconosciute da error_category_of() invece di
    ricadere silenziosamente su "uncategorized" (vedi core/jake_core.py::_execute_command(),
    che costruisce 'error:{result.error}' per ogni chokepoint gia' esistente)."""

    def test_unavailable_dependency_codes(self):
        for code in (
            "AUDIO_UNAVAILABLE", "BRIGHTNESS_UNAVAILABLE", "BROWSER_HISTORY_UNAVAILABLE",
            "HOME_ASSISTANT_UNAVAILABLE", "MISSING_API_KEY", "NEST_UNAVAILABLE",
            "OCR_UNAVAILABLE", "VISION_UNAVAILABLE",
        ):
            self.assertEqual(error_category_of(f"error:{code}"), ERROR_CATEGORY_UNAVAILABLE, code)

    def test_invalid_input_codes(self):
        for code in (
            "CITY_NOT_FOUND", "CLIPBOARD_EMPTY", "CONTACT_NOT_FOUND", "CURRENCY_NOT_FOUND",
            "INCOMPATIBLE_UNITS", "INVALID_DATE", "INVALID_EXPRESSION", "INVALID_JSON",
            "INVALID_TIME", "INVALID_URL", "INVALID_VALUE", "NOT_A_GIT_REPO", "NOT_FOUND",
            "NO_SELECTION", "PATH_NOT_FOUND", "RESULT_NOT_FOUND", "UNSUPPORTED_APP",
            "VOICE_ONLY", "WINDOW_NOT_FOUND",
        ):
            self.assertEqual(error_category_of(f"error:{code}"), ERROR_CATEGORY_INVALID_INPUT, code)

    def test_denied_codes(self):
        for code in ("BLOCKED", "PROTECTED_PATH"):
            self.assertEqual(error_category_of(f"error:{code}"), ERROR_CATEGORY_DENIED, code)

    def test_transient_codes(self):
        for code in (
            "FORGE_FAILED", "HOME_ASSISTANT_ERROR", "HOST_UNREACHABLE", "LAUNCH_FAILED",
            "NEST_ERROR", "PLAN_FAILED",
        ):
            self.assertEqual(error_category_of(f"error:{code}"), ERROR_CATEGORY_TRANSIENT, code)

    def test_already_exists_is_a_conflict(self):
        self.assertEqual(error_category_of("error:ALREADY_EXISTS"), ERROR_CATEGORY_CONFLICT)


class ActionReceiptErrorCategoryValidationTests(unittest.TestCase):
    def _receipt(self, **overrides) -> ActionReceipt:
        defaults = {
            "action_id": "a1", "trace_id": "t1", "ts": 123.0, "intent": "OPEN_APP", "requested_by": "user",
            "risk_decision": "local_reversible", "authorization": "none", "result": "success",
            "idempotency_key": "k1",
        }
        defaults.update(overrides)
        return ActionReceipt(**defaults)

    def test_default_error_category_is_uncategorized(self):
        self.assertEqual(self._receipt().error_category, ERROR_CATEGORY_UNCATEGORIZED)

    def test_valid_error_category_passes_validation(self):
        validate_action_receipt(self._receipt(error_category=ERROR_CATEGORY_SUCCESS))

    def test_unrecognized_error_category_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_action_receipt(self._receipt(error_category="not_a_real_category"))


class ActionReceiptPolicyReasonValidationTests(unittest.TestCase):
    """F1.2.6: policy_reason e' facoltativo (None per i chokepoint che non lo popolano ancora -
    vedi il docstring del campo in core/action_ledger.py) ma, quando presente, deve essere una
    delle quattro costanti di core.policy_engine.POLICY_REASONS - un vocabolario chiuso, mai
    testo libero (e' proprio questo che rende impossibile un segreto li' dentro)."""

    def _receipt(self, **overrides) -> ActionReceipt:
        defaults = {
            "action_id": "a1", "trace_id": "t1", "ts": 123.0, "intent": "OPEN_APP", "requested_by": "user",
            "risk_decision": "local_reversible", "authorization": "none", "result": "success",
            "idempotency_key": "k1",
        }
        defaults.update(overrides)
        return ActionReceipt(**defaults)

    def test_default_policy_reason_is_none(self):
        self.assertIsNone(self._receipt().policy_reason)

    def test_none_policy_reason_passes_validation(self):
        validate_action_receipt(self._receipt(policy_reason=None))

    def test_a_known_policy_reason_passes_validation(self):
        from core.policy_engine import POLICY_REASON_ALLOWED

        validate_action_receipt(self._receipt(policy_reason=POLICY_REASON_ALLOWED))

    def test_an_unrecognized_policy_reason_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_action_receipt(self._receipt(policy_reason="not_a_real_reason"))


class ActionLedgerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.path = Path(self._tmpdir.name) / "ledger.jsonl"
        self.ledger = ActionLedger(path=self.path)

    def _receipt(self, **overrides) -> ActionReceipt:
        defaults = {
            "action_id": "a1", "trace_id": "t1", "ts": 123.0, "intent": "OPEN_APP", "requested_by": "user",
            "risk_decision": "local_reversible", "authorization": "none", "result": "success",
            "idempotency_key": "k1",
        }
        defaults.update(overrides)
        return ActionReceipt(**defaults)


class RecordTests(ActionLedgerTestCase):
    def test_record_appends_a_json_line(self):
        self.ledger.record(self._receipt())
        records = self.ledger.read_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["intent"], "OPEN_APP")

    def test_record_appends_without_truncating_previous_entries(self):
        """Diverso da un log che ruota (core/logger.py): il ledger non deve mai perdere voci
        vecchie scrivendone di nuove."""
        for i in range(5):
            self.ledger.record(self._receipt(action_id=f"a{i}"))
        records = self.ledger.read_all()
        self.assertEqual(len(records), 5)
        self.assertEqual([r["action_id"] for r in records], [f"a{i}" for i in range(5)])

    def test_private_flag_suppresses_recording(self):
        self.ledger.record(self._receipt(), private=True)
        self.assertEqual(self.ledger.read_all(), [])


class PreSchemaVersionRecordCompatibilityTests(ActionLedgerTestCase):
    """F1.1.5 ("versionare lo schema e aggiungere migrazione/compatibilita' per record
    precedenti"): schema_version esiste da quando ActionReceipt esiste - non c'e' MAI stata,
    in produzione, una seconda versione dello schema da cui migrare (validate_action_receipt
    gia' rifiuta in scrittura una versione diversa da quella attuale, vedi
    tests/test_action_contract.py::test_unsupported_schema_version_is_rejected - costruire una
    trasformazione per una v2 che non esiste sarebbe codice morto speculativo). Cio' che resta
    da verificare e' la "compatibilita' per record precedenti": una riga scritta PRIMA che il
    campo schema_version esistesse (o comunque priva di quella chiave) deve restare leggibile,
    non scartata ne' far sollevare un'eccezione - read_all()/by_* non validano mai una riga letta
    da disco (solo un ActionReceipt in costruzione passa da validate_action_receipt), quindi
    questo e' gia' vero per costruzione; qui lo si prova con un file scritto a mano, non con
    ActionLedger.record() (che scriverebbe sempre la versione corrente)."""

    def test_a_record_written_before_schema_version_existed_is_still_read(self):
        pre_existing_line = json.dumps({
            "action_id": "old1", "trace_id": "t-old", "ts": 100.0, "intent": "OPEN_APP",
            "requested_by": "user", "risk_decision": "local_reversible", "authorization": "none",
            "result": "success", "idempotency_key": "k-old",
        }, ensure_ascii=False)
        self.path.write_text(pre_existing_line + "\n", encoding="utf-8")

        records = self.ledger.read_all()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["action_id"], "old1")
        self.assertNotIn("schema_version", records[0], "una riga vecchia non ha mai avuto questa chiave")

    def test_an_old_record_and_a_new_one_coexist_in_the_same_file(self):
        pre_existing_line = json.dumps({
            "action_id": "old1", "trace_id": "t-old", "ts": 100.0, "intent": "OPEN_APP",
            "requested_by": "user", "risk_decision": "local_reversible", "authorization": "none",
            "result": "success", "idempotency_key": "k-old",
        }, ensure_ascii=False)
        self.path.write_text(pre_existing_line + "\n", encoding="utf-8")

        self.ledger.record(self._receipt(action_id="new1"))
        records = self.ledger.read_all()

        self.assertEqual([r["action_id"] for r in records], ["old1", "new1"])
        self.assertNotIn("schema_version", records[0])
        self.assertEqual(records[1]["schema_version"], ACTION_RECEIPT_SCHEMA_VERSION)


class TruncatedLastLineTests(ActionLedgerTestCase):
    """F1.7.1 ("rendere ledger append-only resistente a record parziali e arresto improvviso"):
    buco reale, non solo teorico. Prima di questo fix, record() apriva sempre il file in append e
    scriveva la riga cosi' com'era, senza controllare se l'ultimo byte gia' su disco fosse un
    newline. Un arresto improvviso a meta' di una write() precedente lascia un ultimo record
    troncato SENZA newline finale: la prossima chiamata a record() si concatenava sulla stessa
    riga fisica, producendo un'unica riga JSON non valida che read_all() scarta per intero -
    perdendo non solo il record vecchio (gia' perso comunque) ma anche quello NUOVO, scritto con
    successo subito dopo il riavvio."""

    def _simulate_crash_mid_write(self, partial_json: str) -> None:
        # Stessa cosa che un kill/crash del processo produce davvero: bytes scritti sul disco
        # senza il newline finale che handle.write(line) avrebbe aggiunto se la write fosse
        # arrivata in fondo.
        self.ledger.record(self._receipt(action_id="before-crash"))
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(partial_json)

    def test_a_truncated_last_line_no_longer_swallows_the_next_real_record(self):
        self._simulate_crash_mid_write('{"action_id": "mid-crash", "trace_id": "t2"')
        self.ledger.record(self._receipt(action_id="after-restart"))

        records = self.ledger.read_all()
        ids = [r["action_id"] for r in records]
        self.assertIn("before-crash", ids)
        self.assertIn("after-restart", ids, "il record scritto dopo il riavvio non deve sparire")
        self.assertEqual(len(records), 2, "il solo record troncato va perso, non quello successivo")

    def test_the_truncated_line_itself_is_physically_isolated_on_its_own_line(self):
        self._simulate_crash_mid_write('{"action_id": "mid-crash"')
        self.ledger.record(self._receipt(action_id="after-restart"))

        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3, "riga troncata + separatore = 2 righe fisiche distinte")
        self.assertIn('"mid-crash"', lines[1])
        self.assertIn('"after-restart"', lines[2])

    def test_a_file_already_ending_in_newline_gets_no_extra_blank_line(self):
        self.ledger.record(self._receipt(action_id="a1"))
        self.ledger.record(self._receipt(action_id="a2"))
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)

    def test_recording_into_a_brand_new_file_adds_no_leading_separator(self):
        self.ledger.record(self._receipt(action_id="a1"))
        content = self.path.read_text(encoding="utf-8")
        self.assertFalse(content.startswith("\n"))


class ConcurrentWritesTests(ActionLedgerTestCase):
    """F1.8.2 ("serializzare azioni che toccano lo stesso resource key"): buco reale, non solo
    teorico - record() apriva il file con un open() grezzo a ogni chiamata, senza alcuna
    sincronizzazione tra thread. ActionLedger e' condivisa PER RIFERIMENTO tra JakeCore,
    TaskAgent, PlanExecutor e TriggerScheduler (quest'ultimo su un thread separato): un'
    automazione partita da sola mentre il thread principale registra un comando diretto poteva
    intrecciare le due scritture nello stesso file, producendo righe JSON corrotte in un
    registro che per design non deve mai perderne ne' corromperne una (vedi il docstring del
    modulo)."""

    THREAD_COUNT = 20
    RECORDS_PER_THREAD = 20

    def test_many_threads_writing_concurrently_produce_no_corrupted_lines(self):
        """Stress reale su file vero: ogni riga scritta deve restare JSON valido e nessuna deve
        andare persa, anche con piu' thread che scrivono nello stesso istante."""
        barrier = threading.Barrier(self.THREAD_COUNT)

        def _write_many(thread_index: int):
            barrier.wait()  # massimizza la sovrapposizione reale, non affidata al caso
            for i in range(self.RECORDS_PER_THREAD):
                self.ledger.record(self._receipt(action_id=f"t{thread_index}-{i}"))

        threads = [threading.Thread(target=_write_many, args=(i,)) for i in range(self.THREAD_COUNT)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        records = self.ledger.read_all()  # read_all() salta le righe non-JSON: una corruzione le farebbe sparire
        self.assertEqual(len(records), self.THREAD_COUNT * self.RECORDS_PER_THREAD)
        self.assertEqual(len({r["action_id"] for r in records}), self.THREAD_COUNT * self.RECORDS_PER_THREAD)

    def test_the_write_lock_enforces_mutual_exclusion_deterministically(self):
        """Non affidato alla fortuna del timing come il test sopra: sostituisce il lock vero con
        uno che registra se mai due thread si sono trovati DENTRO la sezione critica nello stesso
        istante - dimostra l'invariante (mutua esclusione), non solo la sua conseguenza
        probabile."""

        class _OverlapDetectingLock:
            def __init__(self):
                self._real_lock = threading.Lock()
                self._active = 0
                self._guard = threading.Lock()
                self.overlap_detected = False

            def __enter__(self):
                self._real_lock.acquire()
                with self._guard:
                    self._active += 1
                    if self._active > 1:
                        self.overlap_detected = True
                return self

            def __exit__(self, *exc_info):
                with self._guard:
                    self._active -= 1
                self._real_lock.release()
                return False

        tracking_lock = _OverlapDetectingLock()
        self.ledger._write_lock = tracking_lock
        barrier = threading.Barrier(self.THREAD_COUNT)

        def _write_many(thread_index: int):
            barrier.wait()
            for i in range(self.RECORDS_PER_THREAD):
                self.ledger.record(self._receipt(action_id=f"t{thread_index}-{i}"))

        threads = [threading.Thread(target=_write_many, args=(i,)) for i in range(self.THREAD_COUNT)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertFalse(tracking_lock.overlap_detected, "due thread erano dentro la sezione critica insieme")


class QueryTests(ActionLedgerTestCase):
    def test_by_trace_id_filters_correctly(self):
        self.ledger.record(self._receipt(action_id="a1", trace_id="shared"))
        self.ledger.record(self._receipt(action_id="a2", trace_id="shared"))
        self.ledger.record(self._receipt(action_id="a3", trace_id="other"))

        results = self.ledger.by_trace_id("shared")

        self.assertEqual({r["action_id"] for r in results}, {"a1", "a2"})

    def test_by_action_id_returns_the_matching_record_or_none(self):
        self.ledger.record(self._receipt(action_id="a1"))

        self.assertEqual(self.ledger.by_action_id("a1")["action_id"], "a1")
        self.assertIsNone(self.ledger.by_action_id("does-not-exist"))

    def test_by_idempotency_key_filters_correctly(self):
        self.ledger.record(self._receipt(action_id="a1", idempotency_key="k1"))
        self.ledger.record(self._receipt(action_id="a2", idempotency_key="k1"))
        self.ledger.record(self._receipt(action_id="a3", idempotency_key="k2"))

        results = self.ledger.by_idempotency_key("k1")

        self.assertEqual({r["action_id"] for r in results}, {"a1", "a2"})


class DuplicateIdempotencyKeysTests(ActionLedgerTestCase):
    def test_two_receipts_with_the_same_key_close_in_time_are_flagged(self):
        self.ledger.record(self._receipt(action_id="a1", idempotency_key="k1", ts=100.0))
        self.ledger.record(self._receipt(action_id="a2", idempotency_key="k1", ts=101.0))

        duplicates = self.ledger.duplicate_idempotency_keys(within_seconds=60)

        self.assertIn("k1", duplicates)
        self.assertEqual({r["action_id"] for r in duplicates["k1"]}, {"a1", "a2"})

    def test_same_key_far_apart_in_time_is_not_flagged(self):
        self.ledger.record(self._receipt(action_id="a1", idempotency_key="k1", ts=100.0))
        self.ledger.record(self._receipt(action_id="a2", idempotency_key="k1", ts=10000.0))

        duplicates = self.ledger.duplicate_idempotency_keys(within_seconds=60)

        self.assertNotIn("k1", duplicates)

    def test_single_occurrence_is_not_a_duplicate(self):
        self.ledger.record(self._receipt(action_id="a1", idempotency_key="k1", ts=100.0))

        self.assertEqual(self.ledger.duplicate_idempotency_keys(), {})

    def test_receipts_without_an_idempotency_key_are_ignored(self):
        self.ledger.record(self._receipt(action_id="a1", idempotency_key=None, ts=100.0))
        self.ledger.record(self._receipt(action_id="a2", idempotency_key=None, ts=101.0))

        self.assertEqual(self.ledger.duplicate_idempotency_keys(), {})


if __name__ == "__main__":
    unittest.main()
