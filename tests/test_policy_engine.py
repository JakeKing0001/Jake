"""Test unitari per core/policy_engine.py (F1, Trustworthy Agent Core 3.0 - "separazione formale
planner/policy engine/executor" in ROADMAP.md, fase F1).

Prima di questo modulo la stessa decisione era scritta a mano in piu' posti - JakeCore.
_resolve_and_execute (percorso interattivo), PlanExecutor.execute (percorso automatico),
RunWorkflowSkill (che non la applicava affatto) - e piu' bug reali sono nati esattamente da
quel disallineamento (vedi ROADMAP.md F1). PolicyEngine e' un oggetto con stato (blocked_intents/
always_confirm_intents/require_auth_intents/auth_gate), condiviso PER RIFERIMENTO da tutto cio'
che deve decidere - un riferimento solo da collegare, non piu' insiemi separati che un nuovo
consumatore puo' dimenticare di collegare a meta' (esattamente il difetto strutturale dietro il
bug di RunWorkflowSkill). Questi test coprono la logica in isolamento; tests/test_jake_core_
permissions.py, tests/test_plan_executor.py e tests/test_workflow_skills.py coprono che i
consumatori reali la usino davvero."""
import tempfile
import unittest
from pathlib import Path

from core.auth_gate import AuthGate
from core.policy_engine import PolicyDecision, PolicyEngine, strip_authorization_signals
from core.request_context import reset_current_device_id, set_current_device_id


class StripAuthorizationSignalsTests(unittest.TestCase):
    def test_removes_all_three_signal_keys(self):
        cleaned = strip_authorization_signals({
            "path": "C:/tmp/file.txt", "confirmed": True, "authenticated": True,
            "authenticated_via": "passphrase", "destination": "C:/tmp",
        })
        self.assertEqual(cleaned, {"path": "C:/tmp/file.txt", "destination": "C:/tmp"})

    def test_none_parameters_does_not_crash(self):
        self.assertEqual(strip_authorization_signals(None), {})

    def test_does_not_mutate_the_original_dict(self):
        original = {"confirmed": True, "path": "x"}
        strip_authorization_signals(original)
        self.assertEqual(original, {"confirmed": True, "path": "x"})


class ConstructorDefaultsTests(unittest.TestCase):
    def test_defaults_to_empty_sets_and_no_auth_gate(self):
        engine = PolicyEngine()
        self.assertEqual(engine.blocked_intents, set())
        self.assertEqual(engine.always_confirm_intents, set())
        self.assertEqual(engine.require_auth_intents, set())
        self.assertIsNone(engine.auth_gate)

    def test_constructor_copies_the_given_sets_not_aliases_them(self):
        """Passare un set esistente al costruttore non deve far si' che PolicyEngine e il
        chiamante finiscano per condividere lo STESSO oggetto set a loro insaputa."""
        original = {"SOME_INTENT"}
        engine = PolicyEngine(blocked_intents=original)
        engine.blocked_intents.add("ALTRO_INTENT")
        self.assertNotIn("ALTRO_INTENT", original)


class RegisterIntentTests(unittest.TestCase):
    def test_admin_intent_is_added_to_both_sets(self):
        engine = PolicyEngine()
        engine.register_intent("SOME_NEW_ADMIN_INTENT")
        self.assertIn("SOME_NEW_ADMIN_INTENT", engine.always_confirm_intents)
        self.assertIn("SOME_NEW_ADMIN_INTENT", engine.require_auth_intents)

    def test_read_only_intent_is_added_to_neither(self):
        engine = PolicyEngine()
        engine.register_intent("GET_TIME")
        self.assertNotIn("GET_TIME", engine.always_confirm_intents)
        self.assertNotIn("GET_TIME", engine.require_auth_intents)

    def test_destructive_intent_is_added_to_confirm_but_not_auth(self):
        engine = PolicyEngine()
        engine.register_intent("DELETE_TODO")
        self.assertIn("DELETE_TODO", engine.always_confirm_intents)
        self.assertNotIn("DELETE_TODO", engine.require_auth_intents)

    def test_self_confirming_intent_is_added_to_neither(self):
        """DELETE_PATH e' DESTRUCTIVE ma si autoconferma (core/risk.py, SELF_CONFIRMING_INTENTS):
        il gate centrale non deve intervenire, altrimenti l'utente vedrebbe un 'confermi?' spoglio
        prima che la skill controlli se l'azione e' persino possibile."""
        engine = PolicyEngine()
        engine.register_intent("DELETE_PATH")
        self.assertNotIn("DELETE_PATH", engine.always_confirm_intents)
        self.assertNotIn("DELETE_PATH", engine.require_auth_intents)


