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

Baseline pre-sperimentazione: oltre alle righe grezze porta commit, configurazione REDATTA (chiavi segrete e
credenziali sostituite), modello Ollama scelto e modelli caricati, preflight, RAM, e un riepilogo dei turni recenti
(errori classificati, timeline degli stati, tempi mediani/p95). Le righe di turno e di stato non contengono mai testo
dell'utente ne' valori di ricordi (core/logger.log_turn/log_state); i percorsi della cartella utente diventano "~".

Uso:
    python main.py --diagnostics                              # stesso comando, dal punto d'ingresso di Jake
    python -m tools.diagnostic_bundle                        # scrive data/jake_diagnostic_bundle.json
    python -m tools.diagnostic_bundle --limit 50              # solo le ultime 50 righe per fonte
    python -m tools.diagnostic_bundle --output altro.json
    python -m tools.diagnostic_bundle --include-verbatim-sessions"""
import argparse
import json
import os
import platform
import re
import statistics
import subprocess
import sys
from pathlib import Path

from core.session_recorder import redact_value
from core.version import PROTOCOL_VERSION, VERSION

DEFAULT_ACTIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_actions.jsonl"
DEFAULT_LEDGER_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_ledger.jsonl"
DEFAULT_SESSIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_sessions.jsonl"
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_diagnostic_bundle.json"
DEFAULT_LIMIT = 200
ROOT = Path(__file__).resolve().parent.parent
_SECRET_NAME = re.compile(r"token|passphrase|password|secret|api_key|_key$|pin$", re.IGNORECASE)
_HOME = os.path.expanduser("~")


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


def _scrub_home(value):
    """I percorsi portano il nome dell'utente Windows: nel bundle diventano "~"."""
    if isinstance(value, str):
        return value.replace(_HOME, "~") if _HOME and len(_HOME) > 3 else value
    if isinstance(value, list):
        return [_scrub_home(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_home(v) for k, v in value.items()}
    return value


def redact_config(values: dict) -> dict:
    """Configurazione condivisibile: ogni chiave segreta o che sembra una credenziale diventa "<redacted>" (o "" se
    vuota, cosi' si vede comunque se e' impostata)."""
    from core.config import SECRET_KEYS

    redacted = {}
    for key, value in values.items():
        if key in SECRET_KEYS or _SECRET_NAME.search(key):
            redacted[key] = "<redacted>" if value else ""
        else:
            redacted[key] = _scrub_home(value)
    return redacted


def _percentiles(values: list[float]) -> dict:
    values = sorted(v for v in values if isinstance(v, (int, float)))
    if not values:
        return {}
    p95 = values[min(len(values) - 1, round(0.95 * (len(values) - 1)))]
    return {"n": len(values), "median": round(statistics.median(values), 1), "p95": round(p95, 1)}


def summarize(actions: list[dict], state_limit: int = 120) -> dict:
    """Cosa serve per capire una sessione reale senza leggere ogni riga: errori classificati, stati, tempi."""
    turns = [a for a in actions if a.get("kind") == "turn" and not a.get("private")]
    errors = [{"ts": t.get("ts"), "trace_id": t.get("trace_id"), "error": t.get("error") or t.get("outcome"),
               "route": t.get("route"), "intent": t.get("intent")}
              for t in turns if t.get("error") or t.get("outcome") not in (None, "ok")]
    errors += [{"ts": a.get("ts"), "trace_id": a.get("trace_id"), "error": a.get("result"), "skill": a.get("skill")}
               for a in actions if a.get("skill") and a.get("result") not in (None, "success")]
    timings: dict[str, list[float]] = {}
    for turn in turns:
        for name, ms in (turn.get("timings_ms") or {}).items():
            timings.setdefault(name, []).append(ms)
    timings["tts"] = [a["tts_ms"] for a in actions if a.get("kind") == "speech" and "tts_ms" in a]
    routes: dict[str, int] = {}
    for turn in turns:
        routes[turn.get("route", "other")] = routes.get(turn.get("route", "other"), 0) + 1
    return {
        "turns": len(turns),
        "private_turns": sum(1 for a in actions if a.get("kind") == "turn" and a.get("private")),
        "routes": routes,
        "memory_used_turns": sum(1 for t in turns if t.get("memory_used")),
        "errors": sorted(errors, key=lambda e: e.get("ts") or 0)[-50:],
        "timings_ms": {name: stats for name, values in timings.items() if (stats := _percentiles(values))},
        "rss_mb_last": next((t["rss_mb"] for t in reversed(turns) if t.get("rss_mb")), None),
        "state_timeline": [{"ts": a.get("ts"), "state": a.get("state"), "trace_id": a.get("trace_id")}
                           for a in actions if a.get("kind") == "state"][-state_limit:],
    }


def collect_environment() -> dict:
    """Commit, configurazione redatta, modello e modelli caricati, preflight e RAM. Ogni sezione e' indipendente:
    una che fallisce dice perche', non fa fallire il bundle."""
    env: dict = {}
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                               capture_output=True, text=True, timeout=10)
        env["git"] = {"commit": head.stdout.strip() or None, "local_changes": bool(dirty.stdout.strip())}
    except Exception as exc:
        env["git"] = {"error": type(exc).__name__}
    config_values: dict = {}
    try:
        from core.config import Config

        config = Config()
        config_values = dict(getattr(config, "_values", {}))
        env["config"] = redact_config(config_values)
    except Exception as exc:
        env["config"] = {"error": type(exc).__name__}
    try:
        from core.ollama_client import DEFAULT_BASE_URL, keep_alive_for

        model = config_values.get("ollama_model", "qwen2.5:7b")
        loaded = []
        from urllib import request as _request

        with _request.urlopen(f"{DEFAULT_BASE_URL.rstrip('/')}/api/ps", timeout=3) as response:
            for item in json.loads(response.read().decode("utf-8")).get("models", []):
                loaded.append({"name": item.get("name"), "size_vram_mb": round((item.get("size_vram") or 0) / 1e6),
                               "expires_at": item.get("expires_at")})
        env["ollama"] = {"selected_model": model, "keep_alive_primary": keep_alive_for(model),
                         "keep_alive_secondary": keep_alive_for("__secondary__"), "loaded": loaded}
    except Exception as exc:
        env["ollama"] = {"error": f"{type(exc).__name__}: Ollama non raggiungibile o risposta non valida"}
    try:
        from tools.preflight import run_all

        checks = run_all(config_values)
        env["preflight"] = [_scrub_home({"name": c.name, "status": c.status, "detail": c.detail}) for c in checks]
    except Exception as exc:
        env["preflight"] = {"error": type(exc).__name__}
    try:
        import psutil

        memory = psutil.virtual_memory()
        env["ram_mb"] = {"total": round(memory.total / 1e6), "available": round(memory.available / 1e6)}
    except Exception as exc:
        env["ram_mb"] = {"error": type(exc).__name__}
    return env


