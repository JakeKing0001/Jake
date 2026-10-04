"""Preflight (F0.6.2): prima di avviare Jake, controlla cio' che gli serve davvero su QUESTA macchina e dice cosa
manca con parole chiare, invece di scoprirlo a meta' di un comando. Nessuna modifica al sistema: solo letture.

- ERRORE: Jake non puo' funzionare (Python troppo vecchio, dipendenze base mancanti, cartella dati non scrivibile).
- ATTENZIONE: una parte non funzionera' (Ollama spento o modello non scaricato, niente microfono, HUD nativo
  abilitato ma non compilato, poco spazio su disco).
- OK / INFO: tutto bene, o un dato utile (VRAM libera).

Uso: python main.py --preflight   (oppure python -m tools.preflight). Codice d'uscita 1 solo se c'e' un ERRORE."""
from __future__ import annotations

import importlib.metadata
import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
MIN_PYTHON = (3, 11)
LOW_DISK_BYTES = 2 * 1024 ** 3  # modelli, database e registri crescono: sotto i 2 GB conviene saperlo prima
OK, INFO, WARN, FAIL = "OK", "INFO", "ATTENZIONE", "ERRORE"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def check_python(version=sys.version_info) -> Check:
    found = f"{version[0]}.{version[1]}"
    if tuple(version[:2]) < MIN_PYTHON:
        return Check("Python", FAIL, f"serve Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} o piu' recente, trovato {found}")
    return Check("Python", OK, found)


def _requirement_names(path: Path) -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        names.append(re.split(r"[<>=!~;\[ ]", line, maxsplit=1)[0])
    return names


def check_requirements(group: str, path: Path, status_if_missing: str, version_of=importlib.metadata.version) -> Check:
    missing = []
    for name in _requirement_names(path):
        try:
            version_of(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    if missing:
        return Check(f"Dipendenze {group}", status_if_missing,
                     f"mancano {', '.join(missing)}: installa con pip install -r {path.relative_to(ROOT).as_posix()}")
    return Check(f"Dipendenze {group}", OK, "installate")


def check_data_dir(data_dir: Path) -> Check:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=data_dir, prefix=".preflight-", delete=True):
            pass
    except OSError as exc:
        return Check("Cartella dati", FAIL, f"{data_dir} non e' scrivibile ({exc.strerror or exc})")
    return Check("Cartella dati", OK, str(data_dir))


def check_disk(data_dir: Path, usage=shutil.disk_usage) -> Check:
    free = usage(data_dir if data_dir.exists() else data_dir.anchor).free
    text = f"{free / 1024 ** 3:.1f} GB liberi"
    return Check("Spazio su disco", WARN if free < LOW_DISK_BYTES else OK,
                 text + (": sotto i 2 GB, modelli e registri potrebbero non starci" if free < LOW_DISK_BYTES else ""))


def check_ollama(base_url: str, model: str, fetch=None) -> Check:
    fetch = fetch or (lambda url: json.loads(request.urlopen(url, timeout=2).read().decode("utf-8")))
    try:
        tags = fetch(f"{base_url}/api/tags")
    except (error.URLError, OSError, ValueError) as exc:
        return Check("Ollama", WARN, f"non raggiungibile su {base_url} ({getattr(exc, 'reason', exc)}): Jake parte, "
                                     "ma non risponde alle domande libere finche' Ollama non e' avviato")
    installed = {str(m.get("name") or "") for m in tags.get("models", []) if isinstance(m, dict)}
    if model not in installed and f"{model}:latest" not in installed:
        return Check("Ollama", WARN, f"attivo, ma il modello configurato '{model}' non e' scaricato: ollama pull {model}")
    return Check("Ollama", OK, f"attivo, modello '{model}' presente")


def check_microphone(query_devices=None) -> Check:
    try:
        if query_devices is None:
            import sounddevice

            query_devices = sounddevice.query_devices
        devices = query_devices()
    except Exception as exc:  # driver audio assente/rotto: la voce non partira', il testo si'
        return Check("Microfono", WARN, f"impossibile leggere i dispositivi audio ({type(exc).__name__}): solo modalita' testo")
    inputs = [d for d in devices if (d.get("max_input_channels") or 0) > 0]
    if not inputs:
        return Check("Microfono", WARN, "nessun dispositivo di ingresso: la modalita' voce non potra' ascoltare")
    return Check("Microfono", OK, f"{len(inputs)} dispositivi di ingresso")


