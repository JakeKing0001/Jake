import json
import os
from pathlib import Path

from core.secrets_vault import is_protected, protect, unprotect

# Chiavi cifrate a riposo con DPAPI (F1, vedi core/secrets_vault.py) invece di restare in chiaro
# su config/settings.json: la passphrase admin e il token di un hub Home Assistant sono le uniche
# due credenziali vere che Config gestisce oggi. Aggiungere qui una nuova chiave la protegge
# automaticamente, sia in lettura (get) sia in scrittura (set) sia alla migrazione di un valore
# gia' salvato in chiaro da una versione precedente (_migrate_secrets).
SECRET_KEYS = {"admin_passphrase", "home_assistant_token"}


class Config:
    """Carica configurazione e credenziali di Jake da config/settings.json.

    Le variabili d'ambiente JAKE_<CHIAVE> hanno sempre la precedenza, cosi' le credenziali
    possono restare fuori dal file su disco quando serve (es. macchine condivise, CI): restano
    intenzionalmente in chiaro su quel canale, che e' gia' il modo per non scriverle affatto su
    disco - DPAPI protegge invece SECRET_KEYS quando finiscono comunque nel file."""

    DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.json"

    def __init__(self, path: Path = None):
        self.path = Path(path) if path else self.DEFAULT_PATH
        self._values = self._load()
        self._migrate_secrets()

    def _load(self) -> dict:
        if self.path.is_file():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _migrate_secrets(self) -> None:
        """Cifra sul posto qualunque valore SECRET_KEYS trovato ancora in chiaro (F1: "migrazione
        sicura dei token gia' salvati") - una tantum, silenziosa, alla prima apertura dopo
        l'aggiornamento: chi aveva gia' admin_passphrase/home_assistant_token in chiaro da una
        versione precedente di Jake non deve fare nulla, e il file su disco smette di contenerli
        in chiaro dal prossimo avvio."""
        changed = False
        for key in SECRET_KEYS:
            value = self._values.get(key)
            if value and not is_protected(value):
                self._values[key] = protect(value)
                changed = True
        if changed:
            self._write()

    def get(self, key: str, default=None):
        env_value = os.environ.get(f"JAKE_{key.upper()}")
        if env_value:
            return env_value
        # Solo l'assenza della chiave deve far scattare il default: un valore memorizzato ma
        # "falsy" (False, 0, "", []) e' comunque una scelta esplicita dell'utente e va rispettata,
        # non silenziosamente scartata.
        value = self._values.get(key, default)
        if key in SECRET_KEYS and isinstance(value, str):
            value = unprotect(value)
        return value

    def set(self, key: str, value) -> None:
        """Aggiorna un valore e lo persiste su config/settings.json (v2.0: modelli
        intercambiabili, tra gli usi). Le variabili d'ambiente restano prioritarie in get()."""
        if key in SECRET_KEYS and value:
            value = protect(value)
        self._values[key] = value
        self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._values, indent=2, ensure_ascii=False), encoding="utf-8")
