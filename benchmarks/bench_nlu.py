"""Benchmark ripetibile per l'NLU (F0, vedi la fase F0 in ROADMAP.md): accuratezza e latenza del
classificatore di intent (core/router.py) su un campione di training/intents.jsonl.

Richiede Ollama in esecuzione con i modelli configurati (vedi README): non fa parte della suite
`tests/`, che il progetto tiene deliberatamente senza dipendenze da Ollama/microfono. Lanciare a
mano con: `python -m benchmarks.bench_nlu` (opzionale `--sample 80 --seed 42`).

Leave-one-out sulla corsia veloce: per ogni frase campionata, la voce esatta corrispondente
viene tolta temporaneamente dall'indice a corrispondenza esatta di ExampleStore prima di
classificarla, e ripristinata subito dopo. Senza questo accorgimento il benchmark misurerebbe
solo la memoria letterale della corsia veloce (quasi sempre 100%, perche' training/intents.jsonl
E' la sorgente di quella corsia - vedi core/nlu/examples.py), non la capacita' del classificatore
di riconoscere una frase come se non l'avesse gia' vista testualmente identica. Il recupero
semantico few-shot (che PUO' ancora vedere frasi simili tra gli esempi) resta invece attivo
di proposito: e' cosi' che il classificatore funziona davvero in produzione.

Limite noto: misura solo Router.detect_intent, non l'intera pipeline di JakeCore._process().
Frasi come "ripeti", "zitto", "aiuto" o "no intendevo X" in produzione non arrivano MAI al
classificatore (le intercetta prima _match_meta_command/example_store esatto/chitchat, vedi
core/jake_core.py): qui compaiono come "misclassificate" perche' il benchmark le manda dritte
al classificatore isolato, non perche' la pipeline reale le sbagli davvero. Un'esecuzione tipica
(60 frasi, seed 7) misura ~82% di accuratezza col classificatore isolato in queste condizioni
volutamente piu' severe della pipeline completa."""
import argparse
import collections
import random
import time

from benchmarks._report import latency_stats, save_report
from core.nlu.examples import ExampleStore, normalize_key
from core.nlu.retriever import CapabilityRetriever
from core.router import Router
from core.skill_registry import SkillRegistry


def _load_dataset(example_store: ExampleStore) -> list:
    # Solo gli esempi builtin (training/intents.jsonl): quelli "learned" sono locali a questa
    # macchina/utente e non versionati, non fanno parte del dataset di riferimento del progetto.
    return [example for example in example_store.all() if example.source == "builtin"]


def run(sample_size: int = 80, seed: int = 42) -> dict:
    registry = SkillRegistry()
    example_store = ExampleStore()
    retriever = CapabilityRetriever(registry, example_store)
    # refresh() costruisce gli indici (capacita' + esempi): senza, restano vuoti e il
    # classificatore lavora quasi alla cieca - JakeCore.__init__ lo chiama sempre, un benchmark
    # che lo dimentica misurerebbe un Router configurato in modo diverso da quello reale.
    retriever.refresh()
    router = Router(
        skill_registry=registry, example_store=example_store, retriever=retriever, client=registry.ollama_client,
    )

    dataset = _load_dataset(example_store)
    rng = random.Random(seed)
    sample = rng.sample(dataset, min(sample_size, len(dataset)))

    rows = []
    latencies_ms = []
    route_counts = collections.Counter()
    correct = 0
    errors = 0
    for example in sample:
        key = normalize_key(example.text)
        removed = example_store._by_key.pop(key, None)
        try:
            started = time.perf_counter()
            command = router.detect_intent(example.text)
            latency_ms = (time.perf_counter() - started) * 1000
        except Exception as exc:
            errors += 1
            print(f"  ERRORE su '{example.text}': {type(exc).__name__}: {exc}")
            continue
        finally:
            if removed is not None:
                example_store._by_key[key] = removed

        is_correct = command.intent == example.intent
        correct += is_correct
        latencies_ms.append(latency_ms)
        route_counts[router.last_route] += 1
        rows.append({
            "text": example.text, "expected": example.intent, "got": command.intent,
            "correct": is_correct, "route": router.last_route, "latency_ms": round(latency_ms, 1),
        })

    n = len(rows)
    report = {
        "sample_size": len(sample), "evaluated": n, "errors": errors,
        "accuracy": round(correct / n, 3) if n else None,
        "latency": latency_stats(latencies_ms),
        "routes": dict(route_counts),
        "misclassified": [row for row in rows if not row["correct"]],
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=80, help="quante frasi campionare da training/intents.jsonl")
    parser.add_argument("--seed", type=int, default=42, help="seed per un campione riproducibile")
    args = parser.parse_args()

    print(f"Benchmark NLU: campione di {args.sample} frasi (seed {args.seed}), richiede Ollama in esecuzione...")
    report = run(sample_size=args.sample, seed=args.seed)

    print(f"\nAccuratezza (leave-one-out sulla corsia esatta): {report['accuracy']:.1%} su {report['evaluated']} frasi ({report['errors']} errori)")
    print(f"Latenza: p50={report['latency'].get('p50_ms')} ms  p95={report['latency'].get('p95_ms')} ms")
    print(f"Instradamento: {report['routes']}")
    if report["misclassified"]:
        print(f"\n{len(report['misclassified'])} frasi classificate male:")
        for row in report["misclassified"][:15]:
            print(f"  '{row['text']}' -> atteso {row['expected']}, ottenuto {row['got']} (via {row['route']})")

    path = save_report("nlu", report)
    print(f"\nReport salvato in {path}")


if __name__ == "__main__":
    main()
