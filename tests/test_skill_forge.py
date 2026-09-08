"""Test unitari per i controlli di sicurezza della Skill Forge (v5.3, Self-Improvement
controllato). FORBIDDEN_PATTERNS (una lista nera testuale) e' gia' in produzione da tempo; qui
si copre soprattutto _check_ast_escapes, che chiude due tecniche note per aggirare un controllo
solo testuale: getattr(obj, "nome_pericoloso") invece della chiamata diretta, e la catena
classica di sandbox-escape di Python (__class__ -> __bases__/__mro__ -> __subclasses__()).
Nessuna vera chiamata a Ollama: si testa solo la validazione statica, non propose()."""
import ast
import unittest

from core.skill_forge import ForgeError, SkillForge

VALID_PLUGIN = '''
from core.skill_result import SkillResult

EXAMPLES = ["fai una cosa di prova", "esegui il test", "prova questa capacita'", "fammi vedere un test"]


class SampleTestSkill:
    metadata = {
        "intent": "SAMPLE_TEST_SKILL",
        "description": "Una skill di prova.",
        "parameters": {},
    }

    def execute(self, parameters=None):
        return SkillResult(success=True, data={"ok": True})

    def format_result(self, result):
        return "Fatto."


def register(registry):
    registry.register_skill("SAMPLE_TEST_SKILL", SampleTestSkill())
'''


class FakeRegistry:
    def list_capabilities(self):
        return []


def _forge() -> SkillForge:
    return SkillForge(FakeRegistry())


class AstEscapeDetectionTests(unittest.TestCase):
    def _check(self, source: str) -> None:
        SkillForge._check_ast_escapes(ast.parse(source))

    def test_dangerous_attribute_access_is_rejected(self):
        for attr in ("__subclasses__", "__bases__", "__globals__", "__mro__", "__builtins__"):
            with self.assertRaises(ForgeError, msg=attr):
                self._check(f"x = something.{attr}")

    def test_getattr_indirection_to_eval_is_rejected(self):
        with self.assertRaises(ForgeError):
            self._check('f = getattr(__builtins__, "eval")')

    def test_getattr_indirection_to_os_system_is_rejected(self):
        with self.assertRaises(ForgeError):
            self._check('import os\nf = getattr(os, "system")')

    def test_classic_sandbox_escape_chain_is_rejected(self):
        # ''.__class__.__bases__[0].__subclasses__() - risale a object per trovare classi
        # non ristrette a partire da una stringa vuota del tutto innocua.
        with self.assertRaises(ForgeError):
            self._check("x = ''.__class__.__bases__[0].__subclasses__()")

    def test_dynamic_import_module_is_rejected(self):
        with self.assertRaises(ForgeError):
            self._check('import importlib\nm = importlib.import_module("os")')

    def test_ordinary_getattr_with_a_safe_name_is_allowed(self):
        self._check('value = getattr(obj, "nome_normale", None)')

    def test_ordinary_attribute_access_is_allowed(self):
        self._check("x = something.upper()\ny = obj.value\nz = path.name")

    def test_class_attribute_alone_is_allowed(self):
        # __class__ da solo (senza risalire a __bases__/__subclasses__) e' normale introspezione.
        self._check("t = type(x).__class__")


class FullValidatePipelineTests(unittest.TestCase):
    def test_legitimate_plugin_passes_validation(self):
        intent, description, examples = _forge()._validate(VALID_PLUGIN)
        self.assertEqual(intent, "SAMPLE_TEST_SKILL")
        self.assertEqual(description, "Una skill di prova.")
        self.assertGreaterEqual(len(examples), 2)

    def test_plugin_with_indirect_escape_is_rejected_before_reaching_the_sandbox(self):
        malicious = VALID_PLUGIN.replace(
            'return SkillResult(success=True, data={"ok": True})',
            'getattr(__builtins__, "eval")("1")\n        return SkillResult(success=True, data={"ok": True})',
        )
        with self.assertRaises(ForgeError):
            _forge()._validate(malicious)


if __name__ == "__main__":
    unittest.main()
