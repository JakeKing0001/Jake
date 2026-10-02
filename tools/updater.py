"""Aggiornamento atomico di Jake (F0.6.3/F0.6.4/F0.6.6/F0.6.7) per l'installazione portable da Git.

Decisione F0.6.1 (02/10/2026): Jake si installa per utente con `setup.ps1` nella cartella del repository, senza
privilegi di amministratore ne' certificati. Questo modulo aggiorna quella installazione:

1. rifiuta se ci sono modifiche locali ai file tracciati (non le sovrascrive mai);
2. porta il ramo corrente al nuovo commit solo in fast-forward (`dev` = origin/master, `stable` = ultimo tag v*);
3. reinstalla le dipendenze solo se il lock file e' cambiato;
4. controllo di salute: compilazione e import del nucleo in un processo nuovo;
5. se il controllo fallisce torna al commit precedente (e alle sue dipendenze).

`data/` e `config/settings.json` sono fuori da Git: un aggiornamento o un rollback non li tocca mai.

Uso: python -m tools.updater [--channel stable|dev] [--check]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = "requirements/all.lock.txt"

Runner = Callable[[list[str]], tuple[int, str]]


def _run(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout + proc.stderr).strip()


@dataclass
class UpdateResult:
    status: str  # "up_to_date" | "available" | "updated" | "rolled_back" | "refused" | "error"
    message: str
    old: str | None = None
    new: str | None = None


class Updater:
    def __init__(self, runner: Runner = _run, python: str = sys.executable):
        self.run = runner
        self.python = python

    def _git(self, *args: str) -> tuple[int, str]:
        return self.run(["git", *args])

    def _target(self, channel: str) -> str | None:
        if channel == "dev":
            return "origin/master"
        code, out = self._git("tag", "--list", "v*", "--sort=-v:refname")
        tags = [line.strip() for line in out.splitlines() if line.strip()] if code == 0 else []
        return tags[0] if tags else None

    def _install_deps(self) -> bool:
        code, _ = self.run([self.python, "-m", "pip", "install", "-q", "--require-hashes", "-r", LOCK_FILE])
        return code == 0

    def health_check(self) -> tuple[bool, str]:
        code, out = self.run([self.python, "-m", "compileall", "-q", "core", "skills", "main.py"])
        if code != 0:
            return False, out or "compilazione non riuscita"
        code, out = self.run([self.python, "-c", "import core.jake_core, core.version"])
        return code == 0, out

    def update(self, channel: str = "stable", check_only: bool = False) -> UpdateResult:
        code, dirty = self._git("status", "--porcelain", "--untracked-files=no")
        if code != 0:
            return UpdateResult("error", f"git non disponibile: {dirty}")
        if dirty:
            return UpdateResult("refused", "Ci sono modifiche locali ai file di Jake: non le sovrascrivo. "
                                           "Salvale o annullale, poi riprova.")
        code, out = self._git("fetch", "--tags", "origin")
        if code != 0:
            return UpdateResult("error", f"Non riesco a scaricare gli aggiornamenti: {out}")
        target = self._target(channel)
        if target is None:
            return UpdateResult("up_to_date", f"Nessuna versione pubblicata sul canale {channel}.")
        _, old = self._git("rev-parse", "HEAD")
        code, new = self._git("rev-parse", f"{target}^{{commit}}")
        if code != 0:
            return UpdateResult("error", f"Versione {target} non trovata.")
        if new == old:
            return UpdateResult("up_to_date", "Jake e' gia' aggiornato.", old, new)
        code, _ = self._git("merge-base", "--is-ancestor", old, new)
        if code != 0:
            return UpdateResult("refused", f"{target} non e' un avanzamento della versione installata: "
                                           "niente aggiornamento automatico.", old, new)
        if check_only:
            return UpdateResult("available", f"Aggiornamento disponibile: {target}.", old, new)

        code, changed = self._git("diff", "--name-only", old, new, "--", LOCK_FILE)
        deps_changed = code != 0 or bool(changed)
        code, out = self._git("merge", "--ff-only", new)
        if code != 0:
            return UpdateResult("error", f"Aggiornamento non applicato: {out}", old, new)
        healthy, detail = (self._install_deps(), "dipendenze non installate") if deps_changed else (True, "")
        if healthy:
            healthy, detail = self.health_check()
        if healthy:
            return UpdateResult("updated", f"Jake aggiornato a {target}.", old, new)

        self._git("reset", "--keep", old)
        if deps_changed:
            self._install_deps()
        return UpdateResult("rolled_back", f"La nuova versione non supera il controllo ({detail[:300]}): "
                                           "ripristinata quella precedente.", old, new)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggiorna Jake con rollback automatico.")
    parser.add_argument("--channel", choices=("stable", "dev"), default="stable")
    parser.add_argument("--check", action="store_true", help="dice solo se c'e' un aggiornamento")
    args = parser.parse_args(argv)
    result = Updater().update(args.channel, check_only=args.check)
    print(result.message)
    return 0 if result.status in {"up_to_date", "available", "updated"} else 1


if __name__ == "__main__":
    sys.exit(main())
