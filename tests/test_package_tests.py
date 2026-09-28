"""F8.3: i test dichiarati da un pacchetto di terzi girano davvero, nella sandbox, prima dell'installazione.

Prima un pacchetto poteva dichiarare un test vuoto o rotto ed essere installato lo stesso. Crittografia, ZIP, catalogo,
sandbox e skill PLAN/INSTALL veri."""
import json
import tempfile
from pathlib import Path

from core.package_tests import run_declared_tests
from core.skill_package import verify_package
from skills.skill_packages import InstallSkillPackageSkill, PlanSkillInstallSkill
from tests.test_skill_package import PackageTestCase
from tests.test_skill_package_runtime import CLEAN_SOURCE

FAILING_TEST = """import unittest


class SkillTests(unittest.TestCase):
    def test_something_the_author_promised(self):
        self.assertEqual(1 + 1, 3)
"""

ESCAPING_TEST = """import os
import unittest


class SkillTests(unittest.TestCase):
    def test_writes_outside_its_folder(self):
        with open(os.environ["JAKE_ESCAPE_TARGET"], "w", encoding="utf-8") as handle:
            handle.write("fuori dalla sandbox")
"""


class DeclaredPackageTestsTests(PackageTestCase):
    def _write(self, test_source: str) -> str:
        package, signature = self.signed(source=CLEAN_SOURCE, test_source=test_source)
        path = self.tmp / "pacchetto.zip"
        path.write_bytes(package)
        (self.tmp / "pacchetto.zip.sig.json").write_text(json.dumps(signature), encoding="utf-8")
        return str(path)

    def test_a_failing_declared_test_blocks_the_plan_and_the_installation(self):
        path = self._write(FAILING_TEST)

        plan = PlanSkillInstallSkill(self.store).execute({"package_path": path})
        install = InstallSkillPackageSkill(self.store).execute({"package_path": path, "digest": plan.data["digest"]})

        self.assertIn("Test del pacchetto NON superati", plan.data["summary"])
        self.assertTrue(any(b.startswith("tests_failed") for b in plan.data["blockers"]))
        self.assertEqual(install.data["reason"], "tests_failed")
        self.assertEqual(self.store.skills(), [], "niente installato")

    def test_passing_declared_tests_are_reported_before_the_user_decides(self):
        plan = PlanSkillInstallSkill(self.store).execute({"package_path": self._write(None)})
        self.assertIn("Test del pacchetto: 1 superati nella sandbox.", plan.data["summary"])
        self.assertEqual(plan.data["blockers"], [])

    def test_a_declared_test_cannot_write_outside_its_folder(self):
        import os

        target = Path(tempfile.gettempdir()) / "jake_escape_attempt.txt"
        target.unlink(missing_ok=True)
        self.addCleanup(target.unlink, missing_ok=True)
        os.environ["JAKE_ESCAPE_TARGET"] = str(target)
        self.addCleanup(os.environ.pop, "JAKE_ESCAPE_TARGET", None)
        package, signature = self.signed(source=CLEAN_SOURCE, test_source=ESCAPING_TEST)

        report = run_declared_tests(verify_package(package, signature, self.trust, self.store.env))

        if not report.restricted:
            self.skipTest("sandbox a integrita' ridotta non disponibile in questo ambiente")
        self.assertFalse(report.ok)
        self.assertFalse(target.exists(), "la scrittura fuori dalla cartella e' fermata dal sistema operativo")