class FakeSkillRegistry:
    def __init__(self, intents):
        self.skills = {intent: object() for intent in intents}


class SyncWithRegistryTests(unittest.TestCase):
    def test_registers_every_intent_in_the_registry(self):
        engine = PolicyEngine()
        registry = FakeSkillRegistry(["DELETE_TODO", "GET_TIME", "SOME_NEW_ADMIN_INTENT"])

        engine.sync_with_registry(registry)

        self.assertIn("DELETE_TODO", engine.always_confirm_intents)
        self.assertIn("SOME_NEW_ADMIN_INTENT", engine.require_auth_intents)
        self.assertNotIn("GET_TIME", engine.always_confirm_intents)


class DecideInteractiveTests(unittest.TestCase):
    def test_blocked_intent_is_blocked_regardless_of_everything_else(self):
        engine = PolicyEngine(blocked_intents={"SOME_INTENT"}, auth_gate=AuthGate(passphrase="x"))

        decision = engine.decide_interactive("SOME_INTENT", {"confirmed": True, "authenticated": True})

        self.assertEqual(decision, PolicyDecision.BLOCK)

    def test_plain_intent_is_allowed(self):
        engine = PolicyEngine(auth_gate=AuthGate())

        self.assertEqual(engine.decide_interactive("GET_TIME", {}), PolicyDecision.ALLOW)

    def test_always_confirm_intent_without_confirmed_needs_confirm(self):
        engine = PolicyEngine(always_confirm_intents={"DELETE_TODO"}, auth_gate=AuthGate())

        self.assertEqual(engine.decide_interactive("DELETE_TODO", {}), PolicyDecision.CONFIRM)

    def test_always_confirm_intent_with_confirmed_is_allowed(self):
        engine = PolicyEngine(always_confirm_intents={"DELETE_TODO"}, auth_gate=AuthGate())

        decision = engine.decide_interactive("DELETE_TODO", {"confirmed": True})

        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_require_auth_intent_needs_auth_when_gate_enabled(self):
        engine = PolicyEngine(
            require_auth_intents={"SET_POWER_PLAN"}, auth_gate=AuthGate(passphrase="apri sesamo"),
        )

        decision = engine.decide_interactive("SET_POWER_PLAN", {})

        self.assertEqual(decision, PolicyDecision.REQUIRE_AUTH)

    def test_require_auth_intent_falls_back_to_confirm_when_gate_disabled(self):
        """Se l'utente non ha mai attivato passphrase/Windows Hello (AuthGate.enabled=False),
        un intent ADMIN resta comunque protetto dalla conferma si'/no ordinaria - nessun vicolo
        cieco senza via d'uscita."""
        engine = PolicyEngine(
            always_confirm_intents={"SET_POWER_PLAN"}, require_auth_intents={"SET_POWER_PLAN"},
            auth_gate=AuthGate(),
        )

        decision = engine.decide_interactive("SET_POWER_PLAN", {})

        self.assertEqual(decision, PolicyDecision.CONFIRM)

    def test_authenticated_parameter_bypasses_require_auth(self):
        engine = PolicyEngine(
            require_auth_intents={"SET_POWER_PLAN"}, auth_gate=AuthGate(passphrase="apri sesamo"),
        )

        decision = engine.decide_interactive("SET_POWER_PLAN", {"authenticated": True})

        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_none_auth_gate_is_treated_as_disabled(self):
        engine = PolicyEngine(require_auth_intents={"SET_POWER_PLAN"}, auth_gate=None)

        self.assertEqual(engine.decide_interactive("SET_POWER_PLAN", {}), PolicyDecision.ALLOW)

    def test_none_parameters_does_not_crash(self):
        engine = PolicyEngine(auth_gate=AuthGate())

        self.assertEqual(engine.decide_interactive("GET_TIME", None), PolicyDecision.ALLOW)


