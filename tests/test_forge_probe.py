"""Test unitari per core/forge_probe.py: nessuna suite esisteva finora, nonostante sia il
componente di sicurezza che valida un plugin candidato (codice generato, potenzialmente ostile)
PRIMA che Skill Forge lo installi davvero - vedi core/skill_forge.py::_sandbox_import(). Gira
normalmente come script standalone in un processo separato a integrita' ridotta; qui si chiama
main() direttamente in-process (comportamento identico, solo senza il processo separato), con
file VERI su disco temporaneo (mai mockati, perche' il contratto del file e' l'intera interfaccia
del componente)."""
import json
import tempfile
import unittest
from pathlib import Path

from core.forge_probe import main


def _run_probe(code: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "input.py"
        output_path = Path(tmp) / "output.json"
        input_path.write_text(code, encoding="utf-8")
        main(str(input_path), str(output_path))
        return json.loads(output_path.read_text(encoding="utf-8"))


VALID_PLUGIN = """
class DummySkill:
    def execute(self, parameters):
        from core.skill_result import SkillResult
        return SkillResult(success=True, data={})

    def format_result(self, outcome):
        return "fatto"


def register(registry):
    registry.register_skill("DUMMY_INTENT", DummySkill())
"""


class ForgeProbeTests(unittest.TestCase):
    def test_a_valid_plugin_is_accepted(self):
        result = _run_probe(VALID_PLUGIN)
        self.assertTrue(result["ok"])
        self.assertEqual(result["registered"], ["DUMMY_INTENT"])
        self.assertIsNone(result["error"])

    def test_a_plugin_without_register_is_rejected(self):
        result = _run_probe("x = 1\n")
        self.assertFalse(result["ok"])
        self.assertIn("register", result["error"])

    def test_a_register_that_registers_nothing_is_rejected(self):
        code = "def register(registry):\n    pass\n"
        result = _run_probe(code)
        self.assertFalse(result["ok"])
        self.assertIn("non ha registrato nulla", result["error"])

    def test_a_skill_whose_execute_does_not_return_a_skill_result_is_rejected(self):
        code = """
class BadSkill:
    def execute(self, parameters):
        return {"success": True}


def register(registry):
    registry.register_skill("BAD_INTENT", BadSkill())
"""
        result = _run_probe(code)
        self.assertFalse(result["ok"])
        self.assertIn("SkillResult", result["error"])

    def test_a_format_result_that_does_not_return_a_string_is_rejected(self):
        code = """
class BadFormatSkill:
    def execute(self, parameters):
        from core.skill_result import SkillResult
        return SkillResult(success=True, data={})

    def format_result(self, outcome):
        return 42


def register(registry):
    registry.register_skill("BAD_FORMAT_INTENT", BadFormatSkill())
"""
        result = _run_probe(code)
        self.assertFalse(result["ok"])
        self.assertIn("format_result", result["error"])

    def test_a_failing_execute_does_not_require_format_result(self):
        code = """
class FailingSkill:
    def execute(self, parameters):
        from core.skill_result import SkillResult
        return SkillResult(success=False, data={}, error="BOOM")


def register(registry):
    registry.register_skill("FAILING_INTENT", FailingSkill())
"""
        result = _run_probe(code)
        self.assertTrue(result["ok"])

    def test_a_syntax_error_is_captured_not_propagated(self):
        result = _run_probe("def register(registry:\n    pass\n")
        self.assertFalse(result["ok"])
        self.assertIn("SyntaxError", result["error"])

    def test_an_exception_raised_while_executing_the_plugin_is_captured(self):
        code = """
raise RuntimeError("il plugin esplode all'importazione")
"""
        result = _run_probe(code)
        self.assertFalse(result["ok"])
        self.assertIn("RuntimeError", result["error"])

    def test_project_root_is_added_to_sys_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.py"
            output_path = Path(tmp) / "output.json"
            input_path.write_text(
                "import sys\n"
                f"assert {tmp!r} in sys.path, sys.path\n"
                "def register(registry):\n"
                "    pass\n",
                encoding="utf-8",
            )
            main(str(input_path), str(output_path), project_root=tmp)
            result = json.loads(output_path.read_text(encoding="utf-8"))
        # register() non registra nulla, quindi ok resta False, ma l'errore NON deve essere
        # un AssertionError sul path (altrimenti project_root non sarebbe stato inserito).
        self.assertIn("non ha registrato nulla", result["error"])

    def test_an_unwritable_output_path_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.py"
            input_path.write_text(VALID_PLUGIN, encoding="utf-8")
            unwritable_output = Path(tmp) / "cartella_inesistente" / "output.json"
            main(str(input_path), str(unwritable_output))
            self.assertFalse(unwritable_output.exists())


if __name__ == "__main__":
    unittest.main()