def check_native_hud(config: dict, default_exe: Path) -> Check | None:
    if not config.get("hud_native_enabled"):
        return None
    exe = Path(config.get("hud_native_path") or default_exe)
    if not exe.is_file():
        return Check("HUD nativo", WARN, f"abilitato ma {exe} non esiste: compila hud/native (vedi hud/native/README.md)")
    if not config.get("companion_server_enabled"):
        return Check("HUD nativo", WARN, "abilitato ma richiede companion_server_enabled: non partira'")
    return Check("HUD nativo", OK, str(exe))


def check_gpu(detect=None) -> Check:
    if detect is None:
        from core.model_router import detect_local_hardware as detect
    info = detect()
    if info.available_vram_mb is None:
        return Check("GPU", INFO, "nessuna GPU NVIDIA rilevata: i modelli girano su CPU (piu' lenti)")
    return Check("GPU", INFO, f"{info.available_vram_mb} MB di VRAM libera")


# stima di cio' che occupa la GPU oltre ai pesi del modello: contesto (8192 token) del 7B, voce RVC e HUD 3D
KV_CACHE_MB = 600
VOICE_AND_HUD_MB = 700


def check_ollama_offload(model: str, budget, calibration: dict | None) -> list[Check]:
    """Con un budget di VRAM (core/ollama_gpu_budget.py): quanto pesa il modello e quanto ne sta DAVVERO sulla GPU.
    Il modello non diventa piu' piccolo: cambia solo dove stanno i suoi layer."""
    if budget is None or not budget.enabled:
        return []
    if budget.fixed_layers is not None and calibration is None:
        return [Check(f"Ollama {model}", INFO, f"num_gpu fisso a {budget.fixed_layers} layer (ollama_num_gpu_layers): "
                                               "VRAM non misurata")]
    if calibration is None:
        return [Check("Budget GPU", WARN, f"modello principale <= {budget.budget_mb} MB ({budget.mode}) impostato ma non "
                                          "ancora calibrato: Jake lo calibra da solo al primo avvio (~30 s, nel frattempo "
                                          "usa la CPU) oppure: python main.py --calibrate-gpu")]
    vram = int(calibration.get("vram_mb") or 0)
    size = int(calibration.get("size_mb") or 0)
    layers, total = calibration.get("num_gpu"), calibration.get("layers_total")
    where = "solo CPU" if layers == 0 else f"offload parziale {layers}/{total} layer"
    detail = (f"~{size / 1000:.1f} GB modello, ~{vram / 1000:.2f} GB VRAM, ~{max(0, size - vram) / 1000:.1f} GB CPU/RAM, "
              f"{where}")
    within = vram <= budget.limit_mb
    budget_detail = f"modello principale ~{vram} MB <= {budget.budget_mb} MB ({budget.mode})" if within else (
        f"modello principale ~{vram} MB oltre il budget di {budget.budget_mb} MB ({budget.mode})")
    return [Check(f"Ollama {model}", OK, detail), Check("Budget GPU", OK if within else WARN, budget_detail)]


def check_gpu_budget(model: str, tags: dict | None, total_vram_mb: int | None, whisper_mb: int | None,
                     resident_model_mb: int | None = None) -> Check | None:
    """Prova reale del 27/09/2026: qwen2.5:7b (~4,8 GB in GPU) + Whisper su CUDA + voce + HUD 3D su una GPU da 8 GB hanno
    esaurito la memoria video e ogni risposta del modello e' scaduta. Qui si somma cio' che dovra' convivere sulla
    GPU e lo si confronta con la memoria totale, PRIMA di scoprirlo parlando."""
    if total_vram_mb is None or not isinstance(tags, dict):
        return None
    size = next((m.get("size") for m in tags.get("models", []) if isinstance(m, dict)
                 and m.get("name") in (model, f"{model}:latest")), None)
    if not isinstance(size, int):
        return None
    # con un budget calibrato conta solo la parte del modello residente sulla GPU, non i suoi ~5 GB
    model_mb = resident_model_mb if resident_model_mb is not None else size // (1024 * 1024) + KV_CACHE_MB
    need = model_mb + (whisper_mb or 0) + VOICE_AND_HUD_MB
    detail = (f"modello {model} ~{model_mb} MB + Whisper ~{whisper_mb or 0} MB + voce/HUD ~{VOICE_AND_HUD_MB} MB "
              f"= ~{need} MB su {total_vram_mb} MB")
    if need > total_vram_mb * 0.9:
        return Check("Memoria GPU", WARN, detail + ": troppo stretta, le risposte rischiano di essere lentissime. "
                     "Usa un modello piu' piccolo (ollama_model) o chiudi altri programmi che usano la GPU")
    return Check("Memoria GPU", OK, detail)


