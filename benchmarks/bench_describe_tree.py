"""Benchmark ripetibile per `UIAutomationAdapter.describe_tree` (F3.2.7, "valutare COM diretto vs
helper C++ con benchmark, mantenendo l'adapter stabile"). Le prime due misure di questo criterio
(Calcolatrice ~74 elementi, Paint ~124 elementi - vedi ROADMAP_EXECUTION.md sezione F3.2) erano
misurazioni manuali usa-e-getta, non un benchmark ripetibile - questo modulo le sostituisce con
qualcosa di rilanciabile, e aggiunge il caso esplicitamente dichiarato come "potrebbe cambiare la
conclusione" in quell'incremento: un albero MOLTO piu' grande (centinaia di elementi DOM), non
solo app desktop con poche decine/centinaia di controlli nativi.

`browser_fixture_large.html` (300 righe di tabella, ~1500 elementi DOM reali con bottoni per
riga) e' una fixture LOCALE deterministica (nessuna dipendenza di rete, nessun sito reale) - lo
stesso principio gia' seguito per `browser_fixture.html`/`browser_fixture_page2.html` (F3.6.1),
aperta tramite lo stesso `browser_adapter.launch_isolated_browser` gia' verificato sicuro
(isolamento del profilo Edge) in quell'incremento, nessuna nuova infrastruttura.

Lanciare a mano con `python -m benchmarks.bench_describe_tree` (richiede Microsoft Edge
installato - salta la parte browser con un avviso onesto se non disponibile, non fa fallire
l'intero benchmark)."""
import argparse
import time
from pathlib import Path

from benchmarks._report import latency_stats, save_report
from core.computer_use.ui_automation_adapter import UIAutomationAdapter

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _bench_qt_fixture(iterations: int) -> dict | None:
    import subprocess
    import sys

    adapter = UIAutomationAdapter()
    process = subprocess.Popen(
        [sys.executable, "-m", "benchmarks.computer_use_fixture", "--auto-close-after", "60"],
        cwd=str(_REPO_ROOT),
    )
    try:
        window = adapter.find_window_by_title("Jake Computer Use Fixture", timeout_seconds=15.0)
        latencies_ms = []
        element_count = None
        for _ in range(iterations):
            started = time.perf_counter()
            tree = adapter.describe_tree(window, max_depth=15)
            latencies_ms.append((time.perf_counter() - started) * 1000)
            if element_count is None:
                element_count = _count_elements(tree)
        return {"element_count": element_count, "latency": latency_stats(latencies_ms)}
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def _bench_large_browser_dom(iterations: int) -> dict | None:
    from core.computer_use.browser_adapter import BrowserNotFoundError, find_page_document, launch_isolated_browser, find_isolated_browser_window

    fixture_url = (_REPO_ROOT / "benchmarks" / "browser_fixture_large.html").as_uri()
    adapter = UIAutomationAdapter()
    try:
        browser = launch_isolated_browser(fixture_url)
    except BrowserNotFoundError:
        return None
    try:
        window = find_isolated_browser_window(adapter, browser, timeout_seconds=25.0)
        document = find_page_document(adapter, window, timeout_seconds=15.0)
        time.sleep(1.0)  # tempo di rendering completo della tabella, non un'attesa a caso: verificato empiricamente necessario per un conteggio elementi stabile

        latencies_ms = []
        element_count = None
        for _ in range(iterations):
            started = time.perf_counter()
            tree = adapter.describe_tree(document, max_depth=25)
            latencies_ms.append((time.perf_counter() - started) * 1000)
            if element_count is None:
                element_count = _count_elements(tree)
        return {"element_count": element_count, "latency": latency_stats(latencies_ms)}
    finally:
        browser.terminate_and_cleanup()


def _count_elements(tree) -> int:
    if tree is None:
        return 0
    count = 1
    for child in tree.children:
        count += _count_elements(child)
    return count


def run(iterations: int = 3) -> dict:
    report: dict = {"iterations": iterations}
    report["qt_fixture"] = _bench_qt_fixture(iterations)
    large_dom = _bench_large_browser_dom(iterations)
    report["large_browser_dom"] = large_dom if large_dom is not None else "saltato: Edge non trovato"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()

    print(f"Benchmark describe_tree: {args.iterations} iterazioni per caso...")
    report = run(iterations=args.iterations)

    qt = report["qt_fixture"]
    print(f"\nFixture Qt: {qt['element_count']} elementi, p50={qt['latency']['p50_ms']} ms")

    browser = report["large_browser_dom"]
    if isinstance(browser, dict):
        print(f"DOM browser grande: {browser['element_count']} elementi, p50={browser['latency']['p50_ms']} ms")
        ms_per_element = browser["latency"]["p50_ms"] / browser["element_count"]
        print(f"ms per elemento: {ms_per_element:.3f}")
    else:
        print(f"DOM browser grande: {browser}")

    path = save_report("describe_tree", report)
    print(f"\nReport salvato in {path}")


if __name__ == "__main__":
    main()
