"""Replay delle sessioni fallite (F0, vedi la fase F0 in ROADMAP.md e core/session_recorder.py).

Legge data/jake_sessions.jsonl (disattivato per default: va acceso con session_recording_enabled
in config/settings.json, vedi README) e per ogni fallimento registrato:
- se e' stato registrato in modalita' VERBATIM (session_recording_verbatim=True, parametri
  veri), lo fa ripartire davvero con SkillRegistry.execute(intent, parametri) e confronta
  l'errore di allora con quello di adesso - utile per verificare che un fix abbia funzionato
  DAVVERO, non solo a occhio;
- se e' stato registrato in modalita' redatta (il default quando l'opzione e' accesa: valori
  stringa sostituiti da segnaposto "<str:N caratteri>"), non puo' essere rieseguito per davvero
  (mancano i valori veri): ne stampa solo un riepilogo (intent, forma dei parametri, errore).

Uso:
    python -m tools.replay_session                       # elenca tutti i fallimenti registrati
    python -m tools.replay_session --intent FIND_FILE     # solo un intent
    python -m tools.replay_session --trace-id abc123def0  # solo un trace_id
    python -m tools.replay_session --replay               # rilancia davvero quelli verbatim"""
import argparse
import json
from pathlib import Path

from core.session_recorder import DEFAULT_PATH


def load_records(path: Path, intent: str = None, trace_id: str = None) -> list[dict]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if intent and record.get("intent") != intent:
            continue
        if trace_id and record.get("trace_id") != trace_id:
            continue
        records.append(record)
    return records


def _describe_parameters(parameters: dict, verbatim: bool) -> str:
    if verbatim:
        return json.dumps(parameters, ensure_ascii=False)
    shape = {key: (value if not isinstance(value, str) else value) for key, value in parameters.items()}
    return json.dumps(shape, ensure_ascii=False)


def replay_one(record: dict) -> str:
    """Rilancia un fallimento verbatim con la stessa SkillRegistry vera che usa Jake, senza
    passare da NLU/agente (l'intent e i parametri sono gia' noti: e' questo che rende il replay
    deterministico, non dipendente da come il modello classificherebbe la frase oggi)."""
    from core.skill_registry import SkillRegistry

    registry = SkillRegistry()
    result = registry.execute(record["intent"], record.get("parameters") or {})
    if result is None:
        return "ERRORE ANCORA PRESENTE: intent non trovato (UNKNOWN_INTENT)"
    new_error = None if result.success else result.error
    old_error = record.get("error", "").removeprefix("error:")
    if result.success:
        return "RISOLTO: ora ha successo"
    if new_error == old_error:
        return f"ANCORA PRESENTE: stesso errore ({new_error})"
    return f"CAMBIATO: ora fallisce con {new_error} invece di {old_error}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--intent", default=None)
    parser.add_argument("--trace-id", default=None)
    parser.add_argument("--replay", action="store_true", help="rilancia davvero i fallimenti verbatim (tocca il sistema vero)")
    args = parser.parse_args()

    records = load_records(args.path, intent=args.intent, trace_id=args.trace_id)
    if not records:
        print(f"Nessun fallimento registrato in {args.path} (session_recording_enabled e' spento per default, vedi README).")
        return

    print(f"{len(records)} fallimenti registrati:\n")
    for record in records:
        verbatim = record.get("verbatim", False)
        mode = "verbatim" if verbatim else "redatto"
        print(f"[{record.get('trace_id')}] {record.get('intent')} ({mode}, errore: {record.get('error')})")
        print(f"  parametri: {_describe_parameters(record.get('parameters') or {}, verbatim)}")
        if args.replay:
            if verbatim:
                print(f"  -> {replay_one(record)}")
            else:
                print("  -> non rieseguibile: registrato in modalita' redatta, mancano i valori veri")
        print()


if __name__ == "__main__":
    main()