class DecideAutomatedTests(unittest.TestCase):
    """Percorso automatico (PlanExecutor): nessun gradino REQUIRE_AUTH, e chi chiama deve gia'
    aver ripulito i parametri - questo metodo non li guarda affatto."""

    def test_blocked_intent_is_blocked(self):
        engine = PolicyEngine(blocked_intents={"SOME_INTENT"})

        self.assertEqual(engine.decide_automated("SOME_INTENT"), PolicyDecision.BLOCK)

    def test_always_confirm_intent_is_confirm_even_with_no_parameters_examined(self):
        """A differenza del percorso interattivo, qui non esiste un modo per 'gia' confermato':
        un intent ADMIN/DESTRUCTIVE si ferma sempre, un piano automatico non ha nessuno pronto a
        rispondere in tempo reale."""
        engine = PolicyEngine(always_confirm_intents={"DELETE_TODO"})

        self.assertEqual(engine.decide_automated("DELETE_TODO"), PolicyDecision.CONFIRM)

    def test_plain_intent_is_allowed(self):
        engine = PolicyEngine()

        self.assertEqual(engine.decide_automated("GET_TIME"), PolicyDecision.ALLOW)


class ExplainTests(unittest.TestCase):
    """F1.2.7 ("policy simulator: mostra se e perche' un'azione sarebbe permessa"). explain()
    non deve avere una logica propria: ogni caso qui ha un test gemello in
    DecideInteractiveTests/DecideAutomatedTests che verifica lo stesso scenario tramite
    decide_interactive()/decide_automated(), cosi' un domani in cui le due implementazioni
    divergessero (esattamente il pattern di bug che il modulo documenta per RunWorkflowSkill)
    verrebbe scoperto qui."""

    def test_blocked_intent_reports_block_on_both_paths_with_a_reason(self):
        engine = PolicyEngine(blocked_intents={"SOME_INTENT"})

        explanation = engine.explain("SOME_INTENT")

        self.assertEqual(explanation["intent"], "SOME_INTENT")
        self.assertEqual(explanation["interactive"], {"decision": "block", "reason": "intent_in_blocked_intents"})
        self.assertEqual(explanation["automated"], {"decision": "block", "reason": "intent_in_blocked_intents"})

    def test_plain_intent_is_allowed_on_both_paths(self):
        engine = PolicyEngine()

        explanation = engine.explain("GET_TIME")

        self.assertEqual(explanation["interactive"]["decision"], "allow")
        self.assertEqual(explanation["automated"]["decision"], "allow")

    def test_always_confirm_intent_without_confirmed_needs_confirm_on_both_paths(self):
        engine = PolicyEngine(always_confirm_intents={"DELETE_TODO"})

        explanation = engine.explain("DELETE_TODO")

        self.assertEqual(explanation["interactive"], {"decision": "confirm", "reason": "intent_in_always_confirm_intents"})
        self.assertEqual(explanation["automated"], {"decision": "confirm", "reason": "intent_in_always_confirm_intents"})

    def test_always_confirm_intent_with_confirmed_parameter_only_allows_the_interactive_path(self):
        """'confirmed' conta per il verdetto interattivo ma MAI per quello automatico - stesso
        motivo di decide_automated(): nessun segnale di autorizzazione e' genuino in un piano
        automatico (vedi strip_authorization_signals nel docstring del modulo)."""
        engine = PolicyEngine(always_confirm_intents={"DELETE_TODO"})

        explanation = engine.explain("DELETE_TODO", {"confirmed": True})

        self.assertEqual(explanation["interactive"]["decision"], "allow")
        self.assertEqual(explanation["automated"]["decision"], "confirm")

    def test_require_auth_only_applies_to_the_interactive_path(self):
        """REQUIRE_AUTH esiste solo per il percorso interattivo (un piano automatico non ha
        nessuno pronto a fornire una passphrase in tempo reale) - vedi il docstring di
        decide_automated(). Un intent in require_auth_intents finisce comunque anche in
        always_confirm_intents nell'uso reale (core/risk.py::needs_central_confirmation include
        ADMIN), quindi il percorso automatico si ferma comunque, ma con CONFIRM, non REQUIRE_AUTH."""
        auth_gate = AuthGate(passphrase="segreta")
        engine = PolicyEngine(auth_gate=auth_gate, require_auth_intents={"SYSTEM_POWER"})

        explanation = engine.explain("SYSTEM_POWER")

        self.assertEqual(explanation["interactive"]["decision"], "require_auth")
        self.assertEqual(explanation["automated"]["decision"], "allow")


