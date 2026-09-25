"""F3.1 — benchmark dei 100 task Computer Use sulla fixture Qt (Gate F3: >= 90% di successo).

    python -m benchmarks.bench_computer_use_tasks            # tutti i 100 task
    python -m benchmarks.bench_computer_use_tasks --tasks 1 2 95

Ogni task gira in isolamento: i suoi test caricano la PROPRIA istanza della fixture (setUpClass/
setUp lanciano un processo nuovo e lo chiudono in tearDown), quindi nessuno stato passa da un task al
successivo. Un task riesce se TUTTI i suoi test passano; un test saltato non e' un successo; un
task `limitation` e' sempre contato come fallito (vedi benchmarks/computer_use_tasks.py). Per ogni
fallimento il report conserva la diagnosi (tipo di errore e messaggio del test, che per i passi di
ComputerAgent include strategia e tentativi). Nessun dato personale: solo la fixture sintetica.
"""
from __future__ import annotations

import argparse
import io
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks._report import save_report  # noqa: E402
from benchmarks.computer_use_tasks import TASKS, Task  # noqa: E402

GATE_SUCCESS_RATE = 0.90


def _diagnosis(test, err) -> str:
    exc_type, exc, _tb = err
    message = str(exc).strip().splitlines()
    first = message[0] if message else ""
    return f"{test.id().rsplit('.', 1)[-1]}: {exc_type.__name__}: {first}"[:400]


def run_task(task: Task) -> dict:
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite(loader.loadTestsFromName(name) for name in task.tests)
    started = time.perf_counter()
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=0, resultclass=None)
    result = runner.run(suite)
    elapsed = time.perf_counter() - started
    problems = [_diagnosis(t, e) for t, e in result.failures + result.errors]
    problems += [f"{t.id().rsplit('.', 1)[-1]}: saltato: {reason}" for t, reason in result.skipped]
    tests_passed = not problems and result.testsRun == len(task.tests)
    if task.kind == "limitation":
        status = "limitation" if tests_passed else "limitation_changed"
        diagnosis = ["limite noto: obiettivo non raggiungibile oggi"] + problems
    else:
        status = "passed" if tests_passed else ("skipped" if result.skipped and not (result.failures or result.errors) else "failed")
        diagnosis = problems
    return {
        "task": task.number, "title": task.title, "kind": task.kind, "area": task.area,
        "status": status, "success": status == "passed", "seconds": round(elapsed, 2),
        "tests": len(task.tests), "diagnosis": diagnosis,
    }


def summarize(results: list[dict]) -> dict:
    total = len(results)
    succeeded = sum(1 for r in results if r["success"])
    rate = succeeded / total if total else 0.0
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {
        "total": total, "succeeded": succeeded, "success_rate": round(rate, 4),
        "by_status": by_status, "gate_threshold": GATE_SUCCESS_RATE,
        "gate": "PASS" if total == 100 and rate >= GATE_SUCCESS_RATE else ("FAIL" if total == 100 else "PARTIAL"),
        "not_succeeded": [{k: r[k] for k in ("task", "title", "status", "diagnosis")} for r in results if not r["success"]],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark dei 100 task Computer Use")
    parser.add_argument("--tasks", type=int, nargs="*", help="solo questi numeri di task")
    args = parser.parse_args(argv)
    selected = [t for t in TASKS if not args.tasks or t.number in args.tasks]

    from core.win_dpi import ensure_dpi_aware

    ensure_dpi_aware()
    results = []
    for task in selected:
        outcome = run_task(task)
        results.append(outcome)
        mark = {"passed": "OK ", "failed": "KO ", "skipped": "SKIP", "limitation": "LIM", "limitation_changed": "LIM?"}[outcome["status"]]
        print(f"[{mark}] Task {task.number:3d} {task.title} ({outcome['seconds']} s)")
        for line in outcome["diagnosis"]:
            print(f"        - {line}")
    summary = summarize(results)
    path = save_report("computer_use_tasks", {"summary": summary, "results": results})
    print(f"\nSuccesso: {summary['succeeded']}/{summary['total']} = {summary['success_rate']:.0%} "
          f"(soglia gate {GATE_SUCCESS_RATE:.0%}) -> {summary['gate']}; per stato: {summary['by_status']}")
    print(f"Report: {path}")
    return 0 if summary["gate"] in ("PASS", "PARTIAL") and not [r for r in results if r["status"] == "failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
