"""Test unitari per core/plugin_loader.py. Il modulo non aveva ancora nessun test, nonostante
sia il punto d'ingresso di codice di terze parti (plugin scritti a mano, o dalla Skill Forge -
vedi core/skill_forge.py) nel processo di Jake: scrive file .py veri su disco temporaneo e li
carica per davvero con importlib, non una simulazione."""
import shutil
import tempfile
import unittest
from pathlib import Path

from core.plugin_loader import load_plugin_file, load_plugins
from core.policy_engine import PolicyEngine
from core.skill_registry import SkillRegistry


class FakeRegistry:
    def __init__(self):
        self.registered = {}
        self.registered_plugin_paths = {}

    def register_skill(self, intent, skill, plugin_path=None):
        self.registered[intent] = skill
        if plugin_path is not None:
            self.registered_plugin_paths[intent] = plugin_path


class FakeLoggerCapturing:
    def __init__(self):
        self.warnings = []
        self.exceptions = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else msg)

    def exception(self, msg, *args):
        self.exceptions.append(msg % args if args else msg)


class PluginLoaderTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="jake_plugin_loader_test_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def _write_plugin(self, filename: str, content: str) -> Path:
        path = self.tmp_dir / filename
        path.write_text(content, encoding="utf-8")
        return path


class LoadPluginFileTests(PluginLoaderTestCase):
    def test_valid_plugin_registers_and_returns_true(self):
        plugin = self._write_plugin("valido.py", (
            "class Skill:\n"
            "    metadata = {'intent': 'FAKE_INTENT', 'description': '', 'parameters': {}}\n"
            "    def execute(self, parameters=None):\n"
            "        return None\n"
            "\n"
            "def register(registry):\n"
            "    registry.register_skill('FAKE_INTENT', Skill())\n"
        ))
        registry = FakeRegistry()

        loaded = load_plugin_file(registry, plugin)

        self.assertTrue(loaded)
        self.assertIn("FAKE_INTENT", registry.registered)

    def test_plugin_without_register_function_is_rejected(self):
        plugin = self._write_plugin("senza_register.py", "x = 1\n")
        registry = FakeRegistry()
        logger = FakeLoggerCapturing()

        loaded = load_plugin_file(registry, plugin, logger=logger)

        self.assertFalse(loaded)
        self.assertEqual(registry.registered, {})
        self.assertEqual(len(logger.warnings), 1)

    def test_plugin_that_raises_on_import_is_isolated(self):
        plugin = self._write_plugin("rotto_import.py", "raise RuntimeError('boom')\n")
        registry = FakeRegistry()
        logger = FakeLoggerCapturing()

        loaded = load_plugin_file(registry, plugin, logger=logger)

        self.assertFalse(loaded)
        self.assertEqual(len(logger.exceptions), 1)

    def test_plugin_whose_register_function_raises_is_isolated(self):
        plugin = self._write_plugin("rotto_register.py", (
            "def register(registry):\n"
            "    raise ValueError('boom')\n"
        ))
        registry = FakeRegistry()
        logger = FakeLoggerCapturing()

        loaded = load_plugin_file(registry, plugin, logger=logger)

        self.assertFalse(loaded)
        self.assertEqual(len(logger.exceptions), 1)

    def test_missing_logger_does_not_crash_on_failure(self):
        plugin = self._write_plugin("senza_register2.py", "x = 1\n")
        registry = FakeRegistry()

        loaded = load_plugin_file(registry, plugin)  # nessun logger passato

        self.assertFalse(loaded)

    def test_the_plugin_file_path_is_forwarded_to_register_skill(self):
        """F1.6 (collegamento del worker sandboxato): load_plugin_file() avvolge la registry in
        un proxy che passa plugin_path a register_skill() - il plugin stesso continua a chiamare
        registry.register_skill(intent, skill) con la stessa firma di sempre, senza saperlo."""
        plugin = self._write_plugin("valido2.py", (
            "class Skill:\n"
            "    metadata = {'intent': 'FAKE_INTENT_2', 'description': '', 'parameters': {}}\n"
            "    def execute(self, parameters=None):\n"
            "        return None\n"
            "\n"
            "def register(registry):\n"
            "    registry.register_skill('FAKE_INTENT_2', Skill())\n"
        ))
        registry = FakeRegistry()

        load_plugin_file(registry, plugin)

        self.assertEqual(registry.registered_plugin_paths["FAKE_INTENT_2"], str(plugin))


