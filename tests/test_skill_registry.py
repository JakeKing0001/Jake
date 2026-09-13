"""Test unitari per core/skill_registry.py::SkillRegistry.register_skill (F1, Trustworthy Agent
Core 3.0 - vedi la fase F1 in ROADMAP.md).

Come tests/test_risk.py, evita di istanziare SkillRegistry() per davvero (il costruttore vero
costruisce anche MemoryManager/NestClient/EmbeddingProvider/VisionProvider/PlannerProvider, vedi
la nota in tests/__init__.py): usa SkillRegistry.__new__ per un oggetto "spoglio" con solo gli
attributi che register_skill() legge (skills, logger), stesso approccio gia' usato per JakeCore
in tests/test_jake_core_permissions.py."""
import sys
import threading
import unittest

from core.policy_engine import PolicyEngine
from core.skill_registry import SkillRegistry
from core.skill_result import SkillResult


class FakeLoggerCapturingWarnings:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else msg)


class FakeSkill:
    metadata = {"intent": "FAKE", "description": "Una skill finta.", "parameters": {}}

    def execute(self, parameters=None):
        return None


def _bare_registry(skills: dict = None) -> SkillRegistry:
    registry = SkillRegistry.__new__(SkillRegistry)
    registry.skills = dict(skills or {})
    registry.logger = FakeLoggerCapturingWarnings()
    return registry


class RegisterSkillCollisionTests(unittest.TestCase):
    """F1: prima di questo controllo, un plugin (o un file copiato per errore dentro plugins/)
    poteva dichiarare un intent gia' esistente e sostituire silenziosamente la skill reale
    dietro quel nome - senza che nulla lo segnalasse, nemmeno nei log. register_skill() e' l'
    unico punto d'ingresso usato da plugin/Skill Forge (le skill built-in arrivano con
    self.skills.update(...) direttamente in __init__, mai da qui)."""

    def test_registering_a_brand_new_intent_does_not_warn(self):
        registry = _bare_registry()

        registry.register_skill("BRAND_NEW_INTENT", FakeSkill())

        self.assertEqual(registry.logger.warnings, [])
        self.assertIn("BRAND_NEW_INTENT", registry.skills)

    def test_registering_an_already_taken_intent_warns_but_still_overwrites(self):
        """Non blocca (un plugin che sostituisce di proposito una skill built-in e' un uso
        legittimo del punto di estensione), ma non deve piu' restare silenzioso."""
        original = FakeSkill()
        replacement = FakeSkill()
        registry = _bare_registry({"SYSTEM_POWER": original})

        registry.register_skill("SYSTEM_POWER", replacement)

        self.assertEqual(len(registry.logger.warnings), 1)
        self.assertIn("SYSTEM_POWER", registry.logger.warnings[0])
        self.assertIs(registry.skills["SYSTEM_POWER"], replacement)

    def test_re_registering_the_exact_same_skill_instance_does_not_warn(self):
        """Ri-registrare la STESSA istanza (es. un modulo plugin ricaricato due volte con lo
        stesso oggetto) non e' una collisione da segnalare."""
        skill = FakeSkill()
        registry = _bare_registry({"SOME_INTENT": skill})

        registry.register_skill("SOME_INTENT", skill)

        self.assertEqual(registry.logger.warnings, [])


class ConcurrentListCapabilitiesTests(unittest.TestCase):
    """F1.8.2 (stesso principio gia' applicato altrove in questa sessione): buco reale,
    riprodotto per davvero prima del fix - list_capabilities() iterava self.skills DIRETTAMENTE
    (`for intent, skill in self.skills.items():`), un dict LIVE che register_skill() (il punto
    d'ingresso di plugin/Skill Forge, raggiungibile da un comando voce/companion mentre
    un'altra richiesta concorrente sta facendo routing/retrieval semantico) puo' mutare in
    qualsiasi momento da un altro thread. Un ciclo `for` su un dict live e' un punto di cambio
    thread naturale a ogni iterazione: se la dimensione del dict cambia a meta' ciclo, Python
    solleva RuntimeError, facendo fallire l'intera richiesta in corso."""

    def setUp(self):
        self._original_switch_interval = sys.getswitchinterval()
        sys.setswitchinterval(0.00001)
        self.addCleanup(sys.setswitchinterval, self._original_switch_interval)

    def test_registering_a_skill_while_listing_capabilities_never_raises(self):
        registry = _bare_registry({f"INTENT_{i}": FakeSkill() for i in range(2000)})
        errors: list[Exception] = []
        errors_lock = threading.Lock()

        def _list_repeatedly():
            for _ in range(50):
                try:
                    registry.list_capabilities()
                except RuntimeError as exc:
                    with errors_lock:
                        errors.append(exc)

        def _register_many():
            for i in range(5000):
                registry.register_skill(f"NEW_INTENT_{i}", FakeSkill())

        threads = [threading.Thread(target=_list_repeatedly), threading.Thread(target=_register_many)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [], "list_capabilities() non deve mai sollevare per una mutazione concorrente")


class RecordingSkill:
    def __init__(self):
        self.calls = 0

    def execute(self, parameters=None):
        self.calls += 1
        return SkillResult(success=True, data={})


class PolicyGateTests(unittest.TestCase):
    """F1.2.1 (percorso 7, l'ultimo dei tre "percorso N" dichiarati aperti - i percorsi 3
    (PlanExecutor.execute) e 6 (rollback_effect) erano gia' fail-closed): questo dispatcher grezzo
    non controllava MAI la policy da solo. Nei due chiamanti di produzione reali
    (JakeCore._resolve_and_execute/_run_confirmed_action) non era gia' sfruttabile - entrambi
    chiamano _authorize_command() PRIMA di arrivare qui - ma restava un default pericoloso per un
    futuro chiamante che se lo dimenticasse, stesso principio "nega per default" gia' applicato ai
    percorsi 3/6."""

    def test_no_policy_engine_blocks_without_calling_the_skill(self):
        skill = RecordingSkill()
        registry = _bare_registry({"CREATE_PATH": skill})

        result = registry.execute("CREATE_PATH", {"path": "x"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "POLICY_BLOCKED")
        self.assertEqual(skill.calls, 0)

    def test_intent_in_blocked_intents_blocks_without_calling_the_skill(self):
        skill = RecordingSkill()
        registry = _bare_registry({"DELETE_PATH": skill})
        policy_engine = PolicyEngine(blocked_intents={"DELETE_PATH"})

        result = registry.execute("DELETE_PATH", {"path": "x"}, policy_engine=policy_engine)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "POLICY_BLOCKED")
        self.assertEqual(skill.calls, 0)

    def test_intent_not_blocked_actually_executes_the_skill(self):
        skill = RecordingSkill()
        registry = _bare_registry({"ADD_NOTE": skill})
        policy_engine = PolicyEngine()

        result = registry.execute("ADD_NOTE", {"text": "x"}, policy_engine=policy_engine)

        self.assertTrue(result.success)
        self.assertEqual(skill.calls, 1)

    def test_unknown_intent_still_returns_none_regardless_of_policy_engine(self):
        """Il controllo di policy si applica solo dopo aver trovato la skill: un intent
        sconosciuto resta None (UNKNOWN_INTENT per chi legge il risultato), non POLICY_BLOCKED -
        comportamento invariato rispetto a prima di questa correzione."""
        registry = _bare_registry({})

        self.assertIsNone(registry.execute("NON_ESISTE", {}))
        self.assertIsNone(registry.execute("NON_ESISTE", {}, policy_engine=PolicyEngine()))


if __name__ == "__main__":
    unittest.main()
