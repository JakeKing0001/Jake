"""Approvazione di un pairing companion (F7.1.2, Companion Mobile MVP): l'ESECUZIONE del "sì" gia'
detto/scritto dall'utente sul canale locale, non la richiesta di conferma (che JakeCore costruisce
direttamente - vedi `core/jake_core.py::_on_pairing_requested`)."""
from core.skill_result import SkillResult
from core.sync_crypto import PublicKeys, SyncCryptoError


class ApprovePairingSkill:
    metadata = {
        "intent": "APPROVE_PAIRING",
        "description": "Autorizza un nuovo dispositivo companion a connettersi a Jake, dopo che l'utente ha "
        "confermato esplicitamente la richiesta di pairing sul PC.",
        "parameters": {
            "challenge_id": {"type": "string", "required": True},
            "requested_name": {"type": "string", "required": False},
            "sync_public_key": {"type": "object", "required": False},
        },
    }

    def __init__(self, pairing_service, sync_keyring=None) -> None:
        self.pairing_service = pairing_service
        self.sync_keyring = sync_keyring

    def execute(self, parameters: dict = None) -> SkillResult:
        parameters = parameters or {}
        challenge_id = (parameters.get("challenge_id") or "").strip()
        if not challenge_id:
            return SkillResult(success=False, data={}, error="MISSING_CHALLENGE_ID")
        credential = self.pairing_service.approve(challenge_id, device_name=parameters.get("requested_name") or "")
        if credential is None:
            return SkillResult(success=False, data={}, error="PAIRING_EXPIRED_OR_UNKNOWN")
        # F7.6 (chiude un gap dichiarato: "scambio delle chiavi pubbliche dentro il pairing"): se il companion ha
        # gia' mandato le proprie chiavi di sincronizzazione, il pairing e' anche il momento in cui entrano nel
        # portachiavi - nessun profilo concesso di default (F7.6.4: un dispositivo riceve solo cio' per cui e'
        # esplicitamente autorizzato, mai per il solo fatto di essere appena stato accoppiato). Una chiave
        # malformata non deve MAI far fallire il pairing gia' riuscito: la sincronizzazione resta un passo
        # successivo, opzionale, che l'utente puo' comunque completare piu' tardi.
        sync_registered = False
        raw_key = parameters.get("sync_public_key")
        if raw_key and self.sync_keyring is not None:
            try:
                self.sync_keyring.add(credential.device_id, PublicKeys.from_dict(raw_key), profiles=set())
                sync_registered = True
            except (SyncCryptoError, KeyError, TypeError, ValueError):
                pass
        return SkillResult(success=True, data={"device_id": credential.device_id, "sync_registered": sync_registered})
