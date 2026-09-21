"""Profili utente separati e modalita' ospite (F2.7.4, F2.7.5).

Un profilo e' uno spazio dei nomi: cronologia della conversazione, database di memoria, preferenze
e tetto di rischio sono SUOI, non condivisi. Cio' che scrive il profilo A non e' visibile al profilo
B perche' vivono in oggetti e file diversi, non perche' un filtro lo nasconde.

Cosa un profilo NON puo' fare: concedersi un permesso. Il tetto di rischio (`max_risk`) puo' solo
RESTRINGERE cio' che il profilo puo' chiedere; le azioni DESTRUCTIVE e ADMIN richiedono comunque
`AuthGate` per QUALUNQUE profilo (`needs_authentication`), anche se la voce e' stata riconosciuta con
piena confidenza: la voce sceglie il profilo, non apre la serratura (vedi core/voice/speaker_profile.py).

L'ospite non lascia nulla: memoria in una cartella temporanea che sparisce alla chiusura, nessun
apprendimento dai suoi comandi, tetto LOCAL_REVERSIBLE."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from core.conversation_state import ConversationStateManager
from core.risk import RiskLevel, is_at_least
from core.voice.speaker_profile import SpeakerHint

DEFAULT_BASE_DIR = Path(__file__).resolve().parent.parent / "data" / "profiles"
_VALID_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
GUEST_ID = "guest"
_ALWAYS_AUTH = (RiskLevel.DESTRUCTIVE, RiskLevel.ADMIN)


class ProfileError(Exception):
    """Operazione su un profilo non valida (id non ammesso, profilo inesistente...)."""


@dataclass
class ProfileNamespace:
    profile_id: str
    display_name: str
    max_risk: RiskLevel
    persistent: bool
    learning_enabled: bool
    memory_db_path: Path
    conversation: ConversationStateManager = field(default_factory=ConversationStateManager)
    preferences: dict = field(default_factory=dict)

    def permits(self, risk: RiskLevel) -> bool:
        """Il tetto del profilo consente una richiesta di questo rischio? E' condizione NECESSARIA,
        non sufficiente: vedi `needs_authentication`."""
        return is_at_least(self.max_risk, risk)

    @staticmethod
    def needs_authentication(risk: RiskLevel) -> bool:
        """Vero per DESTRUCTIVE/ADMIN per qualunque profilo e qualunque riconoscimento vocale."""
        return risk in _ALWAYS_AUTH

    def open_memory(self):
        from core.memory_manager import MemoryManager

        return MemoryManager(self.memory_db_path)


class ProfileManager:
    def __init__(self, base_dir: Path = DEFAULT_BASE_DIR) -> None:
        self.base_dir = Path(base_dir)
        self._registry_path = self.base_dir / "profiles.json"
        self._namespaces: dict[str, ProfileNamespace] = {}
        self._guests: list[ProfileNamespace] = []

    # ---- archivio ---------------------------------------------------------------------------

    def _read(self) -> dict[str, dict]:
        if not self._registry_path.exists():
            return {}
        try:
            return json.loads(self._registry_path.read_text(encoding="utf-8")).get("profiles", {})
        except (OSError, ValueError):
            return {}

    def _write(self, profiles: dict[str, dict]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._registry_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "profiles": profiles}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._registry_path)

    @staticmethod
    def _check_id(profile_id: str) -> str:
        if not _VALID_ID.match(profile_id or ""):
            raise ProfileError("id profilo non valido: solo minuscole, cifre, '-' e '_' (max 32)")
        if profile_id == GUEST_ID:
            raise ProfileError(f"'{GUEST_ID}' e' riservato alla modalita' ospite")
        return profile_id

    # ---- profili persistenti -----------------------------------------------------------------

    def create_profile(self, profile_id: str, display_name: str, max_risk: RiskLevel = RiskLevel.ADMIN) -> ProfileNamespace:
        profile_id = self._check_id(profile_id)
        if not display_name.strip():
            raise ProfileError("serve un nome")
        profiles = self._read()
        if profile_id in profiles:
            raise ProfileError(f"il profilo '{profile_id}' esiste gia'")
        profiles[profile_id] = {"display_name": display_name.strip(), "max_risk": max_risk.value}
        (self.base_dir / profile_id).mkdir(parents=True, exist_ok=True)
        self._write(profiles)
        return self.namespace(profile_id)

    def profile_ids(self) -> list[str]:
        return list(self._read())

    def namespace(self, profile_id: str) -> ProfileNamespace:
        cached = self._namespaces.get(profile_id)
        if cached is not None:
            return cached
        entry = self._read().get(self._check_id(profile_id))
        if entry is None:
            raise ProfileError(f"profilo '{profile_id}' inesistente")
        namespace = ProfileNamespace(
            profile_id, entry["display_name"], RiskLevel(entry["max_risk"]), persistent=True, learning_enabled=True,
            memory_db_path=self.base_dir / profile_id / "jake_memory.db",
        )
        self._namespaces[profile_id] = namespace
        return namespace

    def delete_profile(self, profile_id: str) -> bool:
        """Cancella il profilo E i suoi dati (memoria, cronologia): non una disattivazione."""
        profile_id = self._check_id(profile_id)
        profiles = self._read()
        if profile_id not in profiles:
            return False
        del profiles[profile_id]
        self._namespaces.pop(profile_id, None)
        shutil.rmtree(self.base_dir / profile_id, ignore_errors=True)
        self._write(profiles)
        return True

    # ---- ospite ------------------------------------------------------------------------------

    def guest(self) -> ProfileNamespace:
        """Un profilo ospite NUOVO: memoria in una cartella temporanea, niente apprendimento, tetto
        LOCAL_REVERSIBLE. Ogni chiamata ne crea uno indipendente: due ospiti non condividono nulla."""
        workdir = Path(tempfile.mkdtemp(prefix="jake-guest-"))
        namespace = ProfileNamespace(
            GUEST_ID, "Ospite", RiskLevel.LOCAL_REVERSIBLE, persistent=False, learning_enabled=False,
            memory_db_path=workdir / "jake_memory.db",
        )
        self._guests.append(namespace)
        return namespace

    def close_guest(self, namespace: ProfileNamespace) -> None:
        """Fine della sessione ospite: cancella tutto cio' che ha scritto."""
        if namespace.persistent:
            raise ProfileError("close_guest vale solo per un profilo ospite")
        shutil.rmtree(namespace.memory_db_path.parent, ignore_errors=True)
        namespace.conversation = ConversationStateManager()
        if namespace in self._guests:
            self._guests.remove(namespace)

    def close_all_guests(self) -> int:
        count = len(self._guests)
        for guest in list(self._guests):
            self.close_guest(guest)
        return count

    # ---- selezione dal riconoscimento vocale ----------------------------------------------------------

    def select(self, hint: SpeakerHint) -> ProfileNamespace | None:
        """Il profilo indicato dalla voce, SOLO se il riconoscimento e' sicuro e il profilo esiste ancora.
        Altrimenti None: il chiamante deve chiedere chi parla (F2.7.3) o usare l'ospite, mai indovinare."""
        if hint.confidence != "high" or hint.profile_id is None:
            return None
        try:
            return self.namespace(hint.profile_id)
        except ProfileError:
            return None
