"""Gate hardware di F2: sessione vocale strumentata e verdetto (docs/f2-hardware-validation.md).

    python -m benchmarks.f2_hardware_session run --profile headphones
    python -m benchmarks.f2_hardware_session run --profile laptop
    python -m benchmarks.f2_hardware_session run --profile bluetooth
    python -m benchmarks.f2_hardware_session run --profile wake --hours-note "notte con TV"
    python -m benchmarks.f2_hardware_session evaluate

`run` avvia la stessa voce continua di `main.py --voice --wake-word` (stessi provider, stessa voce
di personaggio se configurata) con `voice_barge_in` e `voice_partials` forzati a `on` SOLO per la
durata della prova: `config/settings.json` non viene modificato. Jake misura da se' latenze e
conteggi (core/voice/session_metrics.py); alla chiusura (Ctrl+C o "Jake, esci") chiede i conteggi
che solo chi fa la prova conosce e salva `benchmarks/results/f2_hardware_<profilo>_<data>.json`.
Nessun audio e nessun testo trascritto vengono salvati.

`evaluate` legge tutti i report e applica le soglie del gate: per OGNI profilo (headphones, laptop,
bluetooth) >= 20 interruzioni con >= 95% riconosciute, p95 <= 300 ms, 0 comandi da eco, 20 risposte
senza interruzione senza falsi stop, prima emissione p95 < 2 s, partial p95 < 1 s (o fallback senza
finali duplicati); per il wake >= 24 h cumulative, <= 1 falso wake ogni 24 h, >= 20 wake
intenzionali con miss rate <= 5%. Un criterio mancante e' `VERIFY`, mai verde per assenza di dati.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks._report import RESULTS_DIR, percentile  # noqa: E402

AUDIO_PROFILES = ("headphones", "laptop", "bluetooth")
PROFILES = AUDIO_PROFILES + ("wake",)
METHOD_VERSION = 3

MIN_INTERRUPTIONS = 20
MIN_DETECTION_RATE = 0.95
MAX_STOP_P95_MS = 300.0
# La latenza misurata da Jake e' un limite inferiore (manca la latenza d'ingresso della scheda audio):
# sopra questa soglia serve la misura esterna (registrazione microfono + loopback) per dare PASS.
MAX_INTERNAL_STOP_P95_MS = 250.0
INTERNAL_LATENCY_METHOD = "internal_lower_bound"
EXTERNAL_LATENCY_METHOD = "external_recording"
MIN_QUIET_TRIALS = 20
MIN_QUIET_EACH_CONDITION = 10  # >= 10 in stanza normale E >= 10 con TV/parlato di sottofondo
MAX_FIRST_EMISSION_P95_MS = 2000.0
MAX_PARTIAL_P95_MS = 1000.0
MIN_WAKE_HOURS = 24.0
MAX_FALSE_WAKES_PER_24H = 1.0
MIN_INTENTIONAL_WAKES = 20
MAX_MISS_RATE = 0.05
WAKE_DISTANCES = ("0.5m", "3m", "6m")


# ---- verdetto ----------------------------------------------------------------------------------


def _p95(values) -> float | None:
    values = [float(v) for v in values or []]
    return percentile(values, 95) if values else None


def evaluate_profile(report: dict) -> dict:
    """Criteri di UN profilo audio. Ogni voce: PASS / FAIL / VERIFY (dati insufficienti)."""
    barge = report.get("barge_in", {})
    attempts = int(barge.get("attempts") or 0)
    detected = int(barge.get("detected") or 0)
    checks: dict[str, str] = {}

    if attempts < MIN_INTERRUPTIONS:
        checks["interruptions_detected"] = "VERIFY"
    else:
        checks["interruptions_detected"] = "PASS" if detected / attempts >= MIN_DETECTION_RATE else "FAIL"

    stop_p95 = _p95(barge.get("latency_ms"))
    if stop_p95 is None or attempts < MIN_INTERRUPTIONS:
        checks["stop_p95"] = "VERIFY"
    elif stop_p95 > MAX_STOP_P95_MS:
        checks["stop_p95"] = "FAIL"  # anche un limite inferiore oltre la soglia e' un fallimento certo
    elif barge.get("latency_method") != EXTERNAL_LATENCY_METHOD and stop_p95 > MAX_INTERNAL_STOP_P95_MS:
        checks["stop_p95"] = "VERIFY"  # stima interna tra 250 e 300 ms: serve la misura esterna
    else:
        checks["stop_p95"] = "PASS"

    quiet_room = int(barge.get("no_interruption_quiet_room") or 0)
    background = int(barge.get("no_interruption_background") or 0)
    quiet_ok = (quiet_room + background >= MIN_QUIET_TRIALS and quiet_room >= MIN_QUIET_EACH_CONDITION
                and background >= MIN_QUIET_EACH_CONDITION)
    false_stops = int(barge.get("false_stops") or 0)
    echo_commands = int(barge.get("echo_commands") or 0)
    if echo_commands > 0:
        checks["no_echo_commands"] = "FAIL"
    else:
        checks["no_echo_commands"] = "PASS" if quiet_ok else "VERIFY"
    if false_stops > 0:
        checks["no_false_stops"] = "FAIL"
    else:
        checks["no_false_stops"] = "PASS" if quiet_ok else "VERIFY"

    first = _p95(report.get("tts", {}).get("first_emission_ms"))
    checks["first_emission_p95"] = "VERIFY" if first is None else ("PASS" if first < MAX_FIRST_EMISSION_P95_MS else "FAIL")

    streaming = report.get("streaming", {})
    partial = _p95(streaming.get("partial_latency_ms"))
    if int(streaming.get("duplicate_finals") or 0) > 0:
        checks["partials"] = "FAIL"
    elif partial is None:
        # nessun partial pubblicato: accettabile solo come fallback a frase completa DAVVERO attivo
        # (trascrittore assente o degradato) e con frasi finali prodotte, mai per semplice assenza di dati
        fallback = bool(streaming.get("fallback_full_utterance")) and int(streaming.get("finals") or 0) > 0
        checks["partials"] = "PASS" if fallback else "VERIFY"
    else:
        checks["partials"] = "PASS" if partial < MAX_PARTIAL_P95_MS else "FAIL"

    return {
        "checks": checks,
        "stop_p95_ms": stop_p95,
        "first_emission_p95_ms": first,
        "partial_p95_ms": partial,
        "verdict": _combine(checks.values()),
    }


def evaluate_wake(reports: list[dict]) -> dict:
    hours = sum(float(r.get("wake", {}).get("hours") or 0) for r in reports)
    false_wakes = sum(int(r.get("wake", {}).get("false_wakes") or 0) for r in reports)
    intentional = sum(int(r.get("wake", {}).get("intentional") or 0) for r in reports)
    missed = sum(int(r.get("wake", {}).get("missed") or 0) for r in reports)
    by_distance = {d: sum(int((r.get("wake", {}).get("intentional_by_distance") or {}).get(d) or 0) for r in reports)
                   for d in WAKE_DISTANCES}
    checks = {}
    if hours < MIN_WAKE_HOURS:
        checks["false_wake_rate"] = "FAIL" if false_wakes > MAX_FALSE_WAKES_PER_24H else "VERIFY"
    else:
        checks["false_wake_rate"] = "PASS" if false_wakes / (hours / 24.0) <= MAX_FALSE_WAKES_PER_24H else "FAIL"
    if intentional < MIN_INTENTIONAL_WAKES:
        checks["miss_rate"] = "VERIFY"
    else:
        checks["miss_rate"] = "PASS" if missed / intentional <= MAX_MISS_RATE else "FAIL"
    # "distribuiti tra circa 0,5 m, 3 m e 6 m": tutte e tre le distanze devono essere state provate
    checks["wake_distances"] = "PASS" if all(by_distance[d] > 0 for d in WAKE_DISTANCES) else "VERIFY"
    return {"hours": round(hours, 2), "false_wakes": false_wakes, "intentional": intentional, "missed": missed,
            "by_distance": by_distance, "checks": checks, "verdict": _combine(checks.values())}


def _combine(values) -> str:
    values = list(values)
    if "FAIL" in values:
        return "FAIL"
    if "VERIFY" in values or not values:
        return "VERIFY"
    return "PASS"


def evaluate_reports(reports: list[dict]) -> dict:
    """Verdetto del gate: tutti e tre i profili audio verdi E il wake verde. Un profilo senza
    report e' VERIFY; il migliore dei report di un profilo non nasconde un fallimento: vale
    l'ultimo report di quel profilo."""
    latest: dict[str, dict] = {}
    for report in sorted(reports, key=lambda r: r.get("recorded_at", "")):
        profile = report.get("profile")
        if profile in AUDIO_PROFILES:
            latest[profile] = report
    profiles = {name: evaluate_profile(latest[name]) if name in latest else {"verdict": "VERIFY", "checks": {}}
                for name in AUDIO_PROFILES}
    wake = evaluate_wake(reports)
    gate = _combine([p["verdict"] for p in profiles.values()] + [wake["verdict"]])
    return {"profiles": profiles, "wake": wake, "gate": gate}


