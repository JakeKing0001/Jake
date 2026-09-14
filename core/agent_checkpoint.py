"""Checkpoint per compiti composti interrotti (F1.8.4, ultimo pezzo dichiarato: "gestire shutdown
con drain limitato, checkpoint e release dei device" - drain e release erano gia' chiusi in questa
sessione, questo chiude il terzo).

Prima di questo modulo, un compito composto (`TaskAgent.run()`, un piano a piu' passi eseguito UN
passo alla volta) interrotto a meta' - kill switch, crash, spegnimento improvviso del PC - andava
completamente PERSO: nessuna traccia di "3 passi su 6 erano gia' riusciti" sopravviveva al riavvio,
anche se quei 3 passi avevano gia' avuto un effetto reale (un file creato, un promemoria impostato)
gia' scritto nel ledger. L'utente doveva ripetere la richiesta da capo, rischiando di ripetere
anche i passi gia' fatti.

Scelte di scope deliberate (non l'intera "resume" ambiziosa possibile):
- Il checkpoint si aggiorna DOPO ogni passo (non continuamente durante un passo): se un passo e'
  a meta' quando Jake si interrompe, quel passo si perde comunque - solo i passi gia' CONCLUSI
  (riusciti o falliti) sopravvivono, mai uno stato a meta' esecuzione di una skill.
- Nessuna ripresa AUTOMATICA all'avvio: un checkpoint trovato resta li', inerte, finche' l'utente
  non chiede esplicitamente di riprendere (vedi RESUME_INTERRUPTED_TASK, skills/session_control.py)
  - riprendere da soli un'automazione DESTRUCTIVE/ADMIN senza che l'utente lo sappia sarebbe
  esattamente il tipo di autonomia non richiesta che F1 vuole evitare.
- Un solo checkpoint alla volta (sovrascritto, non una coda): un compito composto alla volta e'
  gia' il modello mentale di TaskAgent.run() stesso (MAX_STEPS, RUN_TIMEOUT_SECONDS - un compito
  per turno), niente di nuovo da gestire qui.
- Cancellato quando il compito finisce per QUALUNQUE motivo (successo, errore, domanda
  all'utente) - un checkpoint serve solo per un'interruzione ANOMALA (kill/crash), non per il
  normale "il compito e' finito" ne' per una conferma in sospeso (quella ha gia' il proprio
  meccanismo di ripresa, `conversation_state.pending_action` - due concetti diversi, mai
  confusi: un checkpoint stantio dopo un compito concluso normalmente confonderebbe una futura
  RESUME_INTERRUPTED_TASK, facendole credere che ci sia ancora qualcosa da riprendere."""
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CHECKPOINT_PATH = Path(__file__).resolve().parent.parent / "data" / "agent_checkpoint.json"


@dataclass
class AgentCheckpoint:
    trace_id: str
    agent_name: str
    request: str
    completed_steps: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id, "agent_name": self.agent_name, "request": self.request,
            "completed_steps": self.completed_steps, "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict) -> "AgentCheckpoint":
        return AgentCheckpoint(
            trace_id=str(data["trace_id"]), agent_name=str(data["agent_name"]), request=str(data["request"]),
            completed_steps=list(data.get("completed_steps") or []),
            created_at=float(data.get("created_at") or 0.0), updated_at=float(data.get("updated_at") or 0.0),
        )


class AgentCheckpointStore:
    """Un solo file JSON, scritto con lo stesso pattern atomico gia' usato per config.json
    (F1.4.1: scrivi su un `.tmp` nella STESSA cartella, poi `os.replace()` - o il file vecchio
    completo resta intatto, o il nuovo file completo prende il suo posto, mai uno stato a meta'
    anche se il processo muore proprio durante il salvataggio del checkpoint stesso)."""

    def __init__(self, path: Path | str = DEFAULT_CHECKPOINT_PATH):
        self.path = Path(path)

    def save(self, checkpoint: AgentCheckpoint) -> None:
        checkpoint.updated_at = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        tmp_path.write_text(json.dumps(checkpoint.to_dict(), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, self.path)

    def load(self) -> AgentCheckpoint | None:
        """None sia quando non c'e' nessun checkpoint (il caso comune: nessun compito interrotto)
        sia quando il file e' illeggibile/corrotto (un troncamento a meta' scrittura non dovrebbe
        succedere grazie alla scrittura atomica sopra, ma un file manomesso/da una versione futura
        con un formato diverso non deve mai far crashare l'avvio di Jake - nega per difetto,
        stesso principio gia' applicato altrove in F1)."""
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return AgentCheckpoint.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return None

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
