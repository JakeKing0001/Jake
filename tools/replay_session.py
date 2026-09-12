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
    python -m tools.replay_session --replay               # rilancia davvero quelli verbatim

F1.7.5 ("rendere replay sicuro: dry-run predefinito, scope temporaneo e conferma per effetti"):
buco reale, riprodotto prima del fix - replay_one() chiamava SkillRegistry.execute() DIRETTAMENTE,
lo stesso "percorso 7" (dispatcher grezzo) gia' documentato come privo di controllo di policy
proprio (vedi docs/action-execution-paths.md, F1.2.1). Un record VERBATIM salva i parametri
ESATTI passati all'epoca - inclusi eventuali "confirmed": true/"authenticated": true, perche' e'
del tutto plausibile che un'azione CONFIRMATA sia comunque fallita dopo (permesso negato, disco
pieno, file bloccato...) e finisca comunque registrata come fallimento verbatim. Rieseguirla con
`--replay` significava rieseguire un DELETE_PATH/RUN_COMMAND gia' "autorizzato" nel record SENZA
alcun controllo di policy e SENZA alcun utente presente per una nuova conferma - un bypass
completo dell'intera architettura di autorizzazione per uno strumento pensato solo per il debug.
Corretto: i segnali di autorizzazione vengono tolti dai parametri prima di rieseguire (stesso
principio di PlanExecutor.strip_authorization_signals - un record non puo' auto-autorizzarsi piu'
di quanto potrebbe un piano automatico), e l'intent passa comunque da
PolicyEngine.decide_automated() (nessun utente reale e' presente per confermare durante un
replay batch, quindi CONFIRM/BLOCK vengono sempre rifiutati, mai eseguiti automaticamente) prima
di toccare SkillRegistry.execute(). Un intent DESTRUCTIVE/ADMIN o esplicitamente bloccato in
config.json non viene piu' rieseguito da questo strumento: va verificato a mano dall'utente."""
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


def replay_one(record: dict, policy_engine=None, registry=None) -> str:
    """Rilancia un fallimento verbatim con la stessa SkillRegistry vera che usa Jake, senza
    passare da NLU/agente (l'intent e i parametri sono gia' noti: e' questo che rende il replay
    deterministico, non dipendente da come il modello classificherebbe la frase oggi).

    F1.7.5: passa comunque da PolicyEngine.decide_automated() prima di eseguire (vedi il
    docstring del modulo) - nessun utente e' presente per confermare durante un replay batch,
    quindi un intent CONFIRM/BLOCK viene rifiutato onestamente invece di essere eseguito senza
    controllo. I segnali di autorizzazione gia' presenti nel record (es. "confirmed": true di
    un'azione che era stata confermata ma e' comunque fallita dopo) vengono tolti prima sia della
    decisione sia dell'esecuzione: un record non puo' auto-autorizzarsi. `policy_engine`/
    `registry` sono iniettabili (usati dai test per non dipendere dal vero config/settings.json
    su disco o per osservare la chiamata reale alla skill): se `None`, ne costruisce di veri,
    come farebbe l'uso da riga di comando."""
    from core.policy_engine import PolicyDecision, strip_authorization_signals
    from core.skill_registry import SkillRegistry

    intent = record["intent"]
    parameters = strip_authorization_signals(record.get("parameters") or {})

    if registry is None:
        registry = SkillRegistry()
    if policy_engine is None:
        from core.config import Config
        from core.policy_engine import PolicyEngine

        policy_engine = PolicyEngine(blocked_intents=Config().get("blocked_intents", []) or [])
        policy_engine.sync_with_registry(registry)
    decision = policy_engine.decide_automated(intent)
    if decision != PolicyDecision.ALLOW:
        return (
            f"NON RIESEGUITO: la policy risponde '{decision.value}' per {intent} - nessun utente "
            "presente per confermare durante un replay. Verificalo a mano se ti serve davvero."
        )

    result = registry.execute(intent, parameters)
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
