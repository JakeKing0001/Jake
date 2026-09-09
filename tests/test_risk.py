"""Test unitari per la classificazione del rischio (v3.2, vedi core/risk.py).

Non istanzia mai SkillRegistry (richiederebbe Ollama/NEST/i file dati veri, vedi la nota
in tests/test_agentics.py): confronta invece SKILL_RISK con gli intent letti direttamente
dal sorgente di core/skill_catalog.py e core/jake_core.py, cosi' una nuova skill aggiunta
li' e non classificata qui fa fallire subito la suite invece di restare scoperta in silenzio."""
import re
import unittest
from pathlib import Path

from core.risk import (
    SELF_CONFIRMING_INTENTS, SKILL_RISK, RiskLevel, is_at_least, needs_central_auth,
    needs_central_confirmation, risk_of,
)

ROOT = Path(__file__).resolve().parent.parent
INTENT_KEY_RE = re.compile(r'"([A-Z][A-Z0-9_]*)"\s*:')
INTENT_TUPLE_RE = re.compile(r'\("([A-Z][A-Z0-9_]*)"\s*,')
REGISTER_CALL_RE = re.compile(r'register_skill\(\s*\n?\s*"([A-Z][A-Z0-9_]*)"')


def _catalog_intents() -> set[str]:
    """Ogni skill built-in raggruppata in core/skill_catalog.py vive dentro un dict
    letterale "INTENT": skill(...) all'interno delle funzioni build_*_skills()."""
    source = (ROOT / "core" / "skill_catalog.py").read_text(encoding="utf-8")
    body = source.split("def build_time_date_skills", 1)[1]
    return set(INTENT_KEY_RE.findall(body))


def _jake_core_registered_intents() -> set[str]:
    """Le skill che hanno bisogno del core intero (non solo del registry) sono registrate a
    parte in JakeCore.__init__, tra i due commenti sotto."""
    source = (ROOT / "core" / "jake_core.py").read_text(encoding="utf-8")
    start = source.index("# Skill che hanno bisogno del core")
    end = source.index("# Indici del recupero semantico")
    body = source[start:end]
    return set(REGISTER_CALL_RE.findall(body)) | set(INTENT_TUPLE_RE.findall(body))


class TestRiskLevelOrdering(unittest.TestCase):
    def test_read_only_is_the_lowest_level(self):
        for level in RiskLevel:
            self.assertTrue(is_at_least(level, RiskLevel.READ_ONLY))

    def test_admin_is_the_highest_level(self):
        for level in RiskLevel:
            self.assertTrue(is_at_least(RiskLevel.ADMIN, level))

    def test_is_at_least_is_strict_between_adjacent_levels(self):
        self.assertFalse(is_at_least(RiskLevel.READ_ONLY, RiskLevel.LOCAL_REVERSIBLE))
        self.assertTrue(is_at_least(RiskLevel.DESTRUCTIVE, RiskLevel.EXTERNAL_ACTION))


class TestRiskOf(unittest.TestCase):
    def test_known_intent_returns_its_classification(self):
        self.assertEqual(risk_of("DELETE_PATH"), RiskLevel.DESTRUCTIVE)
        self.assertEqual(risk_of("GET_TIME"), RiskLevel.READ_ONLY)

    def test_unclassified_intent_defaults_to_admin(self):
        self.assertEqual(risk_of("SOME_BRAND_NEW_PLUGIN_INTENT"), RiskLevel.ADMIN)

    def test_unclassified_intent_is_actually_gated_not_just_labeled(self):
        """Il default ADMIN non serve a niente se non triggera anche l'enforcement vera: questo
        e' esattamente cio' su cui si basa JakeCore._on_skill_installed (F1, vedi ROADMAP.md) per
        garantire che una skill installata a runtime dalla Skill Forge - o un plugin di terze
        parti mai censito qui - chieda sempre conferma/autenticazione al primo utilizzo. Se in
        futuro il default o needs_central_confirmation/needs_central_auth cambiassero in un modo
        che rompe questa garanzia, deve fallire un test qui, non riaprire in silenzio il buco
        gia' corretto in tests/test_jake_core_permissions.py::OnSkillInstalledGateWiringTests."""
        self.assertTrue(needs_central_confirmation("SOME_BRAND_NEW_PLUGIN_INTENT"))
        self.assertTrue(needs_central_auth("SOME_BRAND_NEW_PLUGIN_INTENT"))


class TestEveryRegisteredSkillIsClassified(unittest.TestCase):
    """Il punto centrale: se qualcuno aggiunge una skill al catalogo o a JakeCore senza
    passare da core/risk.py, questo test lo dice invece di lasciarla ricadere sul default
    ADMIN in silenzio."""

    def test_catalog_intents_all_have_an_explicit_risk_level(self):
        catalog_intents = _catalog_intents()
        self.assertGreater(len(catalog_intents), 100, "il parsing del catalogo sembra non aver trovato nulla")
        missing = catalog_intents - SKILL_RISK.keys()
        self.assertEqual(missing, set(), f"skill del catalogo senza classificazione del rischio: {sorted(missing)}")

    def test_jake_core_registered_intents_all_have_an_explicit_risk_level(self):
        registered = _jake_core_registered_intents()
        self.assertGreater(len(registered), 5, "il parsing delle skill registrate in JakeCore sembra non aver trovato nulla")
        missing = registered - SKILL_RISK.keys()
        self.assertEqual(missing, set(), f"skill di JakeCore senza classificazione del rischio: {sorted(missing)}")

    def test_no_stale_entries_for_intents_that_no_longer_exist(self):
        """L'opposto: se una skill viene rimossa, la sua voce in SKILL_RISK va tolta insieme
        a lei, altrimenti il censimento mente su cosa Jake sa fare davvero."""
        known = _catalog_intents() | _jake_core_registered_intents()
        stale = SKILL_RISK.keys() - known
        self.assertEqual(stale, set(), f"voci di SKILL_RISK per intent che non esistono piu': {sorted(stale)}")


class TestNeedsCentralAuth(unittest.TestCase):
    """v5.4, Permissions & Security Kernel: il gradino REQUIRE_AUTH, un livello sopra
    needs_central_confirmation (che copre DESTRUCTIVE e superiori)."""

    def test_admin_non_self_confirming_intent_needs_auth(self):
        self.assertTrue(needs_central_auth("SET_POWER_PLAN"))

    def test_self_confirming_admin_intent_does_not_need_the_central_gate(self):
        for intent in ("SYSTEM_POWER", "RUN_COMMAND", "RUN_PYTHON_SCRIPT", "CREATE_SKILL"):
            self.assertIn(intent, SELF_CONFIRMING_INTENTS)
            self.assertFalse(needs_central_auth(intent), intent)

    def test_destructive_intent_does_not_need_auth_only_confirmation(self):
        self.assertEqual(risk_of("DELETE_PATH"), RiskLevel.DESTRUCTIVE)
        self.assertFalse(needs_central_auth("DELETE_PATH"))

    def test_read_only_intent_does_not_need_auth(self):
        self.assertFalse(needs_central_auth("GET_TIME"))


if __name__ == "__main__":
    unittest.main()
