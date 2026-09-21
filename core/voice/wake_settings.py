"""Impostazioni di ascolto per dispositivo e calibrazione del rumore (F2.3.1, F2.3.2).

Un microfono da cuffia, quello del portatile e quello di un satellite in cucina non hanno lo stesso
rumore di fondo ne' lo stesso guadagno: aggressivita' del VAD, silenzio che chiude una frase,
tolleranza sulla wake word e finestra di follow-up sono quindi impostazioni PER DISPOSITIVO, con
default sensati e ogni valore validato (un valore assurdo in settings.json non deve poter rompere
l'ascolto o, peggio, renderlo troppo permissivo senza dirlo).

La calibrazione del rumore ambientale (`NoiseCalibrator`) riceve soltanto LIVELLI (un numero per
frame): l'audio non entra mai in questo modulo, quindi non puo' essere registrato ne' conservato."""
from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

WAKE_WORD_VARIANTS = ("jake", "geek", "jack", "jache", "jek", "gec", "jeik", "cheic")
SETTINGS_KEY = "voice_devices"  # config: {"<nome dispositivo>": {"vad_aggressiveness": 3, ...}}

SENSITIVITY_PRESETS = {
    # (vad_aggressiveness, wake_max_distance): piu' sensibile = VAD meno severo e piu' tolleranza
    "high": (1, 2),
    "medium": (2, 1),
    "low": (3, 0),
}
# Parole troppo comuni per essere una wake word: farebbero scattare Jake a ogni frase normale.
_FORBIDDEN_WAKE_WORDS = {"si", "no", "ok", "okay", "ehi", "hey", "ciao", "apri", "chiudi", "stop", "basta", "come", "che", "cosa"}
_WORD = re.compile(r"^[a-zA-ZÀ-ſ]{3,20}$")


class SettingsError(ValueError):
    """Un valore di impostazione non valido."""


@dataclass(frozen=True)
class WakeSettings:
    wake_words: tuple[str, ...] = WAKE_WORD_VARIANTS
    wake_max_distance: int = 1
    vad_aggressiveness: int = 2
    silence_ms: int = 700
    follow_up_seconds: float = 6.0
    energy_gate: float = 0.0  # livello RMS (0-1) sotto il quale un frame non conta come parlato

    def __post_init__(self) -> None:
        if not self.wake_words:
            raise SettingsError("serve almeno una wake word")
        for word in self.wake_words:
            if not _WORD.match(word):
                raise SettingsError(f"wake word non valida: {word!r} (3-20 lettere)")
            if word.lower() in _FORBIDDEN_WAKE_WORDS:
                raise SettingsError(f"wake word troppo comune: {word!r}")
        if not 0 <= self.wake_max_distance <= 2:
            raise SettingsError("wake_max_distance deve essere 0-2")
        if self.vad_aggressiveness not in (0, 1, 2, 3):
            raise SettingsError("vad_aggressiveness deve essere 0-3")
        if not 300 <= self.silence_ms <= 3000:
            raise SettingsError("silence_ms deve essere 300-3000")
        if not 0 <= self.follow_up_seconds <= 30:
            raise SettingsError("follow_up_seconds deve essere 0-30")
        if not 0.0 <= self.energy_gate <= 0.5:
            raise SettingsError("energy_gate deve essere 0-0.5")


_FIELD_TYPES = {
    "wake_words": tuple, "wake_max_distance": int, "vad_aggressiveness": int, "silence_ms": int,
    "follow_up_seconds": (int, float), "energy_gate": (int, float),
}


