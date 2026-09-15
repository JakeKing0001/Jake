"""Test unitari per core/action_contracts.py (F1.1.2, Action Contract 2.0): i cinque contratti
condivisi elencati dalla roadmap - ActionProposal, ActionContext, VerificationEvidence,
UndoDescriptor, ActionError. Nessuna suite esisteva ancora per questo modulo, appena introdotto.

Ogni test di "riuso" verifica che il tipo qui non duplichi una tassonomia/costante gia' esistente
altrove (core/action_ledger.py, core/execution_safety.py, core/risk.py) con un secondo elenco che
potrebbe divergere in silenzio - lo stesso principio gia' applicato altrove in questo progetto."""
import unittest

from core.action_contracts import (
    EFFECT_CLASS_CREATE,
    EFFECT_CLASSES,
    INTENT_EFFECT_CLASS,
    ActionContext,
    ActionError,
    ActionProposal,
    UndoDescriptor,
    VerificationEvidence,
    effect_class_of,
    validate_action_context,
    validate_action_error,
    validate_action_proposal,
    validate_undo_descriptor,
    validate_verification_evidence,
)
from core.action_ledger import (
    ERROR_CATEGORY_TRANSIENT, ERROR_CATEGORY_UNCATEGORIZED, VERIFICATION_FAILED,
    VERIFICATION_UNVERIFIED, VERIFICATION_VERIFIED,
)
from core.execution_safety import RETRYABLE_ERRORS
from core.risk import SKILL_RISK, RiskLevel


class ActionProposalTests(unittest.TestCase):
    def test_for_intent_derives_risk_from_risk_of(self):
        """Non deve essere possibile dichiarare un rischio diverso da quello che il resto del
        sistema (SkillRegistry.risk_of/PolicyEngine.register_intent) assegnerebbe allo stesso
        intent - vedi il docstring di for_intent()."""
        proposal = ActionProposal.for_intent("DELETE_PATH", {"path": "x"}, "user")

        self.assertEqual(proposal.risk, RiskLevel.DESTRUCTIVE.value)

    def test_parameters_are_copied_not_aliased(self):
        original = {"path": "x"}
        proposal = ActionProposal.for_intent("CREATE_PATH", original, "user")
        proposal.parameters["path"] = "y"

        self.assertEqual(original["path"], "x")

    def test_validate_accepts_a_well_formed_proposal(self):
        proposal = ActionProposal.for_intent("GET_TIME", {}, "user")
        validate_action_proposal(proposal)  # non deve sollevare

    def test_validate_rejects_missing_intent(self):
        proposal = ActionProposal(intent="", parameters={}, requested_by="user", risk="read_only")
        with self.assertRaises(ValueError):
            validate_action_proposal(proposal)

    def test_validate_rejects_an_unknown_risk_level(self):
        proposal = ActionProposal(intent="X", parameters={}, requested_by="user", risk="not_a_real_level")
        with self.assertRaises(ValueError):
            validate_action_proposal(proposal)

    def test_validate_rejects_an_unknown_effect_class(self):
        proposal = ActionProposal.for_intent("CREATE_PATH", {}, "user", effect_class="not_a_real_class")
        with self.assertRaises(ValueError):
            validate_action_proposal(proposal)

    def test_effect_class_is_optional_and_absent_by_default(self):
        """Non derivabile in modo affidabile per le skill non censite (vedi il commento su
        EFFECT_CLASSES): deve restare None, non un valore indovinato."""
        proposal = ActionProposal.for_intent("SOME_UNCENSORED_INTENT", {}, "user")
        self.assertIsNone(proposal.effect_class)

    def test_known_effect_class_is_accepted(self):
        proposal = ActionProposal.for_intent("CREATE_PATH", {}, "user", effect_class=EFFECT_CLASS_CREATE)
        validate_action_proposal(proposal)  # non deve sollevare
        self.assertIn(proposal.effect_class, EFFECT_CLASSES)

    def test_for_intent_auto_derives_effect_class_from_the_census(self):
        """F1.1.2 (censimento completo, 15/09/2026): quando il chiamante non passa effect_class,
        for_intent() lo ricava da effect_class_of() invece di lasciarlo sempre None - CREATE_PATH
        e' censito come 'create' leggendo skills/create_path.py."""
        proposal = ActionProposal.for_intent("CREATE_PATH", {"path": "x"}, "user")
        self.assertEqual(proposal.effect_class, EFFECT_CLASS_CREATE)

    def test_an_explicit_effect_class_is_never_overridden_by_the_census(self):
        """Anche se DELETE_PATH e' censito come 'delete', un chiamante che passa esplicitamente
        un altro valore valido resta padrone della propria scelta - for_intent() non la sovrascrive
        mai, stesso principio gia' vero per il caso pre-censimento."""
        proposal = ActionProposal.for_intent("DELETE_PATH", {}, "user", effect_class=EFFECT_CLASS_CREATE)
        self.assertEqual(proposal.effect_class, EFFECT_CLASS_CREATE)