class DecideWithReasonTests(unittest.TestCase):
    """F1.2.6 ("salvare la motivazione della decisione nel ledger"): decide_interactive_with_
    reason()/decide_automated_with_reason() sono le varianti PUBBLICHE usate da PlanExecutor per
    salvare la motivazione senza ricalcolare anche il verdetto dell'altro percorso (a differenza
    di explain(), pensato per un simulatore, non per il logging in produzione) - stessa logica
    di decide_interactive()/decide_automated(), solo con la motivazione in piu'."""

    def test_decide_automated_with_reason_matches_decide_automated_plus_the_right_reason(self):
        engine = PolicyEngine(blocked_intents={"X"})

        decision, reason = engine.decide_automated_with_reason("X")

        self.assertEqual(decision, engine.decide_automated("X"))
        self.assertEqual(reason, "intent_in_blocked_intents")

    def test_decide_interactive_with_reason_matches_decide_interactive_plus_the_right_reason(self):
        engine = PolicyEngine(always_confirm_intents={"DELETE_TODO"})

        decision, reason = engine.decide_interactive_with_reason("DELETE_TODO", {})

        self.assertEqual(decision, engine.decide_interactive("DELETE_TODO", {}))
        self.assertEqual(reason, "intent_in_always_confirm_intents")

    def test_every_reason_constant_is_one_of_the_closed_values(self):
        """POLICY_REASONS e' un vocabolario chiuso (F1.2.6): nessuna motivazione reale puo'
        uscirne, ne' per il percorso interattivo ne' per quello automatico."""
        from core.policy_engine import POLICY_REASONS

        scenarios = [
            PolicyEngine(blocked_intents={"X"}),
            PolicyEngine(always_confirm_intents={"X"}),
            PolicyEngine(auth_gate=AuthGate(passphrase="s"), require_auth_intents={"X"}),
            PolicyEngine(),
        ]
        for engine in scenarios:
            with self.subTest(engine=engine.__dict__):
                _, interactive_reason = engine.decide_interactive_with_reason("X", {})
                _, automated_reason = engine.decide_automated_with_reason("X")
                self.assertIn(interactive_reason, POLICY_REASONS)
                self.assertIn(automated_reason, POLICY_REASONS)


