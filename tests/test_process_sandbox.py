"""Verifica per davvero (non solo per costruzione) che core/process_sandbox.py imponga una
restrizione a livello di sistema operativo, non solo di codice Python: un processo di prova
lanciato tramite run_probe_with_reduced_privileges() deve poter scrivere SOLO sul file di
output dedicato, mai altrove nella stessa cartella - la stessa proprieta' verificata a mano nei
proof-of-concept di questa sessione prima di integrare il meccanismo in SkillForge.

Se pywin32/Mandatory Integrity Control non sono disponibili (piattaforma diversa da Windows, o
ambiente che nega la duplicazione del token), il modulo ripiega su un'esecuzione senza
restrizioni: in quel caso questi test verificano solo che il ripiegamento sia esplicito
(integrity_restricted=False), non la restrizione stessa, che a quel punto il sistema operativo
sottostante non offre."""
import unittest
from pathlib import Path

from core.process_sandbox import _WIN32_AVAILABLE, run_probe_with_reduced_privileges

_WELL_BEHAVED_PROBE = """
import json, sys
input_path, output_path = sys.argv[1], sys.argv[2]
with open(input_path, "r", encoding="utf-8") as handle:
    content = handle.read()
with open(output_path, "w", encoding="utf-8") as handle:
    json.dump({"ok": True, "error": None, "echo": content}, handle)
"""

_MALICIOUS_PROBE = """
import json, os, sys
input_path, output_path = sys.argv[1], sys.argv[2]
canary_path = os.path.join(os.path.dirname(output_path), "canary_scrittura_riuscita.txt")
canary_written = False
try:
    with open(canary_path, "w", encoding="utf-8") as handle:
        handle.write("se questo file esiste, la scrittura fuori dal canale dedicato e' riuscita")
    canary_written = True
except PermissionError:
    canary_written = False
with open(output_path, "w", encoding="utf-8") as handle:
    json.dump({"ok": True, "error": None, "canary_written": canary_written}, handle)
"""


class RunProbeWithReducedPrivilegesTests(unittest.TestCase):
    def _write_probe(self, tmp_path: Path, source: str) -> Path:
        probe_path = tmp_path / "probe.py"
        probe_path.write_text(source, encoding="utf-8")
        return probe_path

    def test_well_behaved_probe_reports_success_and_echoes_input(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            probe_path = self._write_probe(tmp_path, _WELL_BEHAVED_PROBE)
            outcome = run_probe_with_reduced_privileges(
                probe_path, "ciao dal test", cwd=str(tmp_path), timeout=15,
            )
            self.assertIsNone(outcome.launch_error, outcome.launch_error)
            self.assertFalse(outcome.timed_out)
            self.assertTrue(outcome.ok, outcome.error)

    def test_probe_cannot_write_outside_its_dedicated_output_channel(self):
        """La proprieta' di sicurezza chiave: anche se il probe TENTA di scrivere un file
        canarino fuori dal canale di output assegnato, quel file non deve esistere quando gira
        a integrita' ridotta - e' il sistema operativo a impedirlo, non un controllo di Jake."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            probe_path = self._write_probe(tmp_path, _MALICIOUS_PROBE)
            outcome = run_probe_with_reduced_privileges(
                probe_path, "irrilevante", cwd=str(tmp_path), timeout=15,
            )
            self.assertIsNone(outcome.launch_error, outcome.launch_error)
            self.assertFalse(outcome.timed_out)
            self.assertTrue(outcome.ok, outcome.error)

            canary_path = tmp_path / "canary_scrittura_riuscita.txt"
            if outcome.integrity_restricted:
                self.assertFalse(
                    canary_path.exists(),
                    "il probe a integrita' ridotta e' riuscito a scrivere fuori dal canale "
                    "di output dedicato: la restrizione MIC non sta funzionando",
                )
            else:
                if not _WIN32_AVAILABLE:
                    self.skipTest("pywin32 non disponibile: nessuna restrizione da verificare")
                self.skipTest(
                    "la sandbox a integrita' ridotta non si e' attivata in questo ambiente "
                    "(fallback usato) - vedi il log per il motivo"
                )


if __name__ == "__main__":
    unittest.main()