def settings_from_dict(overrides: dict | None, base: WakeSettings | None = None) -> WakeSettings:
    """Applica `overrides` (da settings.json) sopra `base`. Chiavi sconosciute -> errore (un refuso
    come "vad_agressiveness" non deve essere ignorato in silenzio); "sensitivity" e' un
    preset che imposta aggressivita' e tolleranza insieme, e i valori espliciti vincono."""
    settings = base or WakeSettings()
    if not overrides:
        return settings
    data = dict(overrides)
    changes: dict = {}
    preset = data.pop("sensitivity", None)
    if preset is not None:
        if preset not in SENSITIVITY_PRESETS:
            raise SettingsError(f"sensitivity deve essere una tra {', '.join(SENSITIVITY_PRESETS)}")
        changes["vad_aggressiveness"], changes["wake_max_distance"] = SENSITIVITY_PRESETS[preset]
    for key, value in data.items():
        if key not in _FIELD_TYPES:
            raise SettingsError(f"impostazione sconosciuta: {key!r}")
        if key == "wake_words":
            if isinstance(value, str) or not isinstance(value, Iterable):
                raise SettingsError("wake_words deve essere una lista di parole")
            value = tuple(str(word).strip().lower() for word in value)
        elif isinstance(value, bool) or not isinstance(value, _FIELD_TYPES[key]):  # type: ignore[arg-type]
            raise SettingsError(f"{key}: tipo non valido ({type(value).__name__})")
        changes[key] = value
    return replace(settings, **changes)


def load_wake_settings(config, device_name: str | None) -> WakeSettings:
    """Impostazioni per `device_name` da `config` (core.config.Config o qualunque oggetto con .get):
    sezione "default" poi la voce specifica del dispositivo. Un valore non valido solleva
    SettingsError: meglio un errore chiaro all'avvio che un ascolto configurato a caso."""
    table = config.get(SETTINGS_KEY, {}) if config is not None else {}
    if not isinstance(table, dict):
        raise SettingsError(f"{SETTINGS_KEY} deve essere un oggetto")
    settings = settings_from_dict(table.get("default"))
    if device_name and device_name in table:
        settings = settings_from_dict(table[device_name], settings)
    return settings


# ---- calibrazione del rumore ---------------------------------------------------------------

@dataclass(frozen=True)
class Calibration:
    noise_floor: float  # mediana dei livelli RMS (0-1)
    noise_peak: float  # 95mo percentile
    quality: str  # "quiet" | "moderate" | "noisy"
    recommended_aggressiveness: int
    recommended_energy_gate: float
    frames_used: int = field(default=0)


class NoiseCalibrator:
    """Raccoglie livelli RMS di frame di "silenzio" e propone impostazioni. Tiene solo float, mai
    audio (`levels` e' una lista di numeri: un test lo verifica). Chi la usa deve far tacere
    l'utente per ~1,5 s; se durante la calibrazione c'era voce i livelli alti la fanno scartare
    (`result()` ritorna None se il rumore e' troppo variabile per essere "di fondo")."""

    def __init__(self, min_frames: int = 50) -> None:
        self.min_frames = min_frames
        self.levels: list[float] = []

    def add_level(self, level: float) -> None:
        if not isinstance(level, (int, float)) or math.isnan(level) or level < 0:
            raise ValueError(f"livello non valido: {level!r}")
        self.levels.append(min(1.0, float(level)))

    def add_frame(self, frame) -> None:
        """Comodita': calcola il livello RMS di un frame int16 e LO SCARTA."""
        import numpy as np

        samples = np.asarray(frame, dtype=np.float32).reshape(-1)
        self.add_level(float(np.sqrt(np.mean(samples**2))) / 32768.0 if samples.size else 0.0)

    def result(self) -> Calibration | None:
        if len(self.levels) < self.min_frames:
            return None
        ordered = sorted(self.levels)
        median = ordered[len(ordered) // 2]
        peak = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        if peak > max(0.05, median * 6):  # picchi molto sopra la mediana: c'era voce o un colpo, non fondo
            return None
        if median < 0.004:
            quality, aggressiveness = "quiet", 2
        elif median < 0.02:
            quality, aggressiveness = "moderate", 2
        else:
            quality, aggressiveness = "noisy", 3
        gate = round(min(0.5, max(0.0, peak * 1.5)), 4) if quality != "quiet" else 0.0
        return Calibration(round(median, 5), round(peak, 5), quality, aggressiveness, gate, len(self.levels))


def apply_calibration(settings: WakeSettings, calibration: Calibration) -> WakeSettings:
    """Un ambiente piu' rumoroso alza l'aggressivita' e il gate, mai li abbassa sotto cio' che
    l'utente ha scelto: una calibrazione non puo' rendere l'ascolto piu' permissivo di prima."""
    return replace(
        settings,
        vad_aggressiveness=max(settings.vad_aggressiveness, calibration.recommended_aggressiveness),
        energy_gate=max(settings.energy_gate, calibration.recommended_energy_gate),
    )