class FilesystemCapabilityTests(unittest.TestCase):
    """F1.2.2 (primo pezzo di capability: radici filesystem consentite). Solo il percorso
    interattivo e solo i quattro intent di mutazione (CREATE_PATH/RENAME_PATH/MOVE_PATH/
    DELETE_PATH) rispettano allowed_filesystem_roots oggi - vedi il docstring del modulo per il
    perche' decide_automated ne resta fuori."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.allowed_root = Path(self._tmpdir.name) / "allowed"
        self.allowed_root.mkdir()
        self.outside_root = Path(self._tmpdir.name) / "outside"
        self.outside_root.mkdir()

    def test_no_configured_roots_means_no_restriction_at_all(self):
        """Comportamento invariato per chi non configura nulla (default vuoto)."""
        engine = PolicyEngine()
        decision = engine.decide_interactive("DELETE_PATH", {"path": str(self.outside_root / "x.txt")})
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_a_path_inside_an_allowed_root_is_permitted(self):
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        decision = engine.decide_interactive("DELETE_PATH", {"path": str(self.allowed_root / "x.txt")})
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_the_root_itself_is_permitted_not_only_its_descendants(self):
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        decision = engine.decide_interactive("DELETE_PATH", {"path": str(self.allowed_root)})
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_a_path_outside_every_allowed_root_is_blocked(self):
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        decision, reason = engine.decide_interactive_with_reason(
            "DELETE_PATH", {"path": str(self.outside_root / "x.txt")},
        )
        self.assertEqual(decision, PolicyDecision.BLOCK)
        self.assertEqual(reason, "path_outside_allowed_filesystem_roots")

    def test_a_sibling_directory_with_a_similar_prefix_is_not_confused_for_a_descendant(self):
        """"C:\\Allowed" non deve corrispondere per errore a "C:\\AllowedButNot" solo perche'
        condividono un prefisso di stringa - deve esserci un confine di directory vero."""
        similarly_prefixed = Path(str(self.allowed_root) + "ButNot")
        similarly_prefixed.mkdir()
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        decision = engine.decide_interactive("DELETE_PATH", {"path": str(similarly_prefixed / "x.txt")})
        self.assertEqual(decision, PolicyDecision.BLOCK)

    def test_move_path_destination_outside_an_allowed_root_is_blocked_even_if_the_source_is_inside(self):
        """Altrimenti MOVE_PATH sarebbe un modo per far uscire un file dal recinto consentito
        partendo da un percorso permesso."""
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        decision = engine.decide_interactive(
            "MOVE_PATH",
            {"path": str(self.allowed_root / "x.txt"), "destination": str(self.outside_root)},
        )
        self.assertEqual(decision, PolicyDecision.BLOCK)

    def test_move_path_with_both_source_and_destination_inside_is_permitted(self):
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        subdir = self.allowed_root / "sub"
        subdir.mkdir()
        decision = engine.decide_interactive(
            "MOVE_PATH", {"path": str(self.allowed_root / "x.txt"), "destination": str(subdir)},
        )
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_a_dot_dot_traversal_attempt_out_of_an_allowed_root_is_blocked(self):
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        traversal = str(self.allowed_root / ".." / "outside" / "x.txt")
        decision = engine.decide_interactive("DELETE_PATH", {"path": traversal})
        self.assertEqual(decision, PolicyDecision.BLOCK)

    def test_read_only_path_intents_are_now_covered_too(self):
        """F1.2.2 (terza fetta): FIND_FILE/GET_FILE_INFO/READ_FILE_TEXT (RiskLevel.READ_ONLY)
        rispettano la capability quanto le quattro mutazioni - altrimenti la capability
        lascerebbe comunque Jake libero di LEGGERE qualunque file fuori dal recinto configurato."""
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        for intent in ("FIND_FILE", "GET_FILE_INFO", "READ_FILE_TEXT"):
            with self.subTest(intent=intent):
                decision = engine.decide_interactive(intent, {"path": str(self.outside_root / "x.txt")})
                self.assertEqual(decision, PolicyDecision.BLOCK)

    def test_read_only_path_intents_inside_an_allowed_root_are_still_permitted(self):
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        for intent in ("FIND_FILE", "GET_FILE_INFO", "READ_FILE_TEXT"):
            with self.subTest(intent=intent):
                decision = engine.decide_interactive(intent, {"path": str(self.allowed_root / "x.txt")})
                self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_open_path_is_deliberately_not_covered(self):
        """OPEN_PATH e' RiskLevel.LOCAL_REVERSIBLE (apre un file con l'applicazione predefinita),
        non READ_ONLY - un rischio diverso da una lettura pura, fuori scope per questa capability
        finche' non viene deciso diversamente."""
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        decision = engine.decide_interactive("OPEN_PATH", {"path": str(self.outside_root / "x.txt")})
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_find_file_without_an_explicit_path_is_not_restricted(self):
        """Gap noto e dichiarato apertamente: il parametro `path` di FIND_FILE e' opzionale (cerca
        nelle cartelle utente comuni se omesso) - senza un percorso da controllare, questo
        controllo non ha nulla su cui applicarsi."""
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        decision = engine.decide_interactive("FIND_FILE", {"name": "tesi.pdf"})
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_read_only_path_intents_are_covered_on_the_automated_path_too(self):
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        decision = engine.decide_automated("READ_FILE_TEXT", {"path": str(self.outside_root / "x.txt")})
        self.assertEqual(decision, PolicyDecision.BLOCK)

    def test_capability_denial_is_checked_before_confirmation_would_otherwise_apply(self):
        """Un DELETE_PATH gia' 'confirmed' non deve bypassare la capability - il controllo di
        percorso viene prima, non dopo, del gate di conferma."""
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        decision = engine.decide_interactive(
            "DELETE_PATH", {"path": str(self.outside_root / "x.txt"), "confirmed": True},
        )
        self.assertEqual(decision, PolicyDecision.BLOCK)

    def test_a_blocked_intent_still_wins_over_the_capability_check(self):
        engine = PolicyEngine(blocked_intents={"DELETE_PATH"}, allowed_filesystem_roots={str(self.allowed_root)})
        decision, reason = engine.decide_interactive_with_reason(
            "DELETE_PATH", {"path": str(self.allowed_root / "x.txt")},
        )
        self.assertEqual(decision, PolicyDecision.BLOCK)
        self.assertEqual(reason, "intent_in_blocked_intents")

    def test_decide_automated_without_parameters_applies_no_restriction(self):
        """Comportamento invariato per chi non passa `parameters` (es. un vecchio chiamante non
        ancora aggiornato): nessun percorso da controllare, nessuna restrizione applicabile."""
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        decision = engine.decide_automated("DELETE_PATH")
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_decide_automated_enforces_filesystem_roots_when_parameters_are_passed(self):
        """F1.2.2 (seconda fetta): il buco dichiarato apertamente nel modulo - un'automazione
        poteva mutare un percorso fuori dalle radici consentite perche' decide_automated() non
        riceveva affatto `parameters` - e' chiuso: PlanExecutor.execute() (l'unico chiamante di
        produzione) ora passa i parametri del passo."""
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        decision, reason = engine.decide_automated_with_reason(
            "DELETE_PATH", {"path": str(self.outside_root / "x.txt")},
        )
        self.assertEqual(decision, PolicyDecision.BLOCK)
        self.assertEqual(reason, "path_outside_allowed_filesystem_roots")

    def test_decide_automated_permits_a_path_inside_an_allowed_root(self):
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        decision = engine.decide_automated("DELETE_PATH", {"path": str(self.allowed_root / "x.txt")})
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_decide_automated_capability_denial_wins_over_confirm(self):
        """Un'automazione con un DELETE_PATH sia fuori dalle radici consentite SIA gia' in
        always_confirm_intents deve fermarsi per la capability (BLOCK), non arrivare a CONFIRM -
        qui nessuno e' comunque pronto a rispondere, ma il motivo salvato nel ledger deve essere
        quello vero (capability), non un CONFIRM fuorviante che implica "basterebbe confermare"."""
        engine = PolicyEngine(
            always_confirm_intents={"DELETE_PATH"}, allowed_filesystem_roots={str(self.allowed_root)},
        )
        decision, reason = engine.decide_automated_with_reason(
            "DELETE_PATH", {"path": str(self.outside_root / "x.txt")},
        )
        self.assertEqual(decision, PolicyDecision.BLOCK)
        self.assertEqual(reason, "path_outside_allowed_filesystem_roots")

    def test_explain_reports_the_capability_denial_for_the_interactive_verdict(self):
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        result = engine.explain("DELETE_PATH", {"path": str(self.outside_root / "x.txt")})
        self.assertEqual(result["interactive"]["decision"], "block")
        self.assertEqual(result["interactive"]["reason"], "path_outside_allowed_filesystem_roots")

    def test_explain_reports_the_capability_denial_for_the_automated_verdict_too(self):
        """F1.2.2 (seconda fetta): prima di questo fix, explain()["automated"] avrebbe sempre
        detto ALLOW qui, nascondendo che l'automazione si sarebbe davvero bloccata."""
        engine = PolicyEngine(allowed_filesystem_roots={str(self.allowed_root)})
        result = engine.explain("DELETE_PATH", {"path": str(self.outside_root / "x.txt")})
        self.assertEqual(result["automated"]["decision"], "block")
        self.assertEqual(result["automated"]["reason"], "path_outside_allowed_filesystem_roots")