def load_reports(directory: Path = RESULTS_DIR) -> list[dict]:
    reports = []
    for path in sorted(directory.glob("f2_hardware_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("method_version") == METHOD_VERSION:
            reports.append(data)
    return reports


# ---- sessione strumentata ---------------------------------------------------------------------


def _ask_int(prompt: str, default: int = 0) -> int:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit():
            return int(raw)
        print("Serve un numero intero.")


def parse_latencies_ms(raw: str) -> list[float] | None:
    """"210, 230;250" -> [210.0, 230.0, 250.0]; vuoto -> []; None se c'e' un valore non valido."""
    values = []
    for token in raw.replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = float(token)
        except ValueError:
            return None
        if value < 0:
            return None
        values.append(value)
    return values


def _ask_latencies(prompt: str) -> list[float]:
    while True:
        values = parse_latencies_ms(input(f"{prompt}: "))
        if values is not None:
            return values
        print("Servono millisecondi separati da virgole (es. 210, 235).")


def _hardware_description(session) -> dict:
    info = {"machine": platform.machine(), "processor": platform.processor(), "windows": platform.platform()}
    try:
        import sounddevice as sd

        default_in, default_out = sd.default.device
        info["input"] = sd.query_devices(default_in)["name"] if default_in is not None and default_in >= 0 else "?"
        info["output"] = sd.query_devices(default_out)["name"] if default_out is not None and default_out >= 0 else "?"
    except Exception:
        info["input"] = info["output"] = "non rilevato"
    info["stt"] = f"{getattr(session.stt_provider, 'model_size', '?')} su {getattr(session.stt_provider, 'device', '?')}"
    info["tts"] = type(session.tts_provider).__name__
    return info


def ask_manual_counts(profile: str) -> dict:
    """I conteggi che solo chi fa la prova conosce (docs/f2-hardware-validation.md)."""
    answers = {}
    if profile in AUDIO_PROFILES:
        answers["attempts"] = _ask_int("Quante interruzioni hai provato a fare?")
        answers["quiet_room"] = _ask_int("Quante risposte hai lasciato finire in stanza normale?")
        answers["background"] = _ask_int("Quante risposte hai lasciato finire con TV/parlato di sottofondo?")
        answers["false_stops"] = _ask_int("In quante di queste Jake si e' fermato da solo?")
        answers["echo_commands"] = _ask_int("Quante volte Jake ha eseguito un comando uscito dalla SUA voce?")
        answers["external_latency_ms"] = _ask_latencies(
            "Latenze voce->stop misurate con registrazione esterna, in ms separati da virgole (INVIO = stima interna)")
    answers["false_wakes"] = _ask_int("Quante attivazioni NON volute hai contato?")
    for distance in WAKE_DISTANCES:
        answers[f"intentional_{distance}"] = _ask_int(f"Quanti 'Jake' intenzionali a circa {distance}?")
    answers["missed"] = _ask_int("Quanti dei 'Jake' intenzionali NON ha sentito?")
    return answers


def build_report(profile: str, measured: dict, answers: dict, hardware: dict, notes: str = "",
                 partials_degraded: bool = False) -> dict:
    """Il report locale: solo numeri, conteggi e descrizione dell'hardware - mai audio ne' testo
    trascritto (le metriche di sessione non ne contengono per costruzione)."""
    report: dict = {
        "method_version": METHOD_VERSION,
        "profile": profile,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "hardware": hardware,
        "raw_audio_retained": False,
        "notes": notes,
        "measured_by_jake": measured,
    }
    if profile in AUDIO_PROFILES:
        attempts = int(answers.get("attempts") or 0)
        false_stops = int(answers.get("false_stops") or 0)
        # Le interruzioni riconosciute da Jake includono i falsi stop delle prove silenziose:
        # vanno tolti, altrimenti un falso stop gonfierebbe il tasso di riconoscimento.
        detected = max(0, min(attempts, measured["barge_in"]["detected"] - false_stops))
        # La misura esterna, se c'e', sostituisce la stima interna (che resta in measured_by_jake).
        external = list(answers.get("external_latency_ms") or [])
        report["barge_in"] = {
            "attempts": attempts, "detected": detected,
            "latency_ms": external or measured["barge_in"]["latency_ms"],
            "latency_method": EXTERNAL_LATENCY_METHOD if external else measured["barge_in"]["method"],
            "no_interruption_quiet_room": int(answers.get("quiet_room") or 0),
            "no_interruption_background": int(answers.get("background") or 0),
            "false_stops": false_stops, "echo_commands": int(answers.get("echo_commands") or 0),
        }
        streaming = measured["streaming"]
        report["streaming"] = {
            "partial_latency_ms": streaming["partial_latency_ms"],
            "duplicate_finals": streaming["duplicate_finals"],
            "finals": streaming.get("finals", 0),
            # fallback solo se i partial non erano davvero disponibili (trascrittore assente o degradato)
            "fallback_full_utterance": not streaming["partial_latency_ms"] and partials_degraded,
        }
        report["tts"] = {"first_emission_ms": measured["tts"]["first_emission_ms"]}
    by_distance = {d: int(answers.get(f"intentional_{d}") or 0) for d in WAKE_DISTANCES}
    report["wake"] = {
        "hours": measured["hours"], "accepted": measured["wake"]["accepted"],
        "false_wakes": int(answers.get("false_wakes") or 0),
        "intentional": sum(by_distance.values()), "intentional_by_distance": by_distance,
        "missed": int(answers.get("missed") or 0),
    }
    return report


def run_session(profile: str, notes: str = "") -> Path:
    import main
    from core.jake_core import JakeCore
    from core.voice.session_metrics import SessionMetrics
    from core.voice.wake_word_session import WakeWordSession

    core = JakeCore()
    tts_provider, stt_provider, server_manager = main._setup_voice(core)
    metrics = SessionMetrics()
    session = WakeWordSession(
        core, stt_provider, tts_provider, follow_up_seconds=float(core.config.get("follow_up_seconds", 6)),
        replay_window_seconds=float(core.config.get("voice_replay_guard_seconds", 0)),
        speech_style=str(core.config.get("voice_style", "normal") or "normal"),
        output_device_name=core.config.get("voice_output_device") or None,
        barge_in="on", partials="on", speaker_store=main._speaker_store(), metrics=metrics,
    )
    print(f"Prova F2 '{profile}': voce continua con barge-in e partial attivi (solo per questa prova).")
    print("Promemoria, automazioni e avvisi proattivi sono sospesi fino alla fine della prova.")
    print("Segui docs/f2-hardware-validation.md. Chiudi con Ctrl+C o dicendo 'Jake, esci'.")
    released: list[str] = []
    try:
        with core.proactivity_suspended("prova hardware F2") as released:
            try:
                session.run()
            except KeyboardInterrupt:
                pass
    finally:
        session.stop()
        for message in released:
            print(f"Rimandato durante la prova: {message}")
        if server_manager is not None:
            server_manager.stop()
        core.shutdown()

    measured = metrics.snapshot()
    print("\nConteggi che solo tu conosci (INVIO = 0).")
    live = session.live_transcriber
    report = build_report(
        profile, measured, ask_manual_counts(profile), _hardware_description(session), notes,
        partials_degraded=live is None or bool(getattr(live, "degraded", False)),
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"f2_hardware_{profile}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report salvato in {path}")
    return path


def print_verdict(result: dict) -> None:
    for name, profile in result["profiles"].items():
        print(f"{name:11s} {profile['verdict']:6s} {profile.get('checks', {})}")
    wake = result["wake"]
    print(f"{'wake':11s} {wake['verdict']:6s} ore={wake['hours']} falsi={wake['false_wakes']} "
          f"intenzionali={wake['intentional']} persi={wake['missed']} {wake['checks']}")
    print(f"GATE F2 HARDWARE: {result['gate']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Gate hardware di F2")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--profile", choices=PROFILES, required=True)
    run.add_argument("--notes", default="")
    sub.add_parser("evaluate")
    args = parser.parse_args(argv)
    if args.command == "run":
        run_session(args.profile, args.notes)
        return 0
    result = evaluate_reports(load_reports())
    print_verdict(result)
    return 0 if result["gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
