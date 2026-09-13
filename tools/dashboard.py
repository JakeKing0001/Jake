"""Dashboard locale di errori e latenze (F0, criterio di uscita in ROADMAP.md: "dashboard locale
con errori e latenze"). Legge data/jake_actions.jsonl (il log strutturato di core/logger.
log_action - trace_id, durata, modello, skill, decisione di rischio, risultato, verifica) e
data/jake_sessions.jsonl (i soli fallimenti, con parametri - vedi core/session_recorder.py) e
scrive un report HTML autonomo, senza dipendenze esterne ne' CDN (Jake e' "local-first e
offline-capable" per principio, vedi ROADMAP.md - una dashboard che smette di funzionare senza
internet contraddirebbe esattamente quel principio).

Non e' un servizio: genera un file statico e lo apre nel browser, una tantum, quando lo lanci tu.

Uso:
    python -m tools.dashboard                 # legge data/jake_actions.jsonl, apre il report
    python -m tools.dashboard --no-open        # scrive il file senza aprirlo
    python -m tools.dashboard --path altro.jsonl"""
import argparse
import html
import json
import webbrowser
from collections import Counter, defaultdict
from pathlib import Path

from core.action_ledger import error_category_of

DEFAULT_ACTIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_actions.jsonl"
DEFAULT_SESSIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_sessions.jsonl"
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "dashboard.html"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((p / 100) * (len(ordered) - 1))))
    return ordered[index]


def _is_success(result) -> bool:
    # F1.7.6 ("rollback rate"): "rollback_success" e' un successo quanto "success" - la skill
    # compensatoria ha eseguito correttamente - solo su un intent diverso da quello richiesto
    # dall'utente. Trattarlo come un fallimento qui gonfierebbe "fallimenti"/error rate per skill
    # con un rollback riuscito, che e' esattamente l'esito CORRETTO di un errore altrove, non un
    # errore suo. "rollback_failed" resta invece un vero fallimento (il generico != "success" lo
    # gestisce gia' correttamente, nessuna eccezione necessaria per quello).
    return result == "success" or result == "rollback_success"


