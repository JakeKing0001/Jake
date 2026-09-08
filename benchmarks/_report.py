"""Utilita' condivisa dai benchmark (F0: "benchmark ripetibili"): stesso formato di report JSON
per tutti, cosi' due esecuzioni in momenti diversi si possono confrontare senza dover leggere
codice diverso per ciascun benchmark. Non e' un test: nessun benchmark qui dentro fa parte della
suite `tests/` (che il progetto tiene deliberatamente senza Ollama/microfono, vedi README) - va
lanciato a mano, quando serve misurare qualcosa, non a ogni commit."""
import json
import platform
import statistics
import time
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def percentile(values: list[float], p: float) -> float:
    """p in [0, 100]. Implementazione semplice (nearest-rank su una lista ordinata): sufficiente
    per campioni di poche decine di misure, non serve un'interpolazione piu' precisa qui."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((p / 100) * (len(ordered) - 1))))
    return ordered[index]


def latency_stats(latencies_ms: list[float]) -> dict:
    if not latencies_ms:
        return {"count": 0}
    return {
        "count": len(latencies_ms),
        "mean_ms": round(statistics.fmean(latencies_ms), 1),
        "p50_ms": round(percentile(latencies_ms, 50), 1),
        "p95_ms": round(percentile(latencies_ms, 95), 1),
        "min_ms": round(min(latencies_ms), 1),
        "max_ms": round(max(latencies_ms), 1),
    }


def save_report(name: str, report: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "benchmark": name,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "platform": platform.platform(),
        **report,
    }
    path = RESULTS_DIR / f"{name}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
