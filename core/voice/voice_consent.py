"""Consenso esplicito per la clonazione vocale (F2.5.6).

Convertire il timbro di Jake in un altro (RVC, `CharacterTtsProvider`) e' clonazione vocale: se il
modello e' costruito sulla voce di una persona vera, usarlo senza il suo consenso e' un abuso. Questo
registro rende il consenso un atto scritto e revocabile invece di una presunzione:

- un modello non si usa finche' un umano non ha registrato per iscritto CHI e' il soggetto della voce
  e su quale base (`BASES`), con una dichiarazione a parole sue;
- la registrazione si fa solo da riga di comando, mai da Jake, da una skill o da un LLM
  (`python -m core.voice.voice_consent grant ...`): nessun percorso di codice dell'assistente la crea;
- la revoca vale subito, anche a sessione in corso: il provider rilegge il registro a ogni frase;
- se il file e' illeggibile o corrotto si nega (fail closed) e non lo si sovrascrive in silenzio."""
from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from core.logger import get_logger

DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "voice_consent.json"

BASES = {
    "self": "la voce e' mia e la uso io",
    "consenting-person": "la persona titolare della voce ha dato consenso per iscritto",
    "fictional-character": "personaggio di fantasia, nessuna persona reale identificabile come soggetto",
    "synthetic-original": "voce sintetica creata da zero, non derivata da una persona reale",
}


class VoiceConsentError(Exception):
    """Manca il consenso alla clonazione di una voce."""


@dataclass
class ConsentRecord:
    model_name: str
    subject: str
    basis: str
    statement: str
    granted_at: float
    revoked_at: float | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class VoiceConsentRegistry:
    def __init__(self, path: Path = DEFAULT_PATH, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self._logger = get_logger()

    # ---- lettura ------------------------------------------------------------------------

    def _load(self) -> tuple[dict[str, ConsentRecord], bool]:
        """(record per modello, file_leggibile). File assente = leggibile e vuoto."""
        if not self.path.exists():
            return {}, True
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            records = {name: ConsentRecord(**entry) for name, entry in raw["records"].items()}
            return records, True
        except (OSError, ValueError, KeyError, TypeError):
            self._logger.exception("Registro consensi vocali illeggibile: nego ogni clonazione")
            return {}, False

    def records(self) -> list[ConsentRecord]:
        return list(self._load()[0].values())

    def is_allowed(self, model_name: str) -> bool:
        records, readable = self._load()
        record = records.get(model_name)
        return readable and record is not None and record.active

    def require(self, model_name: str) -> None:
        if not self.is_allowed(model_name):
            raise VoiceConsentError(
                f"Nessun consenso registrato per la voce '{model_name}'. Per registrarlo, da terminale: "
                f"python -m core.voice.voice_consent grant {model_name} --subject \"CHI\" "
                f"--basis {{{'|'.join(BASES)}}} --statement \"...\""
            )

    # ---- scrittura ----------------------------------------------------------------------

    def _save(self, records: dict[str, ConsentRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "records": {name: asdict(record) for name, record in records.items()}}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def grant(self, model_name: str, subject: str, basis: str, statement: str) -> ConsentRecord:
        if not model_name.strip():
            raise ValueError("nome del modello mancante")
        if not subject.strip():
            raise ValueError("indica di chi e' la voce (subject)")
        if basis not in BASES:
            raise ValueError(f"basi ammesse: {', '.join(BASES)}")
        if len(statement.strip()) < 10:
            raise ValueError("serve una dichiarazione a parole tue (almeno 10 caratteri)")
        records, readable = self._load()
        if not readable:
            backup = self.path.with_suffix(".corrupt")
            os.replace(self.path, backup)
            self._logger.warning("Registro consensi corrotto spostato in %s", backup)
        record = ConsentRecord(model_name.strip(), subject.strip(), basis, statement.strip(), self._clock())
        records[record.model_name] = record
        self._save(records)
        return record

    def revoke(self, model_name: str) -> bool:
        """True se c'era un consenso attivo da revocare. Il record resta (con la data di revoca):
        la storia di cosa e' stato permesso e quando non si cancella."""
        records, readable = self._load()
        record = records.get(model_name)
        if not readable or record is None or not record.active:
            return False
        record.revoked_at = self._clock()
        self._save(records)
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Consenso alla clonazione vocale (F2.5.6).")
    sub = parser.add_subparsers(dest="command", required=True)
    grant = sub.add_parser("grant", help="registra il consenso per un modello vocale")
    grant.add_argument("model_name")
    grant.add_argument("--subject", required=True, help="di chi e' la voce")
    grant.add_argument("--basis", required=True, choices=list(BASES))
    grant.add_argument("--statement", required=True, help="dichiarazione a parole tue")
    revoke = sub.add_parser("revoke", help="revoca il consenso (vale subito)")
    revoke.add_argument("model_name")
    sub.add_parser("list", help="elenca i consensi")
    args = parser.parse_args(argv)

    registry = VoiceConsentRegistry()
    if args.command == "grant":
        record = registry.grant(args.model_name, args.subject, args.basis, args.statement)
        print(f"Consenso registrato per '{record.model_name}' ({record.basis}).")
    elif args.command == "revoke":
        print("Revocato." if registry.revoke(args.model_name) else "Nessun consenso attivo per quel modello.")
    else:
        for record in registry.records():
            state = "attivo" if record.active else "revocato"
            print(f"{record.model_name}: {state} - {record.subject} ({record.basis})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
