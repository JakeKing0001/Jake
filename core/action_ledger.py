"""Action ledger append-only (F1, Trustworthy Agent Core 3.0): "chi ha chiesto cosa, quale
agente ha deciso, quale skill ha agito, con quale autorizzazione e quale risultato" - vedi la
fase F1 in ROADMAP.md.

Diverso da core/logger.log_action (F0): quello ruota (max 2 MB x 4 file, pensato per il debug
quotidiano - vecchie righe vengono scartate di proposito per non riempire il disco). Questo non
ruota mai: un registro di controllo non deve perdere silenziosamente le voci vecchie. La crescita
illimitata resta un limite noto e dichiarato, non risolto qui - servirebbe una policy di
retention/archiviazione esplicita (F1 la elenca insieme a Privacy Engine, fase 5.6), non una
rotazione silenziosa che la aggirerebbe di nascosto.

action_id identifica UNA azione (un passo dell'agente, un comando singolo); trace_id (core/
logger.py) correla invece TUTTI i passi di una stessa richiesta/compito. authorization e'
derivata da segnali gia' presenti nel sistema (risk.py, i parametri "confirmed"/"authenticated"
gia' usati da JakeCore._resolve_and_execute per riconoscere una richiesta gia' confermata), non
un campo inventato: "none" quando l'azione non ha mai avuto bisogno di autorizzazione,
"confirmed"/"passphrase" quando l'ha ricevuta, "pending"/"blocked" quando e' in attesa o negata."""
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from core.logger import new_trace_id

DEFAULT_LEDGER_PATH = Path(__file__).resolve().parent.parent / "data" / "jake_ledger.jsonl"

# Stesso generatore di core.logger.new_trace_id (uuid4 troncato): action_id e trace_id sono
# concettualmente la stessa cosa (un id breve per correlare righe di log), usati per due scopi
# diversi - un alias invece di duplicare la stessa funzione con un nome diverso.
new_action_id = new_trace_id

AUTHORIZATION_NONE = "none"
AUTHORIZATION_CONFIRMED = "confirmed"
AUTHORIZATION_PASSPHRASE = "passphrase"
AUTHORIZATION_PENDING = "pending"
AUTHORIZATION_BLOCKED = "blocked"


def authorization_of(result: str, parameters: dict) -> str:
    """Deriva lo stato di autorizzazione dagli stessi segnali gia' usati altrove (core/risk.py,
    JakeCore._resolve_and_execute), invece di chiedere a chi registra la ricevuta di dichiararlo
    a mano - due fonti diverse per lo stesso fatto potrebbero disallinearsi in silenzio."""
    parameters = parameters or {}
    if result in ("blocked_by_policy", "policy_blocked"):
        return AUTHORIZATION_BLOCKED
    if result in ("confirmation_required", "auth_required"):
        return AUTHORIZATION_PENDING
    if parameters.get("authenticated"):
        return AUTHORIZATION_PASSPHRASE
    if parameters.get("confirmed"):
        return AUTHORIZATION_CONFIRMED
    return AUTHORIZATION_NONE


@dataclass
class ActionReceipt:
    action_id: str
    trace_id: str
    ts: float
    intent: str
    requested_by: str  # "user" | "agent:<general|coding|research>" | "trigger:<nome>"
    risk_decision: str
    authorization: str
    result: str
    verified: Optional[bool] = None
    duration_ms: Optional[float] = None
    model: Optional[str] = None

    def to_json(self) -> str:
        record = {key: value for key, value in asdict(self).items() if value is not None}
        return json.dumps(record, ensure_ascii=False)


class ActionLedger:
    def __init__(self, path: Path = None):
        self._path = Path(path) if path else DEFAULT_LEDGER_PATH

    def record(self, receipt: ActionReceipt, *, private: bool = False) -> None:
        # Stessa policy di core/logger.log_action e core/session_recorder.SessionRecorder:
        # nulla viene scritto in modalita' privata, senza eccezioni per il ledger.
        if private:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(receipt.to_json() + "\n")

    def read_all(self) -> list[dict]:
        if not self._path.is_file():
            return []
        records = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def by_trace_id(self, trace_id: str) -> list[dict]:
        return [record for record in self.read_all() if record.get("trace_id") == trace_id]

    def by_action_id(self, action_id: str) -> Optional[dict]:
        for record in self.read_all():
            if record.get("action_id") == action_id:
                return record
        return None
