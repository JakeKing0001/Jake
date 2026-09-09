"""Test unitari per core/policy_engine.py (F1, Trustworthy Agent Core 3.0 - "separazione formale
planner/policy engine/executor" in ROADMAP.md, fase F1).

Prima di questo modulo la stessa decisione era scritta a mano in due posti - JakeCore.
_resolve_and_execute (percorso interattivo) e PlanExecutor.execute (percorso automatico) - e due
bug reali sono nati esattamente da quel disallineamento (vedi ROADMAP.md F1: blocked_intents mai
controllato nel percorso interattivo, quindi mai per l'agente a passi; confirmed/authenticated
mai ripuliti nel percorso automatico, quindi un piano poteva auto-autorizzarsi). Questi test
coprono la logica in isolamento; tests/test_jake_core_permissions.py e tests/test_plan_executor.py
coprono che i due esecutori la usino davvero."""
import unittest

from core.auth_gate import AuthGate
from core.policy_engine import (
    PolicyDecision, decide_automated, decide_interactive, register_intent, strip_authorization_signals,
)


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


class RegisterIntentTests(unittest.TestCase):
    def test_admin_intent_is_added_to_both_sets(self):
        always_confirm, require_auth = set(), set()
        register_intent("SOME_NEW_ADMIN_INTENT", always_confirm_intents=always_confirm, require_auth_intents=require_auth)
        self.assertIn("SOME_NEW_ADMIN_INTENT", always_confirm)
        self.assertIn("SOME_NEW_ADMIN_INTENT", require_auth)

    def test_read_only_intent_is_added_to_neither(self):
        always_confirm, require_auth = set(), set()
        register_intent("GET_TIME", always_confirm_intents=always_confirm, require_auth_intents=require_auth)
        self.assertNotIn("GET_TIME", always_confirm)
        self.assertNotIn("GET_TIME", require_auth)

    def test_destructive_intent_is_added_to_confirm_but_not_auth(self):
        always_confirm, require_auth = set(), set()
        register_intent("DELETE_TODO", always_confirm_intents=always_confirm, require_auth_intents=require_auth)
        self.assertIn("DELETE_TODO", always_confirm)
        self.assertNotIn("DELETE_TODO", require_auth)

    def test_self_confirming_intent_is_added_to_neither(self):
        """DELETE_PATH e' DESTRUCTIVE ma si autoconferma (core/risk.py, SELF_CONFIRMING_INTENTS):
        il gate centrale non deve intervenire, altrimenti l'utente vedrebbe un 'confermi?' spoglio
        prima che la skill controlli se l'azione e' persino possibile."""
        always_confirm, require_auth = set(), set()
        register_intent("DELETE_PATH", always_confirm_intents=always_confirm, require_auth_intents=require_auth)
        self.assertNotIn("DELETE_PATH", always_confirm)
        self.assertNotIn("DELETE_PATH", require_auth)


class DecideInteractiveTests(unittest.TestCase):
    def test_blocked_intent_is_blocked_regardless_of_everything_else(self):
        decision = decide_interactive(
            "SOME_INTENT", {"confirmed": True, "authenticated": True},
            blocked_intents={"SOME_INTENT"}, always_confirm_intents=set(), require_auth_intents=set(),
            auth_gate=AuthGate(passphrase="x"),
        )
        self.assertEqual(decision, PolicyDecision.BLOCK)

    def test_plain_intent_is_allowed(self):
        decision = decide_interactive(
            "GET_TIME", {}, blocked_intents=set(), always_confirm_intents=set(),
            require_auth_intents=set(), auth_gate=AuthGate(),
        )
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_always_confirm_intent_without_confirmed_needs_confirm(self):
        decision = decide_interactive(
            "DELETE_TODO", {}, blocked_intents=set(), always_confirm_intents={"DELETE_TODO"},
            require_auth_intents=set(), auth_gate=AuthGate(),
        )
        self.assertEqual(decision, PolicyDecision.CONFIRM)

    def test_always_confirm_intent_with_confirmed_is_allowed(self):
        decision = decide_interactive(
            "DELETE_TODO", {"confirmed": True}, blocked_intents=set(), always_confirm_intents={"DELETE_TODO"},
            require_auth_intents=set(), auth_gate=AuthGate(),
        )
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_require_auth_intent_needs_auth_when_gate_enabled(self):
        decision = decide_interactive(
            "SET_POWER_PLAN", {}, blocked_intents=set(), always_confirm_intents=set(),
            require_auth_intents={"SET_POWER_PLAN"}, auth_gate=AuthGate(passphrase="apri sesamo"),
        )
        self.assertEqual(decision, PolicyDecision.REQUIRE_AUTH)

    def test_require_auth_intent_falls_back_to_confirm_when_gate_disabled(self):
        """Se l'utente non ha mai attivato passphrase/Windows Hello (AuthGate.enabled=False),
        un intent ADMIN resta comunque protetto dalla conferma si'/no ordinaria - nessun vicolo
        cieco senza via d'uscita."""
        decision = decide_interactive(
            "SET_POWER_PLAN", {}, blocked_intents=set(), always_confirm_intents={"SET_POWER_PLAN"},
            require_auth_intents={"SET_POWER_PLAN"}, auth_gate=AuthGate(),
        )
        self.assertEqual(decision, PolicyDecision.CONFIRM)

    def test_authenticated_parameter_bypasses_require_auth(self):
        decision = decide_interactive(
            "SET_POWER_PLAN", {"authenticated": True}, blocked_intents=set(), always_confirm_intents=set(),
            require_auth_intents={"SET_POWER_PLAN"}, auth_gate=AuthGate(passphrase="apri sesamo"),
        )
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_none_auth_gate_is_treated_as_disabled(self):
        decision = decide_interactive(
            "SET_POWER_PLAN", {}, blocked_intents=set(), always_confirm_intents=set(),
            require_auth_intents={"SET_POWER_PLAN"}, auth_gate=None,
        )
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_none_sets_do_not_crash(self):
        decision = decide_interactive("GET_TIME", {}, auth_gate=AuthGate())
        self.assertEqual(decision, PolicyDecision.ALLOW)


class DecideAutomatedTests(unittest.TestCase):
    """Percorso automatico (PlanExecutor): nessun gradino REQUIRE_AUTH, e chi chiama deve gia'
    aver ripulito i parametri - questa funzione non li guarda affatto."""

    def test_blocked_intent_is_blocked(self):
        decision = decide_automated("SOME_INTENT", blocked_intents={"SOME_INTENT"}, always_confirm_intents=set())
        self.assertEqual(decision, PolicyDecision.BLOCK)

    def test_always_confirm_intent_is_confirm_even_with_no_parameters_examined(self):
        """A differenza del percorso interattivo, qui non esiste un modo per 'gia' confermato':
        un intent ADMIN/DESTRUCTIVE si ferma sempre, un piano automatico non ha nessuno pronto a
        rispondere in tempo reale."""
        decision = decide_automated("DELETE_TODO", blocked_intents=set(), always_confirm_intents={"DELETE_TODO"})
        self.assertEqual(decision, PolicyDecision.CONFIRM)

    def test_plain_intent_is_allowed(self):
        decision = decide_automated("GET_TIME", blocked_intents=set(), always_confirm_intents=set())
        self.assertEqual(decision, PolicyDecision.ALLOW)

    def test_none_sets_do_not_crash(self):
        self.assertEqual(decide_automated("GET_TIME"), PolicyDecision.ALLOW)


if __name__ == "__main__":
    unittest.main()
