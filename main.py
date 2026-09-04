import sys

from core.jake_core import JakeCore


def main():
    if "--voice" in sys.argv:
        run_voice_mode()
        return

    if "--tray" in sys.argv:
        run_tray_mode()
        return

    print("Jake avviato.")

    core = JakeCore()

    while True:
        answer = core.answer(input("Tu > "))
        if answer == JakeCore.EXIT_SENTINEL:
            print("Jake > Chiusura...")
            exit()

        print(answer)


def _build_tts_provider():
    """Preferisce le voci OneCore (winsdk): pyttsx3/SAPI5 vede solo le voci "Desktop" classiche
    e su molti PC le voci aggiuntive, spesso maschili, sono registrate solo come OneCore."""
    try:
        from core.voice.onecore_tts_provider import OneCoreTtsProvider
        provider = OneCoreTtsProvider(preferred_gender="male")
        if provider.voice is not None:
            return provider
    except Exception:
        pass

    from core.voice.tts_provider import Pyttsx3TtsProvider
    return Pyttsx3TtsProvider(preferred_gender="male")


# Alias comodi per --character: il nome vero e' quello della sottocartella in rvc_models/.
CHARACTER_ALIASES = {"jake": "jake_the_dog", "jake_the_dog": "jake_the_dog"}


def _character_name_from_args() -> str | None:
    if "--character" not in sys.argv:
        return None
    index = sys.argv.index("--character")
    if index + 1 >= len(sys.argv):
        return None
    raw_name = sys.argv[index + 1]
    return CHARACTER_ALIASES.get(raw_name, raw_name)


def _build_character_tts_provider(base_tts_provider, character_name: str):
    from core.voice.character_tts_provider import CharacterTtsProvider
    from core.voice.rvc_server_manager import RvcServerManager

    manager = RvcServerManager(model_name=character_name)
    if not manager.is_installed():
        print(
            f"Nota: il modello vocale '{character_name}' non è installato in rvc_models/, "
            "uso la voce normale."
        )
        return base_tts_provider, None

    print(f"Avvio la conversione vocale per '{character_name}' (puo' richiedere qualche secondo)...")
    provider = CharacterTtsProvider(base_tts_provider, manager)
    return provider, manager


def _setup_voice():
    """Prepara TTS/STT (ed eventualmente la voce di personaggio) condivisi da push-to-talk
    e voce continua. Restituisce (tts_provider, stt_provider, server_manager_o_None)."""
    from core.voice.stt_provider import WhisperSttProvider

    tts_provider = _build_tts_provider()
    if not tts_provider.matched_preferred_gender:
        print(
            "Nota: nessuna voce italiana maschile installata su questo PC, uso la voce italiana "
            "disponibile. Per aggiungerne una: Impostazioni > Ora e lingua > Voce > Aggiungi voci."
        )

    server_manager = None
    character_name = _character_name_from_args()
    if character_name:
        tts_provider, server_manager = _build_character_tts_provider(tts_provider, character_name)

    print("Carico il modello vocale locale (puo' richiedere un download al primo avvio)...")
    stt_provider = WhisperSttProvider()
    return tts_provider, stt_provider, server_manager


def run_voice_mode():
    try:
        from core.voice.push_to_talk import PushToTalkSession
    except ImportError as exc:
        print(f"Dipendenze voce mancanti ({exc}). Installa i pacchetti in requirements.txt.")
        return

    if "--wake-word" in sys.argv:
        run_wake_word_mode()
        return

    core = JakeCore()
    tts_provider, stt_provider, server_manager = _setup_voice()

    session = PushToTalkSession(core, stt_provider, tts_provider)
    try:
        session.run()
    finally:
        if server_manager is not None:
            server_manager.stop()


def run_wake_word_mode():
    try:
        from core.voice.wake_word_session import WakeWordSession
    except ImportError as exc:
        print(f"Dipendenze voce continua mancanti ({exc}). Installa i pacchetti in requirements.txt.")
        return

    core = JakeCore()
    tts_provider, stt_provider, server_manager = _setup_voice()

    session = WakeWordSession(core, stt_provider, tts_provider)
    try:
        session.run()
    finally:
        if server_manager is not None:
            server_manager.stop()


def run_tray_mode():
    try:
        from core.gui.tray_app import run_tray
    except ImportError as exc:
        print(f"Dipendenze GUI mancanti ({exc}). Installa i pacchetti in requirements.txt.")
        return

    core = JakeCore()
    run_tray(core)


if __name__ == "__main__":
    main()