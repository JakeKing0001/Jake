"""F7.1.3: l'utente decide cosa puo' fare un dispositivo accoppiato ("il telefono puo' solo guardare"). Le capability
sono persistenti (DeviceCredentialStore) e valgono subito sugli endpoint del companion server. Rischio ADMIN: cambia
chi puo' comandare Jake da fuori, quindi la policy chiede autenticazione/conferma prima di arrivare qui."""
from core.skill_result import SkillResult

# livello detto dall'utente -> classi di endpoint concesse
LEVELS = {
    "sola lettura": ("read_only",),
    "lettura": ("read_only",),
    "guardare": ("read_only",),
    "comandi": ("read_only", "command"),
    "completo": ("read_only", "command", "approval"),
    "file": ("read_only", "command", "file"),              # F7.2.5: puo' anche inviare file al PC
}
PROTECTED_DEVICES = {"native-hud-local"}  # ha una credenziale propria, rigenerata a ogni avvio del core


class SetDeviceAccessSkill:
    metadata = {
        "intent": "SET_DEVICE_ACCESS",
        "description": "Imposta cosa puo' fare un dispositivo accoppiato: 'sola lettura' (vede stato ed eventi), 'comandi' (puo' anche mandare comandi) o 'completo' (anche approvare azioni).",
        "parameters": {
            "device": {"type": "string", "required": True, "description": "Nome del dispositivo accoppiato, come nell'elenco dei dispositivi."},
            "level": {"type": "string", "required": True, "description": "'sola lettura', 'comandi' o 'completo'."},
        },
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        wanted = str(parameters.get("device") or "").strip().lower()
        level = str(parameters.get("level") or "").strip().lower()
        classes = LEVELS.get(level)
        if not wanted or classes is None:
            return SkillResult(success=False, data={"levels": sorted(LEVELS)}, error="INVALID_PARAMETERS")
        server, device, error = _find_device(self.core, wanted)
        if error is not None:
            return error
        server.guard.set_capabilities(device.device_id, classes)
        return SkillResult(success=True, data={"device": device.name or device.device_id, "level": level,
                                               "classes": list(classes)})


def _find_device(core, wanted: str):
    """(server, dispositivo, None) oppure (None, None, SkillResult d'errore). Cerca per nome o id tra i dispositivi
    accoppiati; l'HUD nativo di questo PC non e' mai un bersaglio (ha la sua credenziale gestita dal core)."""
    server = getattr(core, "companion_server", None)
    store = getattr(server, "credential_store", None)
    if store is None:
        return None, None, SkillResult(success=False, data={}, error="COMPANION_UNAVAILABLE")
    matches = [d for d in store.list_devices()
               if d.device_id not in PROTECTED_DEVICES and (wanted in d.name.lower() or wanted == d.device_id.lower())]
    if not matches:
        return None, None, SkillResult(success=False, data={"device": wanted}, error="NOT_FOUND")
    if len(matches) > 1:
        return None, None, SkillResult(success=False, data={"candidates": [d.name or d.device_id for d in matches]},
                                       error="AMBIGUOUS")
    return server, matches[0], None


class RevokeDeviceSkill:
    """F7.2.7: "ho perso il telefono" - il dispositivo non puo' piu' fare nulla, subito: credenziale revocata (anche
    lo stream di eventi gia' aperto si chiude, core/companion_server.py) e chiavi di sincronizzazione tolte dal
    portachiavi. Per riaverlo serve un nuovo pairing. Le chiavi salvate SUL telefono le cancella l'app."""

    metadata = {
        "intent": "REVOKE_DEVICE",
        "description": (
            "Scollega e blocca subito un dispositivo accoppiato (telefono perso o rubato, dispositivo non piu' usato): "
            "non potra' piu' vedere ne' comandare nulla finche' non viene accoppiato di nuovo."
        ),
        "parameters": {
            "device": {"type": "string", "required": True, "description": "Nome del dispositivo accoppiato."},
        },
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        wanted = str((parameters or {}).get("device") or "").strip().lower()
        if not wanted:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        server, device, error = _find_device(self.core, wanted)
        if error is not None:
            return error
        revoked = server.credential_store.revoke(device.device_id)
        server.devices.release(device.device_id)  # non resta il dispositivo attivo (F7.4.3)
        keyring = getattr(self.core, "sync_keyring", None)
        keys_removed = bool(keyring.revoke(device.device_id)) if keyring is not None else False
        return SkillResult(success=True, data={"device": device.name or device.device_id, "revoked": revoked,
                                               "sync_keys_removed": keys_removed})
