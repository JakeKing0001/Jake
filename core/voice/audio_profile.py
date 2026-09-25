"""Volume e ritmo del parlato in base al dispositivo di uscita e allo stile scelto (F2.5.7).

L'unico segnale che questo modulo guarda e' il NOME del dispositivo audio di uscita (cuffie,
altoparlante del portatile, TV...) piu' lo stile che l'utente ha scelto (`speech_text.STYLES`).
Non analizza la voce, il tono o l'umore di chi parla e non ne inferisce emozioni: adattare il
volume alle cuffie e' una cortesia, dedurre come sta una persona dalla sua voce e' un'altra cosa
(vedi "Cose che Jake non deve mai diventare" nella roadmap)."""
from __future__ import annotations

from dataclasses import dataclass

from core.voice.speech_text import SpeechStyle


@dataclass(frozen=True)
class OutputProfile:
    kind: str
    volume_scale: float  # moltiplicatore del volume dello stile, in (0, 1]
    rate_delta_percent: int  # ritmo relativo: chi ascolta da lontano/su TV vuole un parlato piu' lento


PROFILES: dict[str, OutputProfile] = {
    "headphones": OutputProfile("headphones", 0.6, 0),  # l'orecchio e' vicino: meno volume, stesso ritmo
    "bluetooth": OutputProfile("bluetooth", 0.8, -3),  # latenza e compressione: un filo piu' lento
    "laptop-speaker": OutputProfile("laptop-speaker", 1.0, 0),
    "tv": OutputProfile("tv", 1.0, -8),  # stanza grande, riverbero: piu' lento
    "unknown": OutputProfile("unknown", 1.0, 0),
}

_KEYWORDS = (
    ("headphones", ("headphone", "headset", "cuffie", "earphone", "auricolar", "airpods")),
    ("bluetooth", ("bluetooth", "hands-free", "a2dp")),
    ("tv", ("hdmi", "displayport", " tv", "tv ", "television", "televisore", "monitor")),
    ("laptop-speaker", ("speaker", "altoparlant", "casse", "realtek")),
)


def classify_output_device(device_name: str | None) -> str:
    """Tipo di dispositivo dal nome che Windows gli da. Ordine dei controlli: cuffie e Bluetooth
    prima, perche' "Bluetooth Headphones" e' meglio trattato come cuffie e "Speakers (Realtek)" cade
    su altoparlante solo se nessun'altra parola chiave e' presente."""
    name = f" {(device_name or '').lower()} "
    for kind, keywords in _KEYWORDS:
        if any(keyword in name for keyword in keywords):
            return kind
    return "unknown"


def detect_output_device_name() -> str | None:
    """Nome dell'uscita audio predefinita di Windows (PortAudio), None se non rilevabile. Serve
    quando `voice_output_device` non e' configurato: senza, ogni uscita risultava "unknown" e il
    barge-in "auto" non riconosceva mai le cuffie."""
    try:
        import sounddevice as sd

        device = sd.query_devices(kind="output")
    except Exception:
        return None
    name = device.get("name") if isinstance(device, dict) else None
    return name if isinstance(name, str) and name.strip() else None


def profile_for_device(device_name: str | None) -> OutputProfile:
    return PROFILES[classify_output_device(device_name)]


def effective_parameters(style: SpeechStyle, profile: OutputProfile) -> tuple[float, int]:
    """(volume 0-1, variazione ritmo in %) finali: lo stile decide quanto piano/veloce, il
    dispositivo lo corregge. Il volume non supera mai 1.0 e non scende sotto 0.05: un sussurro
    silenzioso del tutto non e' un sussurro, e' un guasto."""
    volume = min(1.0, max(0.05, style.volume * profile.volume_scale))
    rate = max(-50, min(50, style.rate_delta_percent + profile.rate_delta_percent))
    return round(volume, 3), rate


def apply_to_provider(provider, style: SpeechStyle, device_name: str | None) -> bool:
    """Passa volume e ritmo al provider TTS. False se il provider non li sa applicare (le
    risposte restano corrette, solo senza regolazione)."""
    volume, rate = effective_parameters(style, profile_for_device(device_name))
    setter = getattr(provider, "set_speech_params", None)
    if setter is None:
        return False
    return bool(setter(volume, rate))