class LoadPluginsTests(PluginLoaderTestCase):
    def test_loads_every_valid_plugin_in_the_directory(self):
        for name in ("uno", "due"):
            self._write_plugin(f"{name}.py", (
                "class Skill:\n"
                "    metadata = {'intent': '" + name.upper() + "', 'description': '', 'parameters': {}}\n"
                "    def execute(self, parameters=None):\n"
                "        return None\n"
                "\n"
                "def register(registry):\n"
                "    registry.register_skill('" + name.upper() + "', Skill())\n"
            ))
        registry = FakeRegistry()

        loaded = load_plugins(registry, plugins_dir=self.tmp_dir)

        self.assertEqual(set(loaded), {"uno", "due"})
        self.assertEqual(set(registry.registered), {"UNO", "DUE"})

    def test_a_broken_plugin_does_not_stop_the_others_from_loading(self):
        """Un plugin rotto non deve mai impedire l'avvio di Jake - vedi il docstring del modulo."""
        self._write_plugin("rotto.py", "raise RuntimeError('boom')\n")
        self._write_plugin("buono.py", (
            "def register(registry):\n"
            "    registry.register_skill('BUONO', object())\n"
        ))
        registry = FakeRegistry()

        loaded = load_plugins(registry, plugins_dir=self.tmp_dir)

        self.assertEqual(loaded, ["buono"])
        self.assertIn("BUONO", registry.registered)

    def test_files_starting_with_underscore_are_skipped(self):
        self._write_plugin("_privato.py", (
            "def register(registry):\n"
            "    registry.register_skill('NON_DOVREBBE_CARICARE', object())\n"
        ))
        registry = FakeRegistry()

        loaded = load_plugins(registry, plugins_dir=self.tmp_dir)

        self.assertEqual(loaded, [])
        self.assertEqual(registry.registered, {})

    def test_empty_directory_returns_an_empty_list(self):
        registry = FakeRegistry()

        loaded = load_plugins(registry, plugins_dir=self.tmp_dir)

        self.assertEqual(loaded, [])

    def test_missing_directory_is_created_instead_of_raising(self):
        missing_dir = self.tmp_dir / "non_esiste_ancora"
        registry = FakeRegistry()

        loaded = load_plugins(registry, plugins_dir=missing_dir)

        self.assertEqual(loaded, [])
        self.assertTrue(missing_dir.is_dir())


class UnclassifiedPluginIntentIsStillGatedTests(PluginLoaderTestCase):
    """F1, criterio di uscita 'nessuna skill non classificata': tests/test_risk.py verifica gia'
    che ogni skill BUILT-IN/di JakeCore abbia un livello di rischio esplicito, ma non c'era un
    equivalente end-to-end per un plugin scritto a mano e caricato da load_plugins() - qui si
    verifica la catena reale (load_plugins() -> SkillRegistry.register_skill() vero ->
    PolicyEngine.sync_with_registry() vero, lo stesso ordine di JakeCore.__init__), non solo i
    pezzi isolati: un intent MAI visto da core/risk.py deve comunque finire dietro conferma E
    autenticazione, esattamente come garantito per un intent forgiato a runtime dalla Skill
    Forge (vedi OnSkillInstalledGateWiringTests in tests/test_jake_core_permissions.py)."""

    def test_a_hand_written_plugin_with_an_unclassified_intent_still_requires_confirmation_and_auth(self):
        self._write_plugin("plugin_sconosciuto.py", (
            "class Skill:\n"
            "    metadata = {'intent': 'UN_INTENT_MAI_VISTO_PRIMA', 'description': '', 'parameters': {}}\n"
            "    def execute(self, parameters=None):\n"
            "        return None\n"
            "\n"
            "def register(registry):\n"
            "    registry.register_skill('UN_INTENT_MAI_VISTO_PRIMA', Skill())\n"
        ))
        registry = SkillRegistry.__new__(SkillRegistry)
        registry.skills = {}
        registry.logger = None
        registry._forged_intents = {}
        registry._sandbox_worker = None
        registry._plugin_violation_counts = {}
        registry._quarantined_plugins = set()

        loaded = load_plugins(registry, plugins_dir=self.tmp_dir)
        self.assertEqual(loaded, ["plugin_sconosciuto"])
        self.assertIn("UN_INTENT_MAI_VISTO_PRIMA", registry.skills)

        policy_engine = PolicyEngine()
        policy_engine.sync_with_registry(registry)

        self.assertIn("UN_INTENT_MAI_VISTO_PRIMA", policy_engine.always_confirm_intents)
        self.assertIn("UN_INTENT_MAI_VISTO_PRIMA", policy_engine.require_auth_intents)


if __name__ == "__main__":
    unittest.main()
