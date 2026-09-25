"""Arruolamento vocale guidato da terminale (F2.7.1): `python -m core.voice.speaker_enroll --id davide --name Davide`.

- consenso esplicito scritto ("CONSENTO"), mai implicito: senza, nulla viene registrato ne' creato;
- id validato PRIMA di registrare (stesse regole di `ProfileManager`): un id rifiutato dopo aver gia'
  salvato l'impronta lascerebbe un profilo vocale senza profilo di memoria;
- almeno `MIN_ENROLL_SAMPLES` frasi valide, con un numero limitato di tentativi per frase (un
  microfono muto non deve bloccare il terminale per sempre);
- l'audio resta in RAM il tempo di calcolarne l'impronta: su disco finisce solo quella.

Il riconoscimento serve a SCEGLIERE il profilo (memoria, cronologia, preferenze), mai ad autorizzare:
vedi `core/profiles.py::ProfileNamespace.needs_authentication`."""
import argparse
from collections.abc import Callable

import numpy as np

from core.profiles import ProfileError, ProfileManager
from core.voice.speaker_profile import (
    MIN_ENROLL_SAMPLES,
    SAMPLE_RATE,
    SpeakerProfile,
    SpeakerProfileStore,
    extract_features,
)

PHRASES = [
    "Jake, che ore sono?",
    "Apri Spotify e metti un po' di musica.",
    "Ricordami di controllare il progetto domani.",
    "Quanto fa diciassette per ventitré?",
    "Jake, spiegami cosa stai facendo.",
]
CONSENT_WORD = "CONSENTO"
MAX_ATTEMPTS_PER_PHRASE = 3


class EnrollmentAborted(Exception):
    """L'arruolamento non e' avvenuto: nessuna impronta ne' profilo sono stati salvati."""


def record_phrase(text: str, seconds: float = 4.0, prompt: Callable[[str], str] = input) -> np.ndarray:
    import sounddevice as sd

    print()
    print(f'Pronuncia: "{text}"')
    prompt("Premi INVIO quando sei pronto...")
    print("Parla...")
    audio = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    print("Registrato.")
    return audio.reshape(-1)


def enroll_speaker(
    profile_id: str,
    display_name: str,
    *,
    prompt: Callable[[str], str] = input,
    record: Callable[[str], np.ndarray] | None = None,
    store: SpeakerProfileStore | None = None,
    manager: ProfileManager | None = None,
    phrases: list[str] = PHRASES,
    max_attempts: int = MAX_ATTEMPTS_PER_PHRASE,
) -> SpeakerProfile:
    manager = manager or ProfileManager()
    try:
        manager.validate_id(profile_id)
    except ProfileError as exc:
        raise EnrollmentAborted(str(exc)) from None
    if not display_name.strip():
        raise EnrollmentAborted("serve un nome")

    print("Jake userà brevi registrazioni della tua voce per creare un'impronta numerica locale.")
    print("L'audio grezzo non viene salvato. Il riconoscimento della voce non è un'autenticazione.")
    if prompt(f'Se acconsenti, scrivi esattamente "{CONSENT_WORD}": ').strip() != CONSENT_WORD:
        raise EnrollmentAborted("consenso non dato")

    recorder = record or (lambda text: record_phrase(text, prompt=prompt))
    features = []
    for phrase in phrases:
        for _attempt in range(max_attempts):
            fingerprint = extract_features(recorder(phrase))
            if fingerprint is not None:
                features.append(fingerprint)
                break
            print("Campione non valido (silenzio o rumore). Riproviamo.")
    if len(features) < MIN_ENROLL_SAMPLES:
        raise EnrollmentAborted(
            f"solo {len(features)} campioni validi su {MIN_ENROLL_SAMPLES} necessari: controlla il microfono"
        )

    profile = (store or SpeakerProfileStore()).enroll(profile_id, display_name, features, consent=True)
    if profile_id not in manager.profile_ids():
        manager.create_profile(profile_id, display_name)
    return profile


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Registra un profilo vocale per Jake.")
    parser.add_argument("--id", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    try:
        profile = enroll_speaker(args.id, args.name)
    except EnrollmentAborted as exc:
        print(f"Arruolamento annullato: {exc}.")
        return 1
    print()
    print(f"Profilo vocale '{profile.display_name}' creato con {profile.samples} campioni.")
    print("Nessuna registrazione audio è stata conservata.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