def build_report(actions: list[dict], sessions: list[dict]) -> dict:
    total = len(actions)
    private_count = sum(1 for a in actions if a.get("private"))
    visible = [a for a in actions if not a.get("private")]

    outcomes = Counter(a.get("result", "?") for a in visible)
    successes = sum(count for outcome, count in outcomes.items() if _is_success(outcome))
    failures = total - private_count - successes

    by_skill = defaultdict(lambda: {"count": 0, "errors": 0, "latencies": []})
    for a in visible:
        skill = a.get("skill", "?")
        entry = by_skill[skill]
        entry["count"] += 1
        if not _is_success(a.get("result")):
            entry["errors"] += 1
        if a.get("duration_ms") is not None:
            entry["latencies"].append(a["duration_ms"])

    skill_rows = []
    for skill, entry in sorted(by_skill.items(), key=lambda kv: -kv[1]["count"]):
        latencies = entry["latencies"]
        skill_rows.append({
            "skill": skill, "count": entry["count"], "errors": entry["errors"],
            "error_rate": entry["errors"] / entry["count"] if entry["count"] else 0,
            "p50_ms": round(_percentile(latencies, 50), 1) if latencies else None,
            "p95_ms": round(_percentile(latencies, 95), 1) if latencies else None,
        })

    all_latencies = [a["duration_ms"] for a in visible if a.get("duration_ms") is not None]
    risk_counts = Counter(a.get("risk_decision", "?") for a in visible)
    verified_counts = Counter(
        "verificato" if a.get("verified") is True else "verifica fallita" if a.get("verified") is False else "non verificato"
        for a in visible
    )
    # F1.7.6 ("mostrare... failure taxonomy"): stessa error_category_of() gia' usata da
    # core/action_ledger.py per l'ActionReceipt (F1.1.4) - la dashboard legge invece
    # data/jake_actions.jsonl (core/logger.log_action, F0: log di debug rotante, sistema diverso
    # dal ledger append-only, vedi il docstring di action_ledger.py), ma il campo `result` e'
    # scritto negli stessi due formati in entrambi i log (letto dal codice: log_action riceve lo
    # stesso `result` gia' calcolato dai quattro chokepoint prima di scrivere sia qui sia nel
    # ledger), quindi la stessa funzione pura si applica senza modifiche.
    failure_category_counts = Counter(error_category_of(a.get("result", "")) for a in visible)

    # F1.7.6 ("mostrare... rollback rate"): l'altra meta' di questa voce, chiusa ora che
    # core/execution_safety.py::rollback_effect() (F1.7.2) scrive un evento distinto - `result`
    # inizia sempre con "rollback_" ("rollback_success"/"rollback_failed"), mai il generico
    # "success" di un'azione qualsiasi con lo stesso intent, quindi contabile senza ambiguita'.
    # Percentuale sul totale delle azioni VISIBILI (non sui soli rollback riusciti, ne' sul totale
    # incluse quelle private): "quale frazione di tutto cio' che e' successo era un tentativo di
    # annullare qualcos'altro", la stessa domanda che p50/p95/verification rate fanno per le loro
    # rispettive dimensioni.
    rollback_events = [a for a in visible if str(a.get("result", "")).startswith("rollback_")]
    rollback_successes = sum(1 for a in rollback_events if a.get("result") == "rollback_success")

    recent_failures = [a for a in visible if not _is_success(a.get("result"))][-30:][::-1]

    return {
        "total": total, "private_count": private_count, "successes": successes, "failures": failures,
        "outcomes": outcomes, "skill_rows": skill_rows,
        "overall_p50_ms": round(_percentile(all_latencies, 50), 1) if all_latencies else None,
        "overall_p95_ms": round(_percentile(all_latencies, 95), 1) if all_latencies else None,
        "risk_counts": risk_counts, "verified_counts": verified_counts,
        "failure_category_counts": failure_category_counts,
        "rollback_count": len(rollback_events), "rollback_successes": rollback_successes,
        "rollback_rate": len(rollback_events) / len(visible) if visible else 0,
        "recent_failures": recent_failures, "recorded_sessions": len(sessions),
    }


def _bar(value: int, max_value: int, width: int = 20) -> str:
    filled = round((value / max_value) * width) if max_value else 0
    return "█" * filled + "░" * (width - filled)


