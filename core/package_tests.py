"""I test dichiarati da un pacchetto di skill, eseguiti davvero prima di installarlo (F8.3).

Il manifest obbliga a dichiarare almeno un file di test (F8.1.5), ma nessuno li eseguiva: un pacchetto poteva
dichiarare un `test_skill.py` vuoto o rotto ed essere installato lo stesso. Qui il pacchetto GIA' verificato (firma,
integrita', archivio e manifest: `core/skill_package.verify_package`) viene estratto in una cartella temporanea e i
suoi test girano in un processo separato a integrita' ridotta (`core/process_sandbox.py`, stessa sandbox delle prove
della Skill Forge): un test che prova a scrivere fuori dalla sua cartella viene fermato dal sistema operativo, non da
una lista. Nessun file di test o nessun test dentro i file conta come fallimento: una promessa non mantenuta.

`core/skill_package.py` resta il modulo che non esegue MAI codice di un pacchetto; questo e' il solo che lo fa, e solo
nella sandbox."""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from core.process_sandbox import run_probe_with_reduced_privileges

PROBE_PATH = Path(__file__).resolve().parent / "package_test_probe.py"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class PackageTestReport:
    ok: bool
    tests_run: int = 0
    failures: list = field(default_factory=list)
    error: str = ""
    restricted: bool = False   # la sandbox a integrita' ridotta era davvero attiva

    def summary(self) -> str:
        if self.ok:
            return f"Test del pacchetto: {self.tests_run} superati nella sandbox."
        detail = self.error or ("falliti: " + ", ".join(self.failures[:3]))
        return f"Test del pacchetto NON superati ({detail})."


def run_declared_tests(verified, timeout: float = 60) -> PackageTestReport:
    """`verified`: un `VerifiedPackage` (file in memoria + manifest validato)."""
    tests = list(verified.manifest.tests.get("files") or [])
    if not tests:
        return PackageTestReport(False, error="nessun file di test dichiarato")
    with tempfile.TemporaryDirectory(prefix="jake_package_tests_") as tmp:
        root = Path(tmp)
        for name, content in verified.files.items():
            target = (root / name).resolve()
            target.relative_to(root.resolve())   # difesa in profondita': i nomi sono gia' stati controllati
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        request = json.dumps({"directory": str(root), "tests": tests, "project_root": str(PROJECT_ROOT)})
        outcome = run_probe_with_reduced_privileges(PROBE_PATH, request, cwd=str(root), timeout=timeout)
    if outcome.launch_error:
        return PackageTestReport(False, error=outcome.launch_error)
    if outcome.timed_out:
        return PackageTestReport(False, error=f"i test non finiscono entro {timeout:.0f} s", restricted=outcome.integrity_restricted)
    payload = outcome.payload
    return PackageTestReport(bool(outcome.ok), int(payload.get("tests_run") or 0), list(payload.get("failures") or []),
                             str(outcome.error or ""), outcome.integrity_restricted)