def _redact_ledger_record(record: dict) -> dict:
    """Le ricevute portano l'utente Windows che ha agito (audit locale): nel bundle condiviso non serve."""
    redacted = _scrub_home(dict(record))
    if redacted.get("windows_user"):
        redacted["windows_user"] = "<redacted>"
    return redacted


def build_bundle(
    actions: list[dict], ledger: list[dict], sessions: list[dict], *, include_verbatim_sessions: bool = False,
    environment: dict | None = None,
) -> dict:
    return {
        "jake_version": VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "python_version": sys.version,
        "platform": platform.platform(),
        "environment": environment or {},
        "summary": summarize(actions),
        "actions": actions,
        "ledger": [_redact_ledger_record(r) for r in ledger],
        "sessions": sessions if include_verbatim_sessions else [_redact_session_record(r) for r in sessions],
    }


def main(argv: list[str] | None = None):
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
    parser.add_argument("--no-environment", action="store_true", help="solo i log (niente git/Ollama/preflight)")
    args = parser.parse_args(argv)

    bundle = build_bundle(
        _load_jsonl(args.actions_path, args.limit),
        _load_jsonl(args.ledger_path, args.limit),
        _load_jsonl(args.sessions_path, args.limit),
        include_verbatim_sessions=args.include_verbatim_sessions,
        environment=None if args.no_environment else collect_environment(),
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
