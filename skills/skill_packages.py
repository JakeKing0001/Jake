import json
from pathlib import Path

from core.skill_result import SkillResult


def _read_package(parameters: dict) -> tuple[bytes, dict] | SkillResult:
    package_path = Path(str(parameters.get("package_path") or "").strip().strip('"'))
    if not str(package_path) or not package_path.is_file():
        return SkillResult(success=False, data={"package_path": str(package_path)}, error="NOT_FOUND")
    signature_path = Path(str(parameters.get("signature_path") or f"{package_path}.sig.json").strip().strip('"'))
    if not signature_path.is_file():
        return SkillResult(success=False, data={"signature_path": str(signature_path)}, error="SIGNATURE_MISSING")
    try:
        signature = json.loads(signature_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SkillResult(success=False, data={}, error="SIGNATURE_INVALID")
    return package_path.read_bytes(), signature


class PlanSkillInstallSkill:
    """F8.2/F8.3, primo passo dell'installazione: verifica firma, integrita', archivio e manifest di un pacchetto
    locale e mostra il PIANO (versione, permessi, rischio, dipendenze, digest) senza installare nulla. Nessun
    codice del pacchetto viene eseguito."""

    metadata = {
        "intent": "PLAN_SKILL_INSTALL",
        "description": "Verifica un pacchetto di skill firmato (file locale) e mostra cosa installerebbe, con permessi e rischio, senza installarlo.",
        "parameters": {
            "package_path": {"type": "string", "required": True, "description": "Percorso del pacchetto .zip."},
            "signature_path": {"type": "string", "required": False, "description": "Firma (default: <pacchetto>.sig.json)."},
        },
    }

    def __init__(self, store):
        self.store = store

    def execute(self, parameters: dict = None):
        from core.skill_package import PackageError

        if self.store is None:
            return SkillResult(success=False, data={}, error="SKILL_STORE_UNAVAILABLE")
        loaded = _read_package(parameters or {})
        if isinstance(loaded, SkillResult):
            return loaded
        package, signature = loaded
        try:
            plan = self.store.plan_install(package, signature)
        except PackageError as exc:
            return SkillResult(success=False, data={"reason": exc.code}, error="PACKAGE_REJECTED")
        # F8.3: i test dichiarati girano davvero, nella sandbox, prima che l'utente decida
        from core.package_tests import run_declared_tests

        tests = run_declared_tests(plan.verified)
        blockers = list(plan.blockers) + ([] if tests.ok else [f"tests_failed: {tests.error or ', '.join(tests.failures[:3])}"])
        return SkillResult(success=True, data={
            "summary": plan.summary + "\n" + tests.summary(), "digest": plan.verified.digest, "blockers": blockers,
            "permissions_increased": plan.permissions_increased,
            "tests": {"ok": tests.ok, "run": tests.tests_run, "failures": tests.failures},
        })


class InstallSkillPackageSkill:
    """F8.2.4/F8.3, secondo passo: installa SOLO il pacchetto il cui digest l'utente ha visto nel piano
    (PLAN_SKILL_INSTALL). Rischio ADMIN: la policy chiede conferma/autenticazione prima di arrivare qui; la
    conferma copre anche l'eventuale aumento di permessi mostrato nel piano. Dopo l'installazione la skill viene
    caricata subito, con il rischio dichiarato dal suo manifest verificato."""

    metadata = {
        "intent": "INSTALL_SKILL_PACKAGE",
        "description": "Installa un pacchetto di skill firmato gia' verificato con PLAN_SKILL_INSTALL (serve il digest mostrato nel piano).",
        "parameters": {
            "package_path": {"type": "string", "required": True, "description": "Percorso del pacchetto .zip."},
            "digest": {"type": "string", "required": True, "description": "Digest mostrato dal piano di installazione."},
            "signature_path": {"type": "string", "required": False, "description": "Firma (default: <pacchetto>.sig.json)."},
        },
    }

    def __init__(self, store, on_installed=None):
        self.store = store
        self.on_installed = on_installed  # callable(skill_id): carica subito la skill installata

    def execute(self, parameters: dict = None):
        from core.skill_package import PackageError, approve

        parameters = parameters or {}
        if self.store is None:
            return SkillResult(success=False, data={}, error="SKILL_STORE_UNAVAILABLE")
        loaded = _read_package(parameters)
        if isinstance(loaded, SkillResult):
            return loaded
        package, signature = loaded
        try:
            plan = self.store.plan_install(package, signature)
            if plan.verified.digest != str(parameters.get("digest") or "").strip():
                return SkillResult(success=False, data={"reason": "digest_mismatch"}, error="PACKAGE_REJECTED")
            # F8.3: niente installazione se i test dichiarati dal pacchetto non passano nella sandbox
            from core.package_tests import run_declared_tests

            tests = run_declared_tests(plan.verified)
            if not tests.ok:
                return SkillResult(success=False, data={"reason": "tests_failed", "detail": tests.summary()},
                                   error="PACKAGE_REJECTED")
            result = self.store.install(plan, approve(plan, "user", allow_permission_increase=True))
        except PackageError as exc:
            return SkillResult(success=False, data={"reason": exc.code}, error="PACKAGE_REJECTED")
        skill_id = plan.verified.manifest.id
        loaded_ok = bool(self.on_installed(skill_id)) if self.on_installed else False
        return SkillResult(success=True, data={"skill_id": skill_id, "version": plan.verified.manifest.version,
                                               "loaded": loaded_ok, "install": result})
