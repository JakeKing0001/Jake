"""Benchmark ripetibile e non distruttivo per la percezione dello schermo (F0, vedi fase F0 in
ROADMAP.md e fase 3.7 Computer Use Engine): latenza di screenshot, OCR e lettura delle finestre
aperte sullo schermo VERO di questa macchina, in questo momento - nessun click, nessuna
interazione simulata, solo lettura.

Non misura l'accuratezza del click (richiederebbe un dataset di task/UI reali ripetibili, non
ancora costruito - vedi "dataset locale di task reali" nella fase F3 della roadmap): misura solo
il collo di bottiglia di percezione (cattura schermo + OCR), che e' un prerequisito misurabile
oggi senza serve UI di test dedicate. Lanciare a mano con:
`python -m benchmarks.bench_computer_use` (non richiede Ollama: OCR locale via winsdk)."""
import argparse
import time

from benchmarks._report import latency_stats, save_report
from core.vision.screen import capture_screenshot, get_active_window_title, list_open_window_titles, read_screen_text


def run(iterations: int = 5) -> dict:
    screenshot_latencies_ms = []
    ocr_latencies_ms = []
    window_query_latencies_ms = []
    ocr_char_counts = []

    for _ in range(iterations):
        started = time.perf_counter()
        image_path = capture_screenshot()
        screenshot_latencies_ms.append((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        text = read_screen_text(image_path)
        ocr_latencies_ms.append((time.perf_counter() - started) * 1000)
        ocr_char_counts.append(len(text or ""))

        started = time.perf_counter()
        get_active_window_title()
        list_open_window_titles()
        window_query_latencies_ms.append((time.perf_counter() - started) * 1000)

    return {
        "iterations": iterations,
        "screenshot_latency": latency_stats(screenshot_latencies_ms),
        "ocr_latency": latency_stats(ocr_latencies_ms),
        "window_query_latency": latency_stats(window_query_latencies_ms),
        "ocr_chars_extracted": {
            "mean": round(sum(ocr_char_counts) / len(ocr_char_counts), 1) if ocr_char_counts else 0,
            "min": min(ocr_char_counts) if ocr_char_counts else 0,
            "max": max(ocr_char_counts) if ocr_char_counts else 0,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5)
    args = parser.parse_args()

    print(f"Benchmark percezione schermo: {args.iterations} iterazioni su questo schermo, ora...")
    report = run(iterations=args.iterations)

    print(f"\nScreenshot: p50={report['screenshot_latency'].get('p50_ms')} ms")
    print(f"OCR: p50={report['ocr_latency'].get('p50_ms')} ms (~{report['ocr_chars_extracted']['mean']} caratteri estratti in media)")
    print(f"Query finestre (attiva + elenco): p50={report['window_query_latency'].get('p50_ms')} ms")

    path = save_report("computer_use", report)
    print(f"\nReport salvato in {path}")


if __name__ == "__main__":
    main()
