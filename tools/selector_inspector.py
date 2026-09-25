"""Inspector del selettore (F3.3.6) da riga di comando: cosa sceglierebbe Jake in una finestra e perche'.

    python -m tools.selector_inspector --window "Jake Computer Use Fixture" --name Aggiungi
    python -m tools.selector_inspector --window "Calcolatrice" --control-type Button --json

Legge SOLO la finestra indicata (UI Automation, nessun click, nessuna digitazione) e stampa candidato
scelto, alternative, punteggio e motivo. Utile per capire un NOT_FOUND/AMBIGUOUS_MATCH o per scegliere
un criterio in piu' (automation id) prima di salvare una procedura."""
import argparse
import json
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inspector del selettore UI Automation")
    parser.add_argument("--window", required=True, help="parte del titolo della finestra")
    parser.add_argument("--name")
    parser.add_argument("--control-type")
    parser.add_argument("--automation-id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not (args.name or args.control_type or args.automation_id):
        parser.error("serve almeno un criterio: --name, --control-type o --automation-id")

    from core.win_dpi import ensure_dpi_aware

    ensure_dpi_aware()
    from core.computer_agent import ComputerAgent
    from core.computer_use.inspector import inspect
    from core.computer_use.selector import ElementSelector
    from core.computer_use.ui_automation_adapter import UIAutomationAdapter, WindowNotFoundError

    adapter = UIAutomationAdapter()
    try:
        window = adapter.find_window_by_title_containing(args.window, timeout_seconds=5.0)
    except WindowNotFoundError as exc:
        print(f"Finestra non trovata: {exc}", file=sys.stderr)
        return 2
    selector = ElementSelector(name=args.name, control_type=args.control_type, automation_id=args.automation_id)
    report = inspect(ComputerAgent().observe_window(window, adapter), selector)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0 if report.verdict == "unique" else 1
    print(f"Esito: {report.verdict} - {report.reason}")
    if report.chosen:
        c = report.chosen
        print(f"Scelto: {c.name!r} [{c.control_type}] id={c.automation_id!r} punteggio={c.score} ({c.reason})")
    for c in report.alternatives:
        print(f"  alternativa: {c.name!r} [{c.control_type}] id={c.automation_id!r} punteggio={c.score} ({c.reason})")
    return 0 if report.verdict == "unique" else 1


if __name__ == "__main__":
    raise SystemExit(main())
