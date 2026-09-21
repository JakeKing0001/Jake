import sys

from core.jake_core import JakeCore
from core.version import PROTOCOL_VERSION, VERSION

USAGE = f"""Jake {VERSION} - assistente personale locale

  python main.py                 modalita' Jarvis (default): HUD in vetro + tray + voce continua
  python main.py --no-voice      solo HUD e barra comandi (Ctrl+Shift+J), senza microfono
  python main.py --cli           modalita' testo nel terminale
  python main.py --voice         push-to-talk nel terminale (tieni premuto F9)
  python main.py --voice --wake-word   voce continua nel terminale, senza HUD
  python main.py --tray          vecchia icona tray con pannello Tk (v1.1)
  python main.py --version       versione di prodotto e protocollo companion/HUD
  opzioni: --character jake      voce del personaggio via RVC (se installata)
"""


def _force_utf8_console() -> None:
    """La console di Windows usa cp1252: stampare una risposta con caratteri come «» o → farebbe
    crashare la modalita' testo con UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main():
    _force_utf8_console()
    # Prima di questo fix, un errore alla costruzione di JakeCore (plugin rotto, config
    # corrotta, ecc.) o dentro la sessione non veniva mai intercettato: in modalita' HUD/tray
    # non c'e' una console visibile (avvio da collegamento/avvio automatico), quindi il
    # processo spariva nel nulla senza che l'utente vedesse nulla.
    try:
        _dispatch()
    except Exception:
        _report_fatal_error(is_gui=not ("--cli" in sys.argv or "--voice" in sys.argv))


def _dispatch():
    if "--version" in sys.argv:
        print(f"Jake {VERSION} (protocollo {PROTOCOL_VERSION})")
        return
    if "--help" in sys.argv or "-h" in sys.argv:
        print(USAGE)
        return
    if "--cli" in sys.argv or "--text" in sys.argv:
        run_cli_mode()
        return
    if "--voice" in sys.argv:
        run_voice_mode()
        return
    if "--tray" in sys.argv:
        run_tray_mode()
        return
    run_jarvis_mode(with_voice="--no-voice" not in sys.argv)


def run_cli_mode():
    print(f"Jake {VERSION} avviato (modalita' testo). Scrivi 'esci' per chiudere.")
    core = JakeCore()
    while True:
        try:
            user_text = input("Tu > ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        answer = core.answer(user_text)
        if answer == JakeCore.EXIT_SENTINEL:
            print("Jake > Chiusura...")
            break
        print(f"Jake > {answer}")
    core.shutdown()


def _report_fatal_error(is_gui: bool) -> None:
    from core.logger import get_logger

    message = "Jake si e' chiuso per un errore imprevisto all'avvio. Dettagli nel log (data/jake.log)."
    get_logger().exception("Errore fatale all'avvio di Jake")
    print(message)

    if is_gui:
        # Senza console ad ascoltare il print sopra, un messagebox nativo di Windows (nessuna
        # dipendenza aggiuntiva, ctypes e' nella libreria standard) e' l'unico modo per far
        # arrivare comunque il messaggio all'utente.
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "Jake", 0x10)
        except Exception:
            pass


def _build_offline_tts_provider():
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


def _build_tts_provider(config=None):
    """v3.0: voce neurale Edge (online) con ripiego automatico sulla voce offline. Con
    "tts_engine": "offline" in settings.json si usa direttamente la voce di Windows."""
    offline = _build_offline_tts_provider()
    engine = (config.get("tts_engine", "edge") if config else "edge") or "edge"
    if str(engine).lower() != "edge":
        return offline
    try:
        from core.voice.edge_tts_provider import EdgeTtsProvider
        voice = (config.get("tts_voice") if config else None) or "it-IT-DiegoNeural"
        rate = (config.get("tts_rate") if config else None) or "+8%"
        return EdgeTtsProvider(voice=voice, rate=rate, fallback=offline)
    except ImportError:
        print("Nota: pacchetto 'edge-tts' non installato, uso la voce offline di Windows.")
        return offline


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

    # F2.5.6: clonare un timbro richiede un consenso scritto e revocabile, registrato da un umano
    # da terminale (mai da Jake). Senza, si parla con la voce normale e si dice come sbloccarla.
    from core.voice.voice_consent import VoiceConsentRegistry

    consent = VoiceConsentRegistry()
    if not consent.is_allowed(character_name):
        print(
            f"Nota: manca il consenso alla clonazione vocale per '{character_name}', uso la voce normale. "
            f"Per registrarlo: python -m core.voice.voice_consent grant {character_name} --subject \"CHI\" "
            "--basis fictional-character --statement \"...\""
        )
        return base_tts_provider, None

    print(f"Avvio la conversione vocale per '{character_name}' (puo' richiedere qualche secondo)...")
    provider = CharacterTtsProvider(
        base_tts_provider, manager, consent_check=lambda: consent.is_allowed(character_name),
    )
    return provider, manager


def _setup_voice(core=None):
    """Prepara TTS/STT (ed eventualmente la voce di personaggio) condivisi da push-to-talk,
    voce continua e HUD. Restituisce (tts_provider, stt_provider, server_manager_o_None)."""
    from core.voice.stt_provider import WhisperSttProvider

    config = core.config if core is not None else None
    tts_provider = _build_tts_provider(config)
    if not getattr(tts_provider, "matched_preferred_gender", True):
        print(
            "Nota: nessuna voce italiana maschile installata su questo PC, uso la voce italiana "
            "disponibile. Per aggiungerne una: Impostazioni > Ora e lingua > Voce > Aggiungi voci."
        )

    server_manager = None
    character_name = _character_name_from_args()
    if character_name:
        tts_provider, server_manager = _build_character_tts_provider(tts_provider, character_name)

    print("Carico il modello vocale locale (puo' richiedere un download al primo avvio)...")
    hotwords = core.skill_registry.app_names()[:80] if core is not None else None
    stt_provider = WhisperSttProvider(
        model_size=(config.get("stt_model") or None) if config else None,
        device=(config.get("stt_device") or None) if config else None,
        hotwords=hotwords,
    )
    print(f"Riconoscimento vocale: {stt_provider.model_size} su {stt_provider.device}.")
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
    tts_provider, stt_provider, server_manager = _setup_voice(core)

    session = PushToTalkSession(core, stt_provider, tts_provider)
    try:
        session.run()
    finally:
        if server_manager is not None:
            server_manager.stop()
        core.shutdown()


def run_wake_word_mode():
    try:
        from core.voice.wake_word_session import WakeWordSession
    except ImportError as exc:
        print(f"Dipendenze voce continua mancanti ({exc}). Installa i pacchetti in requirements.txt.")
        return

    core = JakeCore()
    tts_provider, stt_provider, server_manager = _setup_voice(core)

    session = WakeWordSession(
        core, stt_provider, tts_provider, follow_up_seconds=float(core.config.get("follow_up_seconds", 6)),
        replay_window_seconds=float(core.config.get("voice_replay_guard_seconds", 0)),
        speech_style=str(core.config.get("voice_style", "normal") or "normal"),
        output_device_name=core.config.get("voice_output_device") or None,
        barge_in=str(core.config.get("voice_barge_in", "off") or "off"),
        partials=str(core.config.get("voice_partials", "auto") or "auto"),
    )
    try:
        session.run()
    finally:
        if server_manager is not None:
            server_manager.stop()
        core.shutdown()


def run_tray_mode():
    try:
        from core.gui.tray_app import run_tray
    except ImportError as exc:
        print(f"Dipendenze GUI mancanti ({exc}). Installa i pacchetti in requirements.txt.")
        return

    core = JakeCore()
    run_tray(core)


def run_jarvis_mode(with_voice: bool = True):
    """v3.0: HUD in vetro + tray + voce continua. Se PySide6 manca, ripiega sulla voce
    continua da terminale; se manca il microfono o le dipendenze vocali, resta l'HUD testuale."""
    from core.win_dpi import ensure_dpi_aware

    ensure_dpi_aware()
    try:
        from core.gui.hud.app import JarvisApp
    except ImportError as exc:
        print(f"PySide6 non disponibile ({exc}): avvio la voce continua da terminale.")
        run_wake_word_mode()
        return

    core = JakeCore()
    session = None
    server_manager = None
    if with_voice:
        try:
            from core.voice.wake_word_session import WakeWordSession

            tts_provider, stt_provider, server_manager = _setup_voice(core)
            session = WakeWordSession(
                core, stt_provider, tts_provider,
                follow_up_seconds=float(core.config.get("follow_up_seconds", 6)),
                replay_window_seconds=float(core.config.get("voice_replay_guard_seconds", 0)),
                speech_style=str(core.config.get("voice_style", "normal") or "normal"),
                output_device_name=core.config.get("voice_output_device") or None,
                barge_in=str(core.config.get("voice_barge_in", "off") or "off"),
                partials=str(core.config.get("voice_partials", "auto") or "auto"),
            )
            if not session.vad_listener.is_available():
                print("Nessun microfono: HUD in modalita' solo testo.")
                session = None
        except Exception:
            core.logger.exception("Voce non disponibile: HUD in modalita' solo testo")
            print("Voce non disponibile (vedi data/jake.log): HUD in modalita' solo testo.")
            session = None

    quick_actions = core.config.get("hud_quick_actions") or None
    if isinstance(quick_actions, list):
        quick_actions = [(item.get("label"), item.get("command")) for item in quick_actions
                         if isinstance(item, dict) and item.get("label") and item.get("command")] or None
    app = JarvisApp(
        core, session,
        hotkey=core.config.get("hud_hotkey", "ctrl+shift+j") or "ctrl+shift+j",
        auto_hide_seconds=float(core.config.get("hud_auto_hide_seconds", 6)),
        mode=str(core.config.get("hud_mode", "full") or "full"),
        backdrop=str(core.config.get("hud_backdrop", "clear") or "clear"),
        quick_actions=quick_actions,
    )
    try:
        app.run()
    finally:
        if server_manager is not None:
            server_manager.stop()


if __name__ == "__main__":
    main()
