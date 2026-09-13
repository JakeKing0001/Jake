"""Archivio in sola lettura per il ledger delle azioni (F1.7.3, vedi la fase F1 in
ROADMAP_EXECUTION.md: "definire retention diversa per log operativo, audit di sicurezza e
memoria").

data/jake_ledger.jsonl e' un registro di audit APPEND-ONLY (F1.7.1: resistente a crash/
troncamenti) che oggi cresce senza limite per tutta la vita dell'installazione - a differenza del
log operativo (data/jake_actions.jsonl/jake.log, gia' limitato da RotatingFileHandler a 2MB x 4
file, verificato leggendo core/logger.py) e della memoria (core/memory_manager.py: scadenza
esplicita per-ricordo con purge_expired() gia' automatico via core/system_advisor.py, mentre la
cronologia di conversazione si cancella SOLO su richiesta esplicita dell'utente -
PURGE_OLD_HISTORY, skills/privacy.py - mai automaticamente: "cancellare dati dell'utente senza
che li abbia chiesti sarebbe un danno silenzioso", vedi il docstring di
MemoryManager.purge_history_older_than()). Un registro di AUDIT DI SICUREZZA merita ALMENO la
stessa cautela: rimuovere voci vecchie dal file live rischierebbe di cancellare proprio la prova
che un domani servisse (un accesso, un'azione DESTRUCTIVE/ADMIN eseguita mesi fa).

Questo strumento NON tocca MAI il ledger dal vivo: legge le voci piu' vecchie di una soglia e le
copia in un file di archivio separato, lasciando l'originale intatto - sicuro da eseguire anche
con Jake in esecuzione (sola lettura sul ledger, nessuna scrittura li'; una voce nuova aggiunta
mentre questo strumento gira finisce semplicemente FUORI da questa esecuzione, mai persa ne'
corrotta). Se/quando rimuovere per davvero le voci gia' archiviate dal file originale resta una
decisione dell'utente, non di questo strumento - stessa filosofia "mai automatica, sempre
esplicita" gia' applicata alla cronologia di conversazione.

Uso:
    python -m tools.archive_ledger                        # archivia le voci piu' vecchie di 180 giorni
    python -m tools.archive_ledger --older-than-days 90
    python -m tools.archive_ledger --path altro.jsonl --output archivio.jsonl"""
import argparse
import json
import time
from pathlib import Path

DEFAULT_LEDGER_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_ledger.jsonl"
DEFAULT_RETENTION_DAYS = 180


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


def split_by_age(records: list[dict], older_than_days: float, now: float | None = None) -> tuple[list[dict], list[dict]]:
    """Restituisce (vecchie, recenti). Una voce senza `ts` valido (numero) resta prudentemente
    tra le RECENTI - nega per default anche qui, coerente con lo stile del resto del progetto: un
    record che non si riesce a datare non viene mai archiviato per errore."""
    cutoff = (now if now is not None else time.time()) - older_than_days * 86400
    old, recent = [], []
    for record in records:
        ts = record.get("ts")
        if isinstance(ts, (int, float)) and ts < cutoff:
            old.append(record)
        else:
            recent.append(record)
    return old, recent


def default_archive_path(source: Path, older_than_days: float, now: float | None = None) -> Path:
    cutoff_date = time.strftime(
        "%Y-%m-%d", time.gmtime((now if now is not None else time.time()) - older_than_days * 86400)
    )
    return source.parent / f"{source.stem}_archive_before_{cutoff_date}{source.suffix}"


def archive(path: Path, older_than_days: float, output: Path | None = None) -> dict:
    """Il cuore dello strumento, separato da main() per essere testabile con file temporanei
    reali senza dover passare da argparse/sys.argv. Non modifica MAI `path` (vedi il docstring del
    modulo): scrive solo `output` (creato SOLO se c'e' almeno una voce da archiviare - nessun
    file vuoto quando non c'e' nulla di vecchio abbastanza)."""
    records = _load_jsonl(path)
    old, recent = split_by_age(records, older_than_days)
    output = output or default_archive_path(path, older_than_days)

    if old:
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as handle:
            for record in old:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {"total": len(records), "archived": len(old), "remaining": len(recent), "output": output if old else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", type=Path, default=DEFAULT_LEDGER_PATH)
    parser.add_argument("--older-than-days", type=float, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--output", type=Path, default=None, help="default: <path>_archive_before_<data>.jsonl")
    args = parser.parse_args()

    result = archive(args.path, args.older_than_days, args.output)

    if result["archived"] == 0:
        print(f"Nessuna voce piu' vecchia di {args.older_than_days:.0f} giorni in {args.path}: nulla da archiviare.")
        return

    print(
        f"Archiviate {result['archived']} voci (su {result['total']} totali) piu' vecchie di "
        f"{args.older_than_days:.0f} giorni in {result['output']}. Il ledger originale ({args.path}) NON e' stato "
        f"modificato - {result['remaining']} voci recenti restano li'."
    )


if __name__ == "__main__":
    main()
