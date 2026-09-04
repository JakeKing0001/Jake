import json
import os
from pathlib import Path


class Config:
    """Carica configurazione e credenziali di Jake da config/settings.json.

    Le variabili d'ambiente JAKE_<CHIAVE> hanno sempre la precedenza, cosi' le credenziali
    possono restare fuori dal file su disco quando serve (es. macchine condivise, CI)."""

    DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.json"

    def __init__(self, path: Path = None):
        self.path = Path(path) if path else self.DEFAULT_PATH
        self._values = self._load()

    def _load(self) -> dict:
        if self.path.is_file():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def get(self, key: str, default=None):
        env_value = os.environ.get(f"JAKE_{key.upper()}")
        if env_value:
            return env_value
        # Solo l'assenza della chiave deve far scattare il default: un valore memorizzato ma
        # "falsy" (False, 0, "", []) e' comunque una scelta esplicita dell'utente e va rispettata,
        # non silenziosamente scartata.
        return self._values.get(key, default)

    def set(self, key: str, value) -> None:
        """Aggiorna un valore e lo persiste su config/settings.json (v2.0: modelli
        intercambiabili, tra gli usi). Le variabili d'ambiente restano prioritarie in get()."""
        self._values[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._values, indent=2, ensure_ascii=False), encoding="utf-8")
