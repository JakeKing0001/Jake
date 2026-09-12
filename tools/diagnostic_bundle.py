"""Bundle diagnostico redatto e ispezionabile (F1.7.7, vedi la fase F1 in ROADMAP_EXECUTION.md:
"esportare un bundle diagnostico redatto e ispezionabile prima della condivisione").

Raccoglie le ultime righe di data/jake_actions.jsonl (F0, core/logger.log_action), data/
jake_ledger.jsonl (F1, core/action_ledger.py) e data/jake_sessions.jsonl (core/
session_recorder.py) insieme a versione di Jake/Python/piattaforma in UN SOLO file JSON locale.

Le prime due fonti non contengono mai parametri veri per costruzione (ActionReceipt e i record
di log_action portano intent/skill/rischio/esito/durata, non i valori passati alla skill - vedi
i rispettivi moduli): nulla da redigere li'. jake_sessions.jsonl invece PUO' contenere parametri
verbatim (modalita' `session_recording_verbatim`, pensata per il debug locale di chi la attiva
sapendo di scrivere dati veri su disco - vedi core/session_recorder.py): un bundle pensato per
essere CONDIVISO non deve mai propagare quella scelta locale a chi lo riceve, quindi ogni record
di sessione viene ri-redatto con la stessa `redact_value()` gia' usata da SessionRecorder, a meno
che l'opt-in esplicito `--include-verbatim-sessions` non venga passato (per chi sta gia'
condividendo il bundle con se stesso, es. da una macchina all'altra, e sa cosa contiene).

Non invia mai nulla da solo: scrive un file locale, da APRIRE E LEGGERE prima di condividerlo con
chiunque - "local-first" e "azioni visibili" restano principi non negoziabili (vedi ROADMAP.md).

Uso:
    python -m tools.diagnostic_bundle                        # scrive data/jake_diagnostic_bundle.json
    python -m tools.diagnostic_bundle --limit 50              # solo le ultime 50 righe per fonte
    python -m tools.diagnostic_bundle --output altro.json
    python -m tools.diagnostic_bundle --include-verbatim-sessions"""
import argparse
import json
import platform
import sys
from pathlib import Path

from core.session_recorder import redact_value
from core.version import PROTOCOL_VERSION, VERSION

DEFAULT_ACTIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_actions.jsonl"
DEFAULT_LEDGER_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_ledger.jsonl"
DEFAULT_SESSIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_sessions.jsonl"
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_diagnostic_bundle.json"
DEFAULT_LIMIT = 200


def _load_jsonl(path: Path, limit: int) -> list[dict]:
    """Le ULTIME `limit` righe valide (un bundle diagnostico serve a capire cosa e' successo di
    recente, non l'intera storia): righe malformate vengono saltate invece di far fallire
    l'intero bundle per una singola riga corrotta."""
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
    return records[-limit:] if limit else records


def _redact_session_record(record: dict) -> dict:
    """Ri-redige `parameters` indipendentemente dal flag `verbatim` gia' scritto nel record: un
    bundle pensato per la condivisione non deve mai propagare la scelta locale di debug verbatim
    a chi lo riceve, salvo opt-in esplicito (vedi build_bundle)."""
    redacted = dict(record)
    if "parameters" in redacted:
        redacted["parameters"] = redact_value(redacted["parameters"])
    return redacted


def build_bundle(
    actions: list[dict], ledger: list[dict], sessions: list[dict], *, include_verbatim_sessions: bool = False,
) -> dict:
    return {
        "jake_version": VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "python_version": sys.version,
        "platform": platform.platform(),
        "actions": actions,
        "ledger": ledger,
        "sessions": sessions if include_verbatim_sessions else [_redact_session_record(r) for r in sessions],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--actions-path", type=Path, default=DEFAULT_ACTIONS_PATH)
    parser.add_argument("--ledger-path", type=Path, default=DEFAULT_LEDGER_PATH)
    parser.add_argument("--sessions-path", type=Path, default=DEFAULT_SESSIONS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="righe piu' recenti per fonte (0 = tutte)")
    parser.add_argument(
        "--include-verbatim-sessions", action="store_true",
        help="non ri-redigere i parametri di sessione gia' in modalita' verbatim (opt-in esplicito)",
    )
    args = parser.parse_args()

    bundle = build_bundle(
        _load_jsonl(args.actions_path, args.limit),
        _load_jsonl(args.ledger_path, args.limit),
        _load_jsonl(args.sessions_path, args.limit),
        include_verbatim_sessions=args.include_verbatim_sessions,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"Bundle diagnostico scritto in {args.output} "
        f"({len(bundle['actions'])} azioni, {len(bundle['ledger'])} ricevute, {len(bundle['sessions'])} sessioni). "
        "Apri e leggi il file prima di condividerlo con chiunque."
    )


if __name__ == "__main__":
    main()