class IntentEffectClassCensusTests(unittest.TestCase):
    """F1.1.2 (censimento completo, 15/09/2026): INTENT_EFFECT_CLASS copre 208 dei 209 intent di
    core/risk.py::SKILL_RISK, letti dal comportamento REALE di ogni skill (non ipotizzato dal
    nome). Manca deliberatamente solo RESUME_INTERRUPTED_TASK - vedi il commento sopra
    INTENT_EFFECT_CLASS per il perche'. Questi test proteggono l'INTEGRITA' del censimento
    (nessun intent fantasma, nessun valore fuori dalle cinque classi valide), non la correttezza
    di ogni singola classificazione (verificata a campione leggendo il codice durante la
    revisione, non riverificabile automaticamente qui senza duplicare quella lettura)."""

    def test_every_census_key_is_a_real_registered_intent(self):
        """Nessuna voce del censimento deve riferirsi a un intent che non esiste davvero in
        SKILL_RISK - un errore di battitura qui produrrebbe silenziosamente un intent fantasma
        mai raggiungibile da effect_class_of() per l'intent vero."""
        phantom_intents = set(INTENT_EFFECT_CLASS) - set(SKILL_RISK)
        self.assertEqual(phantom_intents, set())

    def test_every_registered_intent_is_censused_except_the_one_declared_gap(self):
        """RESUME_INTERRUPTED_TASK e' l'UNICA eccezione dichiarata (riprende un TaskAgent da un
        checkpoint: l'agente ripreso decide da solo il prossimo passo, nessun effetto dominante
        stabile legato all'intent stesso) - qualunque altro intent mancante sarebbe un buco nel
        censimento, non una scelta deliberata."""
        uncensused = set(SKILL_RISK) - set(INTENT_EFFECT_CLASS)
        self.assertEqual(uncensused, {"RESUME_INTERRUPTED_TASK"})

    def test_every_census_value_is_a_valid_effect_class(self):
        for intent, value in INTENT_EFFECT_CLASS.items():
            with self.subTest(intent=intent):
                self.assertIn(value, EFFECT_CLASSES)

    def test_effect_class_of_returns_none_for_an_uncensused_intent(self):
        """A differenza di risk_of() (ricade su ADMIN come scelta di sicurezza), qui non esiste
        un default 'piu' prudente' plausibile tra le cinque classi - vedi il commento sopra
        effect_class_of()."""
        self.assertIsNone(effect_class_of("RESUME_INTERRUPTED_TASK"))
        self.assertIsNone(effect_class_of("UN_INTENT_MAI_ESISTITO"))

    def test_effect_class_of_matches_the_census_for_a_few_representative_intents(self):
        """Un campione, non un giro esaustivo di tutti i 208 (gia' coperto dagli altri test
        sopra) - dimostra che effect_class_of() legge davvero dal dizionario, non da una copia
        separata che potrebbe disallinearsi."""
        cases = {
            "GET_TIME": "read", "ADD_NOTE": "create", "RENAME_PATH": "modify",
            "DELETE_PATH": "delete", "RUN_COMMAND": "external",
        }
        for intent, expected in cases.items():
            with self.subTest(intent=intent):
                self.assertEqual(effect_class_of(intent), expected)


class ActionContextTests(unittest.TestCase):
    def test_ts_defaults_to_now_when_not_given(self):
        context = ActionContext(trace_id="t1", requested_by="user")
        self.assertGreater(context.ts, 0)

    def test_explicit_ts_is_preserved(self):
        context = ActionContext(trace_id="t1", requested_by="user", ts=123.0)
        self.assertEqual(context.ts, 123.0)

    def test_validate_rejects_missing_trace_id(self):
        context = ActionContext(trace_id="", requested_by="user")
        with self.assertRaises(ValueError):
            validate_action_context(context)

    def test_validate_accepts_a_well_formed_context(self):
        context = ActionContext(trace_id="t1", requested_by="agent:general", private=True, model="qwen2.5:7b")
        validate_action_context(context)  # non deve sollevare


