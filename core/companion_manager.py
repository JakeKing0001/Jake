"""Companion e HUD nativo: pairing, continuita' della sessione fra dispositivi e credenziale dell'HUD (F7).

Estratto da JakeCore (3.2 Reliability & Architecture): metodi spostati alla lettera, comportamento
invariato. Lavorano sullo stato di JakeCore tramite self, come prima."""
from __future__ import annotations

from pathlib import Path

from core.companion_guard import is_loopback_host
from core.request_context import current_conversation_channel
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.native_hud import NativeHudSupervisor


class CompanionMixin:
    def hud_presentation_command(self, text: str) -> str | None:
        """La modalita' (expanded|mini|hidden) se `text` e' ESATTAMENTE una frase di SET_HUD_PRESENTATION, altrimenti
        None. Solo la corsia esatta: la sessione vocale lo chiede prima di riportare grande l'HUD alla wake word, cosi'
        "Jake, rimpicciolisciti" detto da mini non passa per un fotogramma da grande."""
        try:
            example = self.example_store.find_exact(self.normalizer.normalize(text or ""))
        except Exception:
            return None
        if example is None or example.intent != "SET_HUD_PRESENTATION":
            return None
        mode = (example.parameters or {}).get("mode")
        return mode if isinstance(mode, str) else None

    native_hud: NativeHudSupervisor | None

    def _on_pairing_requested(self, challenge, requested_name: str, sync_public_key: dict | None) -> None:
        """F7.1.2 (Companion Mobile MVP): il lato "conferma sul PC" del pairing - collegato da
        core/companion_server.py::_handle_pairing_start subito dopo aver aperto la challenge.
        Imposta una richiesta di conferma sul canale LOCALE (voce/CLI: current_device_id() e'
        sempre None sul thread che gestisce /pairing/start, mai impostato per quell'endpoint -
        vedi core/companion_guard.py::EndpointClass.PAIRING) con lo STESSO meccanismo gia' usato
        per ogni altra azione ADMIN (_handle_confirmation/_finalize_pending_action): un "si'" (e
        la passphrase, se ne e' stata configurata una - APPROVE_PAIRING e' classificato ADMIN in
        core/risk.py) autorizza per davvero. Nessun dispositivo nasce da solo: se l'utente non e'
        li' a leggere, la challenge scade da sola dopo 5 minuti (core/pairing_service.py) - questo
        metodo non riprova ne' attende, e' chiamato una volta sola per richiesta.

        "text" e' deliberatamente VUOTO (a differenza di ogni altra azione che la popola per
        l'apprendimento, vedi _finalize_pending_action): imparare "pairing di X" come comando
        insegnato riprodurrebbe un challenge_id ormai scaduto/consumato, inutile e fuorviante."""
        label = (requested_name or "").strip() or "sconosciuto"
        self.conversation_state.set_pending_action({
            "intent": "APPROVE_PAIRING",
            "parameters": {
                "challenge_id": challenge.challenge_id, "requested_name": requested_name,
                "sync_public_key": sync_public_key,
            },
            "reason": "confirmation_required",
            "text": "",
        })
        message = self.notify(
            "pairing",
            f"Un nuovo dispositivo '{label}' chiede di collegarsi a Jake. Autorizzi il pairing? (scade tra 5 minuti)",
        )
        if message is None:
            return
        print(f"\nJake > {message}\nTu > ", end="", flush=True)

    CONTINUITY_TURNS = 6

    def _pc_takes_the_session(self) -> None:
        if current_conversation_channel() is not None:
            return   # un telefono che parla resta il suo canale: nessuna elezione (voce, CLI e HUD sono il PC)
        server = getattr(self, "companion_server", None)
        if server is None or not getattr(server, "running", False):
            return
        try:
            server.pc_takes_the_session()
        except Exception:
            self.logger.exception("Errore riportando la sessione al PC")

    def _continuity_snapshot(self) -> dict:
        """F7.4.8: gli ultimi scambi della conversazione (unica per tutti i dispositivi) per chi prende la sessione dal
        PC: sul telefono si riparte da dove si era, senza rispiegare. In modalita' privata nulla: lo scambio privato non
        esce verso companion e HUD (v4.9.1)."""
        if self.private_mode:
            return {"recent": []}
        turns = self.conversation_state.get_short_term_history()[-self.CONTINUITY_TURNS:]
        return {"recent": [{"role": turn.get("role"), "text": turn.get("text", "")} for turn in turns]}

    def _turn_device_label(self) -> str | None:
        """Il nome del dispositivo companion da cui arriva il turno (dal registro, altrimenti l'id); None per il PC."""
        channel = current_conversation_channel()
        if channel is None:
            return None
        server = getattr(self, "companion_server", None)
        try:
            for device in server.devices.list_devices() if server is not None else []:
                if isinstance(device, dict) and device.get("id") == channel and device.get("name"):
                    return str(device["name"])
        except Exception:
            self.logger.exception("Errore leggendo il nome del dispositivo")
        return channel

    def _start_native_hud(self, config: dict, companion_host: str, companion_tls_context) -> None:

        from core.native_hud import DEFAULT_EXE, NativeHudSupervisor

        if not bool(config.get("companion_server_enabled", False)):
            self.logger.warning("hud_native_enabled richiede companion_server_enabled: HUD nativo non avviato")
            return
        if companion_tls_context is not None or not is_loopback_host(companion_host):
            self.logger.warning("HUD nativo non avviato: supporta solo il companion su loopback senza TLS")
            return
        port = int(getattr(self.companion_server, "port", 0) or config.get("companion_server_port", 8765) or 8765)
        exe = Path(config.get("hud_native_path") or DEFAULT_EXE)
        credentials = self._provision_native_hud_credential()
        self.native_hud = NativeHudSupervisor(exe, f"http://127.0.0.1:{port}", credentials=credentials,
                                              on_gave_up=self._native_hud_gave_up)
        if not self.native_hud.start():
            self.native_hud = None
            self._revoke_native_hud_credential()

    def _native_hud_gave_up(self, crashes: int) -> None:
        """F4.8: l'HUD nativo continua a chiudersi e non viene piu' riavviato. Prima lo diceva solo il log: l'HUD
        spariva senza spiegazione e la sua credenziale restava valida. Ora la credenziale si revoca (nessuno la usa
        piu') e l'utente lo sa dalla stessa strada delle altre notifiche."""
        self._revoke_native_hud_credential()
        self.present_notification(
            "advisory", f"L'HUD si è chiuso in modo anomalo {crashes} volte in pochi minuti: lo lascio spento. "
                        "Jake continua a funzionare; i dettagli sono nel log.")

    def _provision_native_hud_credential(self) -> dict | None:
        """L'HUD nativo e' un client companion come un altro: si autentica con una credenziale
        per-dispositivo (F7), mai con un'eccezione per localhost. Ruotata a ogni avvio del core,
        capability minime (vedere lo stato e mandare comandi; niente approval, file o audio),
        revocata allo shutdown. Il token esiste in chiaro solo qui e nel processo dell'HUD."""
        from core.companion_guard import EndpointClass
        from core.native_hud import NATIVE_HUD_CREDENTIAL_TTL_S, NATIVE_HUD_DEVICE_ID

        store = getattr(self.companion_server, "credential_store", None)
        if store is None:
            return None  # nessuna autenticazione per-dispositivo in uso: non serve una credenziale
        store.register_device(NATIVE_HUD_DEVICE_ID, "HUD nativo (questo PC)")
        credential = store.issue_credential(NATIVE_HUD_DEVICE_ID, ttl_seconds=NATIVE_HUD_CREDENTIAL_TTL_S)
        self.companion_server.guard.set_capabilities(
            NATIVE_HUD_DEVICE_ID, {EndpointClass.READ_ONLY, EndpointClass.COMMAND},
        )
        return {"device_id": NATIVE_HUD_DEVICE_ID, "token": credential.token}

    def _revoke_native_hud_credential(self) -> None:
        from core.native_hud import NATIVE_HUD_DEVICE_ID

        store = getattr(getattr(self, "companion_server", None), "credential_store", None)
        if store is None:
            return
        try:
            store.revoke(NATIVE_HUD_DEVICE_ID)
        except Exception:
            self.logger.exception("Errore revocando la credenziale dell'HUD nativo")
