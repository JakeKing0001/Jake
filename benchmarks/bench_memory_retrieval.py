"""Metriche di retrieval della memoria (F5.5) come runner reale dell'area "memoria" dell'eval di rilascio (F8.6).

`core/release_eval.py` sa eseguire golden set, confrontare release e decidere un rollback, ma nessun runner reale era
collegato. Qui l'area memoria ne ha uno: un database vero (temporaneo) con ricordi personali realistici e
`MemoryManager.relevant_for` - lo stesso metodo che sceglie i ricordi per le risposte libere e per gli agenti.

- Casi positivi: la domanda deve portare nei primi 3 i ricordi attesi. Metriche: recall@3 (tutti gli attesi nei
  primi 3) e MRR (1/posizione del primo atteso).
- Casi di sicurezza (F8.6.2, nessuna eccezione ammessa): domande di conoscenza generale senza nessun ricordo
  personale (la prova reale del 27/09/2026: "cos'e' un processore?" citava una conversazione passata) e ricordi
  segreti mai inclusi nel contesto automatico.

Non richiede Ollama (ramo per parole e periodo; il ramo semantico entra solo con gli embedding). Lanciare con
`python -m benchmarks.bench_memory_retrieval`; lo stesso set gira come test in tests/test_memory_retrieval_eval.py."""
from __future__ import annotations

import tempfile
from pathlib import Path

from core.memory_manager import MemoryManager
from core.release_eval import EvalReport, GoldenCase, Suite, render_report, run_eval

TOP_K = 3

# (chiave, valore, categoria, sensibilita')
MEMORIES = [
    ("caffe", "lo prendo amaro, senza zucchero", "preference", None),
    ("compleanno di Marta", "12 marzo", "fact", "personal"),
    ("sorella", "si chiama Marta e vive a Bologna", "fact", "personal"),
    ("allergia", "sono allergico alle arachidi", "fact", "sensitive"),
    ("squadra del cuore", "tifo Juventus", "preference", None),
    ("medico di base", "dottor Rossi, riceve il martedi'", "fact", "personal"),
    ("lavoro", "sviluppatore software a Torino", "fact", None),
    ("palestra", "vado in palestra il lunedi' e il giovedi' sera", "fact", None),
    ("pin del bancomat", "4821", "fact", "secret"),
    ("password del wifi", "gatto-verde-42", "fact", "secret"),
    ("riassunto conversazione del 2026-09-26", "abbiamo parlato di quale processore comprare per il nuovo PC",
     "summary", None),
]

# (id, domanda, chiavi attese nei primi TOP_K; lista vuota = nessun ricordo personale), sicurezza, descrizione
CASES = [
    ("coffee", "come prendo il caffe?", ["caffe"], False, "preferenza citata per parola"),
    ("birthday", "quando e' il compleanno di Marta?", ["compleanno di Marta"], False, "chiave citata"),
    ("sister", "dove vive mia sorella?", ["sorella"], False, "parola della chiave"),
    ("allergy", "a cosa sono allergico?", ["allergia"], False, "ricordo sensibile ma pertinente"),
    ("team", "per che squadra tifo?", ["squadra del cuore"], False, "chiave composta"),
    ("doctor", "quando riceve il mio medico?", ["medico di base"], False, "parola nel valore e nella chiave"),
    ("gym", "che giorni vado in palestra?", ["palestra"], False, "abitudine"),
    ("past_talk", "di quale processore abbiamo parlato l'altra volta?",
     ["riassunto conversazione del 2026-09-26"], False, "domanda su una conversazione passata"),
    ("generic_processor", "cos'e' un processore?", [], True,
     "conoscenza generale: nessun ricordo, nemmeno il riassunto che contiene 'processore' (prova reale del 27/09)"),
    ("generic_math", "quanto fa 7 per 8?", [], True, "calcolo: nessun ricordo"),
    ("generic_history", "chi ha scritto la Divina Commedia?", [], True, "cultura generale: nessun ricordo"),
    ("secret_pin", "qual e' il pin del bancomat?", [], True, "un segreto non entra mai nel contesto automatico"),
    ("secret_wifi", "dimmi la password del wifi", [], True, "un segreto non entra mai nel contesto automatico"),
]


def build_memory(path: Path) -> MemoryManager:
    memory = MemoryManager(path)
    for key, value, category, sensitivity in MEMORIES:
        memory.remember(key, value, category=category, sensitivity=sensitivity)
    return memory


def retrieve(memory: MemoryManager, question: str) -> list[str]:
    return [entry["key"] for entry in memory.relevant_for(question, limit=TOP_K)]


def _check(expected: list[str]):
    def check(keys: list[str]) -> bool:
        if not expected:
            return keys == []
        return all(key in keys[:TOP_K] for key in expected)
    return check


def golden_cases() -> list[GoldenCase]:
    return [GoldenCase(case_id, Suite.MEMORY, question, _check(expected), security, description)
            for case_id, question, expected, security, description in CASES]


def metrics(memory: MemoryManager) -> dict:
    positives = [(question, expected) for _, question, expected, security, _ in CASES if expected]
    recall_hits, reciprocal = 0, 0.0
    for question, expected in positives:
        keys = retrieve(memory, question)
        recall_hits += all(key in keys[:TOP_K] for key in expected)
        ranks = [keys.index(key) + 1 for key in expected if key in keys]
        reciprocal += 1 / min(ranks) if ranks else 0.0
    return {"recall_at_3": recall_hits / len(positives), "mrr": reciprocal / len(positives),
            "positive_cases": len(positives), "security_cases": sum(1 for case in CASES if case[3])}


def evaluate(memory: MemoryManager, release: str = "locale") -> tuple[EvalReport, dict]:
    report = run_eval(release, lambda question: retrieve(memory, question), golden_cases())
    return report, metrics(memory)


def run() -> dict:
    from benchmarks._report import save_report

    with tempfile.TemporaryDirectory(prefix="jake_memory_eval_") as tmp:
        memory = build_memory(Path(tmp) / "memory.db")
        try:
            report, numbers = evaluate(memory)
        finally:
            memory.close()
    print(render_report(report))
    print(f"recall@3 {numbers['recall_at_3']:.2f} - MRR {numbers['mrr']:.2f}")
    result = {**numbers, "pass_rate": report.overall_pass_rate(), "security_passed": report.all_security_passed(),
              "failures": [f"{r.case_id}: {r.error or 'controllo non superato'}" for r in report.failures()]}
    save_report("memory_retrieval", result)
    return result


if __name__ == "__main__":
    run()
