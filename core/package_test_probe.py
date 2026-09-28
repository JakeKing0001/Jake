"""Sonda che esegue i test dichiarati da un pacchetto di skill (F8.3), in un processo separato a integrita' ridotta
(core/process_sandbox.py) lanciato da core/package_tests.py. Legge {directory, tests, project_root} dal file di input e
scrive l'esito come JSON nel file di output: niente stdin/stdout, stesso canale verificato della sonda della Forge.

Gira come script standalone (`python package_test_probe.py <input> <output>`): nessun import di Jake qui in cima."""
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path


def main(input_path: str, output_path: str) -> None:
    result: dict = {"ok": False, "error": None, "tests_run": 0, "failures": []}
    try:
        with open(input_path, encoding="utf-8") as handle:
            spec = json.load(handle)
        directory = Path(spec["directory"])
        sys.path.insert(0, str(spec["project_root"]))
        sys.path.insert(0, str(directory))
        suite = unittest.TestSuite()
        for index, relative in enumerate(spec["tests"]):
            module_spec = importlib.util.spec_from_file_location(f"jake_package_test_{index}", directory / relative)
            if module_spec is None or module_spec.loader is None:
                raise ImportError(f"test non caricabile: {relative}")
            module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(module)
            suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
        outcome = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        problems = [test.id() for test, _ in outcome.failures + outcome.errors]
        result.update(tests_run=outcome.testsRun, failures=problems[:10],
                      ok=outcome.testsRun > 0 and outcome.wasSuccessful())
        if outcome.testsRun == 0:
            result["error"] = "i file di test dichiarati non contengono nessun test"
    except Exception as exc:  # la sonda scrive sempre un esito, mai propaga
        result["error"] = f"{type(exc).__name__}: {exc}"
    try:
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle)
    except OSError:
        pass


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