class DeviceCapabilityTests(unittest.TestCase):
    """F1.2.3 (intersezione, primo pezzo - capability per DISPOSITIVO): "vince il piu'
    restrittivo" - un intent bloccato per UN dispositivo si ferma sempre per quel dispositivo,
    indipendentemente da cosa permetterebbe la policy a livello utente, senza toccare cio' che
    puo' fare la voce locale o un altro dispositivo."""

    def setUp(self):
        self._tokens = []
        self.addCleanup(self._reset_all)

    def _reset_all(self):
        for token in reversed(self._tokens):
            reset_current_device_id(token)

    def _as_device(self, device_id):
        self._tokens.append(set_current_device_id(device_id))

    def test_no_device_blocked_intents_configured_means_no_restriction(self):
        """Comportamento invariato per chi non configura nulla (default vuoto)."""
        engine = PolicyEngine()
        self._as_device("phone-ospite")
        self.assertEqual(engine.decide_interactive("DELETE_PATH", {}), PolicyDecision.ALLOW)

    def test_an_intent_blocked_for_a_device_is_blocked_only_for_that_device(self):
        engine = PolicyEngine(device_blocked_intents={"phone-ospite": {"DELETE_PATH"}})

        self._as_device("phone-ospite")
        self.assertEqual(engine.decide_interactive("DELETE_PATH", {}), PolicyDecision.BLOCK)

    def test_the_same_intent_is_still_allowed_for_a_different_device(self):
        engine = PolicyEngine(device_blocked_intents={"phone-ospite": {"DELETE_PATH"}})

        self._as_device("tablet-fiducia")
        self.assertEqual(engine.decide_interactive("DELETE_PATH", {}), PolicyDecision.ALLOW)

    def test_the_same_intent_is_still_allowed_from_the_local_voice_channel(self):
        engine = PolicyEngine(device_blocked_intents={"phone-ospite": {"DELETE_PATH"}})
        # Nessun set_current_device_id chiamato: canale locale (None), un caso a se'.
        self.assertEqual(engine.decide_interactive("DELETE_PATH", {}), PolicyDecision.ALLOW)

    def test_device_block_reports_a_distinct_reason_from_the_global_block(self):
        engine = PolicyEngine(device_blocked_intents={"phone-ospite": {"DELETE_PATH"}})
        self._as_device("phone-ospite")

        decision, reason = engine.decide_interactive_with_reason("DELETE_PATH", {})

        self.assertEqual(decision, PolicyDecision.BLOCK)
        self.assertEqual(reason, "intent_in_device_blocked_intents")

    def test_device_block_wins_over_confirmation(self):
        """Un intent bloccato per il dispositivo non deve mai arrivare a CONFIRM - "vince il piu'
        restrittivo" significa fermarsi qui, non chiedere conferma su qualcosa gia' vietato."""
        engine = PolicyEngine(
            always_confirm_intents={"DELETE_PATH"}, device_blocked_intents={"phone-ospite": {"DELETE_PATH"}},
        )
        self._as_device("phone-ospite")

        decision = engine.decide_interactive("DELETE_PATH", {"confirmed": True})

        self.assertEqual(decision, PolicyDecision.BLOCK)

    def test_a_global_block_still_applies_regardless_of_device_capability(self):
        """L'intersezione vale nei due sensi: un dispositivo non specificamente ristretto resta
        comunque soggetto a blocked_intents (livello utente)."""
        engine = PolicyEngine(blocked_intents={"DELETE_PATH"}, device_blocked_intents={"phone-ospite": {"RENAME_PATH"}})
        self._as_device("phone-ospite")

        decision, reason = engine.decide_interactive_with_reason("DELETE_PATH", {})

        self.assertEqual(decision, PolicyDecision.BLOCK)
        self.assertEqual(reason, "intent_in_blocked_intents")

    def test_device_capability_applies_on_the_automated_path_too(self):
        engine = PolicyEngine(device_blocked_intents={"phone-ospite": {"DELETE_PATH"}})
        self._as_device("phone-ospite")

        decision = engine.decide_automated("DELETE_PATH")

        self.assertEqual(decision, PolicyDecision.BLOCK)

    def test_explain_reports_the_device_block_on_both_verdicts(self):
        engine = PolicyEngine(device_blocked_intents={"phone-ospite": {"DELETE_PATH"}})
        self._as_device("phone-ospite")

        result = engine.explain("DELETE_PATH")

        self.assertEqual(result["interactive"]["decision"], "block")
        self.assertEqual(result["interactive"]["reason"], "intent_in_device_blocked_intents")
        self.assertEqual(result["automated"]["decision"], "block")
        self.assertEqual(result["automated"]["reason"], "intent_in_device_blocked_intents")