def render_html(report: dict, actions_path: Path, sessions_path: Path) -> str:
    esc = html.escape
    skill_rows_html = "\n".join(
        f"<tr><td>{esc(row['skill'])}</td><td>{row['count']}</td>"
        f"<td class='{'err' if row['errors'] else ''}'>{row['errors']} ({row['error_rate']:.0%})</td>"
        f"<td>{row['p50_ms'] if row['p50_ms'] is not None else '-'}</td>"
        f"<td>{row['p95_ms'] if row['p95_ms'] is not None else '-'}</td></tr>"
        for row in report["skill_rows"]
    )
    max_outcome = max(report["outcomes"].values(), default=1)
    outcomes_html = "\n".join(
        f"<tr><td>{esc(outcome)}</td><td>{count}</td>"
        f"<td class='bar'>{_bar(count, max_outcome)}</td></tr>"
        for outcome, count in report["outcomes"].most_common()
    )
    risk_html = "\n".join(
        f"<tr><td>{esc(risk)}</td><td>{count}</td></tr>" for risk, count in report["risk_counts"].most_common()
    )
    verified_html = "\n".join(
        f"<tr><td>{esc(state)}</td><td>{count}</td></tr>" for state, count in report["verified_counts"].most_common()
    )
    failure_category_html = "\n".join(
        f"<tr><td>{esc(category)}</td><td>{count}</td></tr>"
        for category, count in report["failure_category_counts"].most_common()
    )
    failures_html = "\n".join(
        f"<tr><td>{esc(f.get('trace_id', ''))}</td><td>{esc(f.get('skill', ''))}</td>"
        f"<td class='err'>{esc(f.get('result', ''))}</td><td>{esc(f.get('risk_decision', ''))}</td></tr>"
        for f in report["recent_failures"]
    ) or "<tr><td colspan='4'><em>nessun fallimento registrato</em></td></tr>"

    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<title>Jake - dashboard locale</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #0f1115; color: #e6e6e6; margin: 0; padding: 24px; }}
  h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.05rem; margin-top: 2rem; color: #9fb4ff; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 16px 0; }}
  .stat {{ background: #1a1d24; border-radius: 8px; padding: 14px; }}
  .stat .n {{ font-size: 1.6rem; font-weight: 600; }} .stat .l {{ font-size: 0.8rem; color: #9aa0aa; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 8px; }}
  th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid #262a33; font-size: 0.9rem; }}
  th {{ color: #9aa0aa; font-weight: 500; }}
  .err {{ color: #ff8080; }} .bar {{ font-family: monospace; letter-spacing: -2px; color: #6fa8ff; }}
  .note {{ color: #9aa0aa; font-size: 0.85rem; }}
</style></head>
<body>
<h1>Jake - dashboard locale (F0)</h1>
<p class="note">Generato da {esc(str(actions_path))} - nessun dato lascia questa macchina, nessuna dipendenza esterna.</p>
<div class="grid">
  <div class="stat"><div class="n">{report['total']}</div><div class="l">azioni registrate</div></div>
  <div class="stat"><div class="n">{report['successes']}</div><div class="l">successi</div></div>
  <div class="stat"><div class="n">{report['failures']}</div><div class="l">fallimenti</div></div>
  <div class="stat"><div class="n">{report['private_count']}</div><div class="l">in modalita' privata (contenuto non registrato)</div></div>
  <div class="stat"><div class="n">{report['overall_p50_ms'] if report['overall_p50_ms'] is not None else '-'} ms</div><div class="l">latenza p50</div></div>
  <div class="stat"><div class="n">{report['overall_p95_ms'] if report['overall_p95_ms'] is not None else '-'} ms</div><div class="l">latenza p95</div></div>
  <div class="stat"><div class="n">{report['recorded_sessions']}</div><div class="l">sessioni fallite registrate ({esc(str(sessions_path))})</div></div>
  <div class="stat"><div class="n">{report['rollback_count']}</div><div class="l">rollback (di cui {report['rollback_successes']} riusciti) - {report['rollback_rate']:.1%} delle azioni</div></div>
</div>

<h2>Per skill</h2>
<table><tr><th>Skill</th><th>Azioni</th><th>Errori</th><th>p50</th><th>p95</th></tr>
{skill_rows_html or "<tr><td colspan='5'><em>nessuna azione registrata</em></td></tr>"}
</table>

<h2>Esiti</h2>
<table><tr><th>Risultato</th><th>N</th><th></th></tr>{outcomes_html}</table>

<h2>Decisione di rischio</h2>
<table><tr><th>Livello</th><th>N</th></tr>{risk_html}</table>

<h2>Verifica dell'effetto</h2>
<table><tr><th>Stato</th><th>N</th></tr>{verified_html}</table>

<h2>Categoria di errore (F1.1.4)</h2>
<table><tr><th>Categoria</th><th>N</th></tr>{failure_category_html}</table>

<h2>Ultimi fallimenti</h2>
<table><tr><th>trace_id</th><th>Skill</th><th>Risultato</th><th>Rischio</th></tr>{failures_html}</table>
</body></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", type=Path, default=DEFAULT_ACTIONS_PATH)
    parser.add_argument("--sessions-path", type=Path, default=DEFAULT_SESSIONS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    actions = _load_jsonl(args.path)
    sessions = _load_jsonl(args.sessions_path)
    report = build_report(actions, sessions)
    output_html = render_html(report, args.path, args.sessions_path)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output_html, encoding="utf-8")
    print(f"Dashboard scritta in {args.output} ({report['total']} azioni, {report['failures']} fallimenti).")
    if not args.no_open:
        webbrowser.open(args.output.resolve().as_uri())


if __name__ == "__main__":
    main()