class VerificationEvidenceTests(unittest.TestCase):
    """from_verified() deve usare la STESSA mappatura di
    core.action_ledger.verification_status_of() - vedi i tre test di riuso sotto."""

    def test_none_means_unverified(self):
        evidence = VerificationEvidence.from_verified("ADD_NOTE", None)
        self.assertEqual(evidence.status, VERIFICATION_UNVERIFIED)

    def test_true_means_verified(self):
        evidence = VerificationEvidence.from_verified("CREATE_PATH", True)
        self.assertEqual(evidence.status, VERIFICATION_VERIFIED)

    def test_false_means_verification_failed(self):
        evidence = VerificationEvidence.from_verified("CREATE_PATH", False)
        self.assertEqual(evidence.status, VERIFICATION_FAILED)

    def test_checked_at_defaults_to_now(self):
        evidence = VerificationEvidence.from_verified("CREATE_PATH", True)
        self.assertGreater(evidence.checked_at, 0)

    def test_method_is_optional(self):
        evidence = VerificationEvidence.from_verified("ADD_NOTE", None)
        self.assertIsNone(evidence.method)

    def test_validate_rejects_an_unrecognized_status(self):
        evidence = VerificationEvidence(intent="X", status="not_a_real_status")
        with self.assertRaises(ValueError):
            validate_verification_evidence(evidence)


class UndoDescriptorTests(unittest.TestCase):
    def test_no_expiry_means_never_expired(self):
        descriptor = UndoDescriptor(action_id="a1", compensating_intent="DELETE_PATH", compensating_parameters={})
        self.assertFalse(descriptor.is_expired(now=1e12))
        self.assertTrue(descriptor.is_usable(now=1e12))

    def test_expires_at_in_the_past_is_expired(self):
        descriptor = UndoDescriptor(
            action_id="a1", compensating_intent="DELETE_PATH", compensating_parameters={}, expires_at=100.0,
        )
        self.assertTrue(descriptor.is_expired(now=200.0))
        self.assertFalse(descriptor.is_usable(now=200.0))

    def test_expires_at_in_the_future_is_not_expired(self):
        descriptor = UndoDescriptor(
            action_id="a1", compensating_intent="DELETE_PATH", compensating_parameters={}, expires_at=200.0,
        )
        self.assertFalse(descriptor.is_expired(now=100.0))
        self.assertTrue(descriptor.is_usable(now=100.0))

    def test_used_descriptor_is_not_usable_even_if_not_expired(self):
        descriptor = UndoDescriptor(
            action_id="a1", compensating_intent="DELETE_PATH", compensating_parameters={}, used=True,
        )
        self.assertFalse(descriptor.is_usable())

    def test_validate_rejects_missing_action_id(self):
        descriptor = UndoDescriptor(action_id="", compensating_intent="DELETE_PATH", compensating_parameters={})
        with self.assertRaises(ValueError):
            validate_undo_descriptor(descriptor)

    def test_validate_rejects_a_non_positive_expiry(self):
        descriptor = UndoDescriptor(
            action_id="a1", compensating_intent="DELETE_PATH", compensating_parameters={}, expires_at=0.0,
        )
        with self.assertRaises(ValueError):
            validate_undo_descriptor(descriptor)


class ActionErrorTests(unittest.TestCase):
    """from_result() riusa error_category_of()/normalize_result_code() (F1.1.4) - non una
    seconda normalizzazione parallela."""

    def test_category_matches_error_category_of(self):
        error = ActionError.from_result("error:OPERATION_FAILED")
        self.assertEqual(error.category, ERROR_CATEGORY_TRANSIENT)

    def test_code_is_normalized_without_the_error_prefix(self):
        error = ActionError.from_result("error:OPERATION_FAILED")
        self.assertEqual(error.code, "OPERATION_FAILED")

    def test_lowercase_no_prefix_result_is_also_normalized(self):
        """Stesso formato usato da TaskAgent per alcuni step (vedi core/action_ledger.py sui due
        formati in uso)."""
        error = ActionError.from_result("missing_parameters")
        self.assertEqual(error.code, "MISSING_PARAMETERS")

    def test_retryable_matches_the_shared_retryable_errors_set(self):
        for code in RETRYABLE_ERRORS:
            with self.subTest(code=code):
                error = ActionError.from_result(f"error:{code}")
                self.assertTrue(error.retryable)

    def test_non_retryable_code_is_marked_accordingly(self):
        error = ActionError.from_result("error:MISSING_PARAMETERS")
        self.assertFalse(error.retryable)

    def test_unmapped_skill_specific_code_falls_back_to_uncategorized(self):
        error = ActionError.from_result("error:UN_CODICE_MAI_VISTO_XYZ")
        self.assertEqual(error.category, ERROR_CATEGORY_UNCATEGORIZED)

    def test_message_is_optional(self):
        error = ActionError.from_result("error:OPERATION_FAILED")
        self.assertIsNone(error.message)

    def test_validate_rejects_missing_code(self):
        error = ActionError(category=ERROR_CATEGORY_UNCATEGORIZED, code="")
        with self.assertRaises(ValueError):
            validate_action_error(error)

    def test_validate_rejects_an_unrecognized_category(self):
        error = ActionError(category="not_a_real_category", code="X")
        with self.assertRaises(ValueError):
            validate_action_error(error)


if __name__ == "__main__":
    unittest.main()
