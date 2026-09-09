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
import unittest

from core.auth_gate import AuthGate
from core.policy_engine import PolicyDecision, PolicyEngine, strip_authorization_signals


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


if __name__ == "__main__":
    unittest.main()
