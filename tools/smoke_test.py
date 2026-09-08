"""Smoke test installazione/avvio/arresto (F0, criterio di uscita in ROADMAP.md). Avvia
`main.py --cli` come processo vero (non importa JakeCore in-process: deve passare dallo stesso
avvio che usa un utente reale, incluso caricare plugin/skill/config da disco), gli manda un
comando innocuo via stdin, controlla che risponda, poi lo chiude con "esci" e verifica che il
processo termini pulito (exit code 0) entro un timeout - non solo che non crashi subito.

Uso: python -m tools.smoke_test  (richiede Ollama in esecuzione, come il resto dell'avvio normale)"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_SECONDS = 60


def run() -> tuple[bool, str]:
    # sys.executable, non un percorso .venv fisso: questo stesso script gira sia in sviluppo
    # locale (.venv\Scripts\python.exe) sia in CI (Python di sistema installato da actions/
    # setup-python, senza .venv - vedi .github/workflows/ci.yml).
    proc = subprocess.Popen(
        [sys.executable, "main.py", "--cli"],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    started = time.monotonic()
    try:
        stdout, _ = proc.communicate(input="che ore sono\nesci\n", timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, _ = proc.communicate()
        return False, f"TIMEOUT dopo {TIMEOUT_SECONDS}s (non ha risposto/chiuso in tempo). Output raccolto:\n{stdout}"

    elapsed = time.monotonic() - started
    return evaluate(stdout, proc.returncode, elapsed)


def evaluate(stdout: str, returncode: int, elapsed: float) -> tuple[bool, str]:
    """Separata da run() per poter essere testata (tests/test_smoke_test.py) senza spawnare
    davvero un processo Python a ogni test. "Jake > " deve comparire almeno due volte: una per
    la risposta vera al comando, una per "Chiusura..." (vedi main.run_cli_mode()) - una sola
    occorrenza vorrebbe dire che il comando non ha avuto risposta prima della chiusura."""
    checks = {
        "si e' avviato (banner)": "avviato" in stdout.lower(),
        "ha risposto al comando": stdout.lower().count("jake >") >= 2,
        "si e' chiuso su 'esci'": "chiusura" in stdout.lower(),
        "exit code 0": returncode == 0,
    }
    failed = [name for name, ok in checks.items() if not ok]
    report = f"Avvio->risposta->chiusura in {elapsed:.1f}s. exit code: {returncode}\n\n" + "\n".join(
        f"  [{'OK' if ok else 'FALLITO'}] {name}" for name, ok in checks.items()
    )
    if failed:
        report += f"\n\nOutput completo:\n{stdout}"
    return not failed, report


def main():
    print("Smoke test: avvio main.py --cli, un comando, chiusura...")
    ok, report = run()
    print(report)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