def run_all(config: dict) -> list[Check]:
    from core.native_hud import DEFAULT_EXE
    from core.ollama_client import DEFAULT_BASE_URL

    data_dir = ROOT / "data"
    checks = [
        check_python(),
        check_requirements("base", ROOT / "requirements" / "base.txt", FAIL),
        check_requirements("voce", ROOT / "requirements" / "voice.txt", WARN),
        check_data_dir(data_dir),
        check_disk(data_dir),
        check_ollama(DEFAULT_BASE_URL, str(config.get("ollama_model") or "qwen2.5:7b")),
        check_microphone(),
        check_gpu(),
    ]
    try:
        from core.voice.stt_provider import WhisperSttProvider, _gpu_total_vram_mb

        total = _gpu_total_vram_mb()
        whisper_mb = 1118 if WhisperSttProvider._cuda_compute_type(total) == "int8_float16" else 2061  # misurati
        tags = json.loads(request.urlopen(f"{DEFAULT_BASE_URL}/api/tags", timeout=2).read().decode("utf-8"))
        model = str(config.get("ollama_model") or "qwen2.5:7b")
        offload, calibration = _offload_status(config, tags, model)
        checks.extend(offload)
        resident = int(calibration["vram_mb"]) if calibration else None
        budget = check_gpu_budget(model, tags, total, whisper_mb, resident_model_mb=resident)
    except Exception:
        budget = None
    if budget is not None:
        checks.append(budget)
    hud = check_native_hud(config, DEFAULT_EXE)
    if hud is not None:
        checks.append(hud)
    return checks


def _offload_status(config: dict, tags: dict, model: str) -> tuple[list[Check], dict | None]:
    from core.ollama_client import OllamaClient
    from core.ollama_gpu_budget import Calibrator, OllamaApi, budget_from_settings, gpu_name, model_digest

    budget = budget_from_settings(config)
    if not budget.enabled:
        return [], None
    from core.ollama_client import DEFAULT_BASE_URL

    calibrator = Calibrator(OllamaApi(DEFAULT_BASE_URL), model, budget, budget.context or OllamaClient.DEFAULT_NUM_CTX,
                            gpu_name())
    calibration = calibrator.cached(model_digest(tags.get("models", []), model))
    return check_ollama_offload(model, budget, calibration), calibration


def render(checks: list[Check]) -> str:
    width = max(len(c.status) for c in checks)
    lines = [f"[{c.status:<{width}}] {c.name}: {c.detail}" for c in checks]
    failures = sum(c.status == FAIL for c in checks)
    warnings = sum(c.status == WARN for c in checks)
    if failures:
        lines.append(f"Jake non puo' partire: {failures} {'errore' if failures == 1 else 'errori'} da correggere.")
    elif warnings:
        lines.append(f"Jake puo' partire, con {warnings} {'limite indicato' if warnings == 1 else 'limiti indicati'} sopra.")
    else:
        lines.append("Tutto pronto.")
    return "\n".join(lines)


def main() -> int:
    from core.config import Config

    config = Config()
    checks = run_all({key: config.get(key) for key in (
        "ollama_model", "hud_native_enabled", "hud_native_path", "companion_server_enabled", "low_memory",
        "ollama_gpu_budget_mb", "ollama_gpu_budget_mode", "ollama_num_gpu_layers", "ollama_context")})
    print(render(checks))
    return 1 if any(c.status == FAIL for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
