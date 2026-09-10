"""Registro dispositivi e passaggio di consegne (v5.9, Ambient Computing): tiene traccia di
quale dispositivo - la sessione vocale locale, un HUD nativo, un'app companion su un altro
dispositivo (v5.8) - e' "attivo" in questo momento, quello che deve rispondere o parlare adesso,
e gestisce l'handoff quando un altro dispositivo reclama la sessione (es. ci si sposta dal PC al
telefono). Pura logica in memoria: niente rete qui, la usa core/companion_server.py."""


class DeviceRegistry:
    def __init__(self):
        self._active_device_id: str | None = None
        self._known_devices: dict[str, dict] = {}

    def register(self, device_id: str, name: str = "") -> None:
        # claim() chiama register() a ogni richiesta (core/companion_server.py::_handle_claim),
        # col nome che il client manda in quel momento: prima di questa correzione setdefault()
        # fissava il nome alla PRIMA registrazione per sempre, ignorando silenziosamente un nome
        # diverso su un claim successivo (es. l'utente rinomina il dispositivo nell'app
        # companion) - riprodotto per davvero: due claim dello stesso device_id con nomi diversi
        # lasciavano list_devices() a mostrare per sempre il primo nome. Un nome vuoto su un
        # claim successivo non cancella pero' un nome gia' noto: significa solo che quella
        # richiesta non ne ha mandato uno, non che l'utente lo abbia tolto.
        existing = self._known_devices.setdefault(device_id, {"name": name})
        if name and existing.get("name") != name:
            existing["name"] = name

    def claim(self, device_id: str, name: str = "") -> str | None:
        """device_id diventa il dispositivo attivo. Restituisce l'id del dispositivo
        precedentemente attivo (da avvisare dell'handoff, vedi EventType.DEVICE_HANDOFF), o None
        se non c'era nessuno attivo o era gia' lui stesso a reclamare di nuovo."""
        self.register(device_id, name)
        previous = self._active_device_id
        self._active_device_id = device_id
        return previous if previous != device_id else None

    def release(self, device_id: str) -> bool:
        """Il dispositivo rinuncia a essere quello attivo (es. l'app companion va in background).
        Vero se davvero era lui quello attivo."""
        if self._active_device_id == device_id:
            self._active_device_id = None
            return True
        return False

    @property
    def active_device_id(self) -> str | None:
        return self._active_device_id

    def list_devices(self) -> list[dict]:
        return [
            {"id": device_id, "name": info.get("name", ""), "active": device_id == self._active_device_id}
            for device_id, info in self._known_devices.items()
        ]
