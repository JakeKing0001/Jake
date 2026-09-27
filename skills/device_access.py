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
        server = getattr(self.core, "companion_server", None)
        store = getattr(server, "credential_store", None)
        if store is None:
            return SkillResult(success=False, data={}, error="COMPANION_UNAVAILABLE")
        matches = [d for d in store.list_devices()
                   if d.device_id not in PROTECTED_DEVICES and (wanted in d.name.lower() or wanted == d.device_id.lower())]
        if not matches:
            return SkillResult(success=False, data={"device": wanted}, error="NOT_FOUND")
        if len(matches) > 1:
            return SkillResult(success=False, data={"candidates": [d.name or d.device_id for d in matches]}, error="AMBIGUOUS")
        device = matches[0]
        server.guard.set_capabilities(device.device_id, classes)
        return SkillResult(success=True, data={"device": device.name or device.device_id, "level": level,
                                               "classes": list(classes)})
