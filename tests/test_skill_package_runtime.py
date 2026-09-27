"""F8.2/F8.3 nel runtime: il catalogo dei pacchetti firmati non era usato da nessuno all'avvio. Ora JakeCore carica
le skill installate (ricontrollando firma e hash), l'installazione passa da piano -> digest -> approvazione (ADMIN
per la policy) e il rischio DICHIARATO dal manifest verificato arriva a risk_of. Crittografia e ZIP veri."""
import logging

from core.response_formatter import format_skill_result
from core.risk import PACKAGE_RISK, RiskLevel, register_package_risk, risk_of
from core.skill_package import TrustStore
from tests.test_skill_package import MARKER_SOURCE, FakeRegistry, PackageTestCase

# Lo stesso sorgente dei test del catalogo, ma senza il file marcatore scritto all'import: qui la skill viene
# caricata due volte (installazione e riavvio) e un file in piu' nella cartella installata sarebbe (giustamente)
# un'alterazione che la manda in quarantena.
CLEAN_SOURCE = "\n".join(line for line in MARKER_SOURCE.splitlines() if "IMPORTED.marker" not in line) + "\n"


class SkillPackageRuntimeTests(PackageTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "runtime"
        trust = TrustStore(self.root / "trust.json")
        trust.add("Davide", self.key.public_b64(), "davide.")
        self.addCleanup(PACKAGE_RISK.clear)

    def _core(self):
        from core.jake_core import JakeCore

        core = JakeCore.__new__(JakeCore)
        core.skill_registry = FakeRegistry()
        core.logger = logging.getLogger("test.skill_package_runtime")
        core.loaded_packages = core._load_skill_packages({"skill_packages_dir": str(self.root)})
        # come nella sezione delle skill del core di JakeCore.__init__
        from skills.skill_packages import InstallSkillPackageSkill, PlanSkillInstallSkill
        core.skill_registry.skills["PLAN_SKILL_INSTALL"] = PlanSkillInstallSkill(core.skill_store)
        core.skill_registry.skills["INSTALL_SKILL_PACKAGE"] = InstallSkillPackageSkill(
            core.skill_store, on_installed=core._activate_skill_package)
        return core

    def _write(self):
        package, signature = self.signed(source=CLEAN_SOURCE)
        package_path = self.tmp / "moneta.zip"
        package_path.write_bytes(package)
        (self.tmp / "moneta.zip.sig.json").write_text(__import__("json").dumps(signature), encoding="utf-8")
        return str(package_path)

    def test_plan_then_install_with_the_seen_digest_then_load_again_at_startup(self):
        core = self._core()
        self.assertEqual(core.loaded_packages, [])
        path = self._write()

        plan = core.skill_registry.skills["PLAN_SKILL_INSTALL"].execute({"package_path": path})
        self.assertTrue(plan.success, plan)
        self.assertNotIn("TOSS_DEMO_COIN", core.skill_registry.skills, "il piano non installa nulla")
        # cio' che l'utente legge: il riepilogo e l'impronta da confermare, non il dizionario grezzo
        reply = format_skill_result("PLAN_SKILL_INSTALL", plan)
        self.assertIn(plan.data["summary"], reply)
        self.assertIn(plan.data["digest"], reply)
        self.assertNotIn("{", reply)

        install = core.skill_registry.skills["INSTALL_SKILL_PACKAGE"]
        mismatch = install.execute({"package_path": path, "digest": "0" * 64})
        self.assertEqual(mismatch.data["reason"], "digest_mismatch")
        self.assertIn("non è più quello del piano", format_skill_result("INSTALL_SKILL_PACKAGE", mismatch))
        result = install.execute({"package_path": path, "digest": plan.data["digest"]})
        self.assertTrue(result.success, result)
        self.assertEqual(format_skill_result("INSTALL_SKILL_PACKAGE", result),
                         f"Ho installato davide.moneta {result.data['version']} ed è già attiva.")
        self.assertTrue(result.data["loaded"])
        self.assertIn("TOSS_DEMO_COIN", core.skill_registry.skills)
        self.assertEqual(risk_of("TOSS_DEMO_COIN"), RiskLevel.READ_ONLY, "rischio dal manifest verificato")

        PACKAGE_RISK.clear()
        restarted = self._core()
        self.assertEqual(restarted.loaded_packages, ["davide.moneta"])
        self.assertIn("TOSS_DEMO_COIN", restarted.skill_registry.skills)

    def test_a_tampered_installed_package_is_not_loaded_at_startup(self):
        core = self._core()
        path = self._write()
        digest = core.skill_registry.skills["PLAN_SKILL_INSTALL"].execute({"package_path": path}).data["digest"]
        core.skill_registry.skills["INSTALL_SKILL_PACKAGE"].execute({"package_path": path, "digest": digest})
        installed = next((self.root / "store").rglob("skill.py"))
        installed.write_text(installed.read_text(encoding="utf-8") + "\n# alterato\n", encoding="utf-8")
        PACKAGE_RISK.clear()
        self.assertEqual(self._core().loaded_packages, [])

    def test_the_install_intents_are_classified_and_a_package_cannot_redeclare_a_builtin(self):
        self.assertEqual(risk_of("INSTALL_SKILL_PACKAGE"), RiskLevel.ADMIN)
        self.assertEqual(risk_of("PLAN_SKILL_INSTALL"), RiskLevel.READ_ONLY)
        with self.assertRaises(ValueError):
            register_package_risk("DELETE_PATH", RiskLevel.READ_ONLY)
        self.assertEqual(risk_of("SCONOSCIUTO_DA_PACCHETTO"), RiskLevel.ADMIN)


if __name__ == "__main__":
    import unittest

    unittest.main()
