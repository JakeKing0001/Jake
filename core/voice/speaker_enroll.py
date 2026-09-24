import argparse

import sounddevice as sd

from core.profiles import ProfileManager
from core.voice.speaker_profile import (
    SAMPLE_RATE,
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


def record_phrase(text: str, seconds: float = 4.0):
    print()
    print(f'Pronuncia: "{text}"')
    input("Premi INVIO quando sei pronto...")

    print("Parla...")
    audio = sd.rec(
        int(seconds * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
    )
    sd.wait()
    print("Registrato.")

    return audio.reshape(-1)


def main():
    parser = argparse.ArgumentParser(
        description="Registra un profilo vocale per Jake."
    )
    parser.add_argument("--id", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    print(
        "Jake userà brevi registrazioni della tua voce per creare "
        "un'impronta numerica locale."
    )
    print("L'audio grezzo non viene salvato.")

    consent = input(
        'Se acconsenti, scrivi esattamente "CONSENTO": '
    ).strip()

    if consent != "CONSENTO":
        print("Enrollment annullato.")
        return

    features = []

    for phrase in PHRASES:
        while True:
            audio = record_phrase(phrase)
            fingerprint = extract_features(audio)

            if fingerprint is not None:
                features.append(fingerprint)
                break

            print("Campione non valido. Riproviamo.")

    store = SpeakerProfileStore()

    profile = store.enroll(
        args.id,
        args.name,
        features,
        consent=True,
    )

    manager = ProfileManager()

    if args.id not in manager.profile_ids():
        manager.create_profile(args.id, args.name)

    print()
    print(
        f"Profilo vocale '{profile.display_name}' creato "
        f"con {profile.samples} campioni."
    )
    print("Nessuna registrazione audio è stata conservata.")


if __name__ == "__main__":
    main()