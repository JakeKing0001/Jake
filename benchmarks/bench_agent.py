"""Benchmark ripetibile per l'agente a passi (F0, vedi fase F0 in ROADMAP.md): latenza, numero
di passi ed esito su un piccolo insieme di richieste composte, con Ollama vero (non un client
finto come in tests/test_agent.py, che testa la logica di retry/verifica/rollback in isolamento).

Richiede Ollama in esecuzione. Lanciare a mano con: `python -m benchmarks.bench_agent`.

I compiti qui sotto sono scelti per essere ripetibili e senza effetti collaterali fuori da una
cartella temporanea: nessuno apre un'app, un sito o modifica file veri dell'utente, cosi' il
benchmark si puo' lanciare piu' volte di fila senza lasciare tracce ne' dipendere dallo stato
del PC di chi lo esegue (a differenza di, es., "apri spotify e metti in pausa")."""
import argparse
import shutil
import tempfile
import time
from pathlib import Path

from benchmarks._report import latency_stats, save_report
from core.agent import TaskAgent
from core.nlu.examples import ExampleStore
from core.nlu.retriever import CapabilityRetriever
from core.response_formatter import format_skill_result
from core.skill_registry import SkillRegistry

TASKS = [
    "dimmi che ore sono e che giorno della settimana e' oggi",
    "crea la cartella {tmp}/jake_bench_prova e poi dimmi se esiste davvero",
    "crea il file {tmp}/jake_bench_nota.txt e poi leggilo",
]


def run(tasks: list[str] = None) -> dict:
    registry = SkillRegistry()
    example_store = ExampleStore()
    retriever = CapabilityRetriever(registry, example_store)
    retriever.refresh()
    model = registry.config.get("ollama_model", "qwen2.5:7b")
    agent = TaskAgent(
        registry, retriever, registry.ollama_client, model_provider=lambda: model,
        format_result=lambda intent, result: format_skill_result(intent, result, registry),
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="jake_bench_agent_"))
    try:
        requests = [task.format(tmp=str(tmp_dir).replace("\\", "/")) for task in (tasks or TASKS)]
        rows = []
        latencies_ms = []
        for request in requests:
            started = time.perf_counter()
            outcome = agent.run(request)
            latency_ms = (time.perf_counter() - started) * 1000
            latencies_ms.append(latency_ms)
            completed = bool(outcome.final_answer) and outcome.error is None
            rows.append({
                "request": request, "steps": len(outcome.steps), "completed": completed,
                "error": outcome.error, "asked_question": outcome.question,
                "final_answer": outcome.final_answer, "latency_ms": round(latency_ms, 1),
            })
        report = {
            "tasks": len(rows), "completed": sum(1 for row in rows if row["completed"]),
            "latency": latency_stats(latencies_ms), "runs": rows,
        }
        return report
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    print(f"Benchmark agente: {len(TASKS)} compiti, richiede Ollama in esecuzione...")
    report = run()

    print(f"\nCompletati: {report['completed']}/{report['tasks']}")
    print(f"Latenza: p50={report['latency'].get('p50_ms')} ms  p95={report['latency'].get('p95_ms')} ms")
    for row in report["runs"]:
        status = "OK" if row["completed"] else f"NON COMPLETATO (error={row['error']}, ask_user={row['asked_question']!r})"
        print(f"  [{row['steps']} passi, {row['latency_ms']} ms] {status}: {row['request']}")
        if row["final_answer"]:
            print(f"    -> {row['final_answer']}")

    path = save_report("agent", report)
    print(f"\nReport salvato in {path}")


if __name__ == "__main__":
    main()