class WebDomainCapabilityTests(unittest.TestCase):
    """F1.2.2 (seconda capability: dominio web). Solo OPEN_URL rispetta allowed_web_domains oggi -
    vedi il docstring del modulo per il perche' CHECK_WEBSITE_STATUS ne resta fuori."""

    def test_no_configured_domains_means_no_restriction_at_all(self):
        """Comportamento invariato per chi non configura nulla (default vuoto)."""
        engine = PolicyEngine()
        decision = engine.decide_interactive("OPEN_URL", {"url": "https://esempio-qualsiasi.com"})
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_a_url_on_an_allowed_domain_is_permitted(self):
        engine = PolicyEngine(allowed_web_domains={"wikipedia.org"})
        decision = engine.decide_interactive("OPEN_URL", {"url": "https://wikipedia.org/wiki/Python"})
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_a_subdomain_of_an_allowed_domain_is_permitted(self):
        engine = PolicyEngine(allowed_web_domains={"wikipedia.org"})
        decision = engine.decide_interactive("OPEN_URL", {"url": "https://it.wikipedia.org/wiki/Python"})
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_a_url_outside_every_allowed_domain_is_blocked(self):
        engine = PolicyEngine(allowed_web_domains={"wikipedia.org"})
        decision, reason = engine.decide_interactive_with_reason("OPEN_URL", {"url": "https://esempio-vietato.com"})
        self.assertEqual(decision, PolicyDecision.BLOCK)
        self.assertEqual(reason, "domain_outside_allowed_web_domains")

    def test_a_url_without_a_scheme_is_still_checked(self):
        """OpenUrlSkill aggiunge https:// da sola se l'utente non lo dice - lo stesso trattamento
        vale qui, altrimenti "esempio-vietato.com" (senza schema) aggirerebbe il controllo."""
        engine = PolicyEngine(allowed_web_domains={"wikipedia.org"})
        decision = engine.decide_interactive("OPEN_URL", {"url": "esempio-vietato.com"})
        self.assertEqual(decision, PolicyDecision.BLOCK)

    def test_a_similarly_prefixed_domain_is_not_confused_for_a_subdomain(self):
        """"wikipedia.org" non deve corrispondere per errore a "not-wikipedia.org" solo perche'
        condividono un suffisso di stringa - deve esserci un confine di dominio vero."""
        engine = PolicyEngine(allowed_web_domains={"wikipedia.org"})
        decision = engine.decide_interactive("OPEN_URL", {"url": "https://not-wikipedia.org"})
        self.assertEqual(decision, PolicyDecision.BLOCK)

    def test_check_website_status_is_deliberately_not_covered(self):
        """CHECK_WEBSITE_STATUS e' RiskLevel.READ_ONLY (verifica solo se un sito risponde), non
        apre nulla - un rischio diverso da OPEN_URL, fuori scope per questa capability."""
        engine = PolicyEngine(allowed_web_domains={"wikipedia.org"})
        decision = engine.decide_interactive("CHECK_WEBSITE_STATUS", {"url": "https://esempio-vietato.com"})
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_capability_denial_is_checked_before_confirmation_would_otherwise_apply(self):
        engine = PolicyEngine(allowed_web_domains={"wikipedia.org"}, always_confirm_intents={"OPEN_URL"})
        decision = engine.decide_interactive(
            "OPEN_URL", {"url": "https://esempio-vietato.com", "confirmed": True},
        )
        self.assertEqual(decision, PolicyDecision.BLOCK)

    def test_a_blocked_intent_still_wins_over_the_capability_check(self):
        engine = PolicyEngine(blocked_intents={"OPEN_URL"}, allowed_web_domains={"wikipedia.org"})
        decision, reason = engine.decide_interactive_with_reason("OPEN_URL", {"url": "https://wikipedia.org"})
        self.assertEqual(decision, PolicyDecision.BLOCK)
        self.assertEqual(reason, "intent_in_blocked_intents")

    def test_decide_automated_enforces_web_domains_when_parameters_are_passed(self):
        engine = PolicyEngine(allowed_web_domains={"wikipedia.org"})
        decision, reason = engine.decide_automated_with_reason("OPEN_URL", {"url": "https://esempio-vietato.com"})
        self.assertEqual(decision, PolicyDecision.BLOCK)
        self.assertEqual(reason, "domain_outside_allowed_web_domains")

    def test_explain_reports_the_web_capability_denial_on_both_verdicts(self):
        engine = PolicyEngine(allowed_web_domains={"wikipedia.org"})
        result = engine.explain("OPEN_URL", {"url": "https://esempio-vietato.com"})
        self.assertEqual(result["interactive"]["decision"], "block")
        self.assertEqual(result["interactive"]["reason"], "domain_outside_allowed_web_domains")
        self.assertEqual(result["automated"]["decision"], "block")
        self.assertEqual(result["automated"]["reason"], "domain_outside_allowed_web_domains")


if __name__ == "__main__":
    unittest.main()
