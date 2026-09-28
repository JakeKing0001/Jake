"""F8.3 (Skill Forge 2.0): la skill generata diventa un pacchetto firmato nel catalogo, non un file sciolto in plugins/.

Solo il modello e' finto (risponde con un plugin scritto qui): i controlli statici, le esecuzioni di prova nella
sandbox (processo separato), il manifest, il pacchetto ZIP, la firma Ed25519, il piano, l'approvazione legata al
digest, l'installazione, il caricamento con ricontrollo di firma/hash e la disinstallazione sono quelli veri."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core.skill_forge import FORGE_ID_PREFIX, ForgeError, SkillForge
from core.skill_manifest import MANIFEST_FILENAME, Environment
from core.skill_package import SkillStore, TrustStore
from skills.skill_forge_skills import CreateSkillSkill, DeleteCreatedSkillSkill

ENV = Environment(jake_version="5.9", python_version="3.12.6", windows_version="10.0")

DOUBLER = '''
from core.skill_result import SkillResult

EXAMPLES = ["raddoppia 21", "quanto fa il doppio di 4", "dammi il doppio di 10"]
FIXTURES = [{"input": {"number": 21}}, {"input": {}}]


class DoubleNumberSkill:
    metadata = {
        "intent": "DOUBLE_NUMBER",
        "description": "Raddoppia un numero.",
        "parameters": {"number": {"type": "integer", "required": True, "description": "Il numero"}},
    }

    def execute(self, parameters=None):
        parameters = parameters or {}
        if "number" not in parameters:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        return SkillResult(success=True, data={"result": parameters["number"] * 2})

    def format_result(self, result):
        return f"Fa {result.data['result']}."


def register(registry):
    registry.register_skill("DOUBLE_NUMBER", DoubleNumberSkill())
'''

NETWORK = DOUBLER.replace("from core.skill_result import SkillResult",
                          "import urllib.parse\n\nfrom core.skill_result import SkillResult")

NEVER_WORKS = DOUBLER.replace('FIXTURES = [{"input": {"number": 21}}, {"input": {}}]', 'FIXTURES = [{"input": {}}]')


class FakeClient:
    def __init__(self, *answers):
        self.answers = [f"```python\n{answer}\n```" for answer in answers]

    def is_available(self):
        return True

    def pick_model(self, preferred, fallback):
        return "coder-finto"

    def chat_text(self, model, messages, options=None, timeout=None):
        return self.answers.pop(0)


class Registry:
    def __init__(self):
        self.skills = {}
        self.sandboxed = {}

    def list_capabilities(self):
        return [{"intent": intent} for intent in self.skills]

    def register_skill(self, intent, skill, plugin_path=None):
        self.skills[intent] = skill
        if plugin_path is not None:
            self.sandboxed[intent] = plugin_path   # SkillRegistry lo esegue nel worker isolato (F1.6)

    def unregister_skill(self, intent):
        self.skills.pop(intent, None)
        self.sandboxed.pop(intent, None)


class ForgePackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jake_forge_packages_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.registry = Registry()

    def _store(self) -> SkillStore:
        return SkillStore(self.tmp / "catalog", TrustStore(self.tmp / "catalog" / "trust.json"), ENV,
                          installed_versions=lambda name: None)

    def _forge(self, *answers) -> SkillForge:
        return SkillForge(self.registry, client=FakeClient(*answers), plugins_dir=self.tmp / "plugins",
                          skill_store=self._store())

    def test_a_forged_skill_is_installed_as_a_signed_package_with_fixtures_from_the_sandbox(self):
        forge = self._forge(DOUBLER)
        create = CreateSkillSkill(forge)

        proposal = create.execute({"request": "impara a raddoppiare un numero"})

        self.assertEqual(proposal.error, "CONFIRMATION_REQUIRED")
        self.assertIn("Permessi richiesti: nessuno. Rischio massimo: sola lettura.", proposal.data["message"])
        self.assertNotIn("DOUBLE_NUMBER", self.registry.skills, "niente e' installato prima del si'")
        confirm = proposal.data["confirm_parameters"]
        self.assertEqual(len(confirm["digest"]), 64)

        installed = create.execute(confirm)

        self.assertTrue(installed.success, installed)
        skill_id = f"{FORGE_ID_PREFIX}double_number"
        folder = Path(installed.data["path"])
        manifest = json.loads((folder / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        spec = manifest["intents"][0]
        self.assertEqual((manifest["id"], manifest["provenance"]["kind"], spec["risk"]), (skill_id, "forge", "read_only"))
        # le fixture sono l'esito reale delle prove nella sandbox, non scritte dal modello
        self.assertEqual(spec["fixtures"][0]["output"], {"result": 42})
        self.assertEqual(spec["fixtures"][1]["error_code"], "missing_parameters")
        self.assertEqual(spec["input_schema"]["properties"], {"number": {"type": "integer"}})
        self.assertIn("DOUBLE_NUMBER", self.registry.sandboxed, "gira nel worker isolato come ogni pacchetto")
        self.assertFalse(list((self.tmp / "plugins").glob("*.py")) if (self.tmp / "plugins").exists() else [],
                         "nessun file sciolto in plugins/")

        # il test incluso nel pacchetto riesegue davvero le fixture contro la skill installata
        run = subprocess.run([sys.executable, "-m", "unittest", "-q", "test_skill"], cwd=folder, capture_output=True,
                             text=True, timeout=60, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
                                                         "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(run.returncode, 0, run.stderr)

        # riavvio: un catalogo nuovo ricarica il pacchetto ricontrollando firma e hash
        restarted = self._store()
        self.assertEqual(restarted.verify_installed(skill_id, "1.0.0"), [])
        fresh = Registry()
        self.assertTrue(restarted.load(fresh, skill_id).ok)
        self.assertEqual(fresh.skills["DOUBLE_NUMBER"].execute({"number": 5}).data, {"result": 10})

    def test_imports_that_reach_the_network_raise_the_declared_risk_and_are_shown_before_the_yes(self):
        forge = self._forge(NETWORK)
        proposal = CreateSkillSkill(forge).execute({"request": "raddoppia usando la rete"})
        self.assertIn("accede alla rete", proposal.data["message"])
        self.assertIn("azione verso l'esterno", proposal.data["message"])
        draft = next(iter(forge.drafts.values()))
        self.assertEqual(draft.risk, "external_action")

    def test_a_skill_whose_every_trial_run_fails_is_not_packaged(self):
        forge = self._forge(NEVER_WORKS, NEVER_WORKS)
        with self.assertRaises(ForgeError) as caught:
            forge.propose("raddoppia un numero")
        self.assertIn("nessuna esecuzione di prova", str(caught.exception))

    def test_the_yes_installs_only_the_package_that_was_shown(self):
        forge = self._forge(DOUBLER)
        confirm = CreateSkillSkill(forge).execute({"request": "raddoppia"}).data["confirm_parameters"]

        refused = CreateSkillSkill(forge).execute({**confirm, "digest": "0" * 64})
        without = {key: value for key, value in confirm.items() if key != "digest"}
        refused_without_digest = CreateSkillSkill(forge).execute(without)

        self.assertEqual((refused.error, refused_without_digest.error), ("FORGE_FAILED", "FORGE_FAILED"))
        self.assertNotIn("DOUBLE_NUMBER", self.registry.skills)
        self.assertEqual(forge.skill_store.skills(), [])

    def test_deleting_a_forged_skill_removes_it_from_the_catalog_the_disk_and_the_registry(self):
        forge = self._forge(DOUBLER)
        create = CreateSkillSkill(forge)
        installed = create.execute(create.execute({"request": "raddoppia"}).data["confirm_parameters"])
        self.assertEqual(forge.list_created()[0]["intent"], "DOUBLE_NUMBER")

        deleted = DeleteCreatedSkillSkill(forge).execute({"name": "raddoppia"})

        self.assertTrue(deleted.success, deleted)
        self.assertFalse(Path(deleted.data["path"]).exists())
        self.assertFalse(Path(installed.data["path"]).exists())
        self.assertEqual(forge.skill_store.skills(), [])
        self.assertNotIn("DOUBLE_NUMBER", self.registry.skills)


if __name__ == "__main__":
    unittest.main()
