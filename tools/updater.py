"""Aggiornamento atomico di Jake (F0.6.3/F0.6.4/F0.6.6/F0.6.7) per l'installazione portable da Git.

Decisione F0.6.1 (02/10/2026): Jake si installa per utente con `setup.ps1` nella cartella del repository, senza
privilegi di amministratore ne' certificati. Questo modulo aggiorna quella installazione:

1. rifiuta se ci sono modifiche locali ai file tracciati (non le sovrascrive mai);
2. porta il ramo corrente al nuovo commit solo in fast-forward (`dev` = origin/master, `stable` = ultimo tag v*, o
   con `release_public_key` nelle impostazioni il commit indicato da `releases/stable.json` firmato - F0.6.5);
3. reinstalla le dipendenze solo se il lock file e' cambiato;
4. controllo di salute: compilazione e import del nucleo in un processo nuovo;
5. se il controllo fallisce torna al commit precedente (e alle sue dipendenze).

`data/` e `config/settings.json` sono fuori da Git: un aggiornamento o un rollback non li tocca mai.

Uso: python -m tools.updater [--channel stable|dev] [--check]
Chi pubblica: python -m tools.updater --keygen   (una volta: chiave privata in data/, pubblica da mettere nelle
              impostazioni di chi aggiorna come `release_public_key`)
              python -m tools.updater --sign v1.2 (scrive releases/stable.json firmato per quel tag; poi commit e push)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = "requirements/all.lock.txt"
MANIFEST_PATH = "releases/stable.json"
SIGNING_KEY_FILE = ROOT / "data" / "release_signing_key.txt"

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
    def __init__(self, runner: Runner = _run, python: str = sys.executable, public_key: str | None = None):
        self.run = runner
        self.python = python
        # F0.6.5: ancora di fiducia nelle impostazioni LOCALI (gitignorate): un aggiornamento non puo' cambiarla
        self.public_key = public_key

    def _git(self, *args: str) -> tuple[int, str]:
        return self.run(["git", *args])

    def _signed_stable_target(self) -> tuple[str | None, str]:
        """Il commit della versione stabile dichiarato da releases/stable.json su origin/master, solo se firmato da
        una chiave fidata. (None, motivo) altrimenti: con una chiave configurata non si installa nulla di non firmato."""
        from core.release_eval import ReleaseError, ReleaseManifest, ReleaseTrustStore

        code, raw = self._git("show", f"origin/master:{MANIFEST_PATH}")
        if code != 0:
            return None, "nessuna versione stabile firmata pubblicata"
        try:
            data = json.loads(raw)
            manifest = ReleaseManifest(**data["manifest"])
            trust = ReleaseTrustStore()
            trust.add_public(str(self.public_key))
            trust.verify(manifest, data["signature"])
        except ReleaseError as exc:
            return None, f"firma della versione stabile rifiutata ({exc.code})"
        except (ValueError, KeyError, TypeError):
            return None, "manifest della versione stabile illeggibile"
        if manifest.channel != "stable":
            return None, "il manifest firmato non e' del canale stabile"
        return manifest.build_digest, manifest.version

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
        if channel == "stable" and self.public_key:
            target, label = self._signed_stable_target()
            if target is None:
                return UpdateResult("refused", f"Aggiornamento stabile non applicato: {label}.")
        else:
            target = self._target(channel)
            label = target or ""
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
            return UpdateResult("refused", f"{label} non e' un avanzamento della versione installata: "
                                           "niente aggiornamento automatico.", old, new)
        if check_only:
            return UpdateResult("available", f"Aggiornamento disponibile: {label}.", old, new)

        code, changed = self._git("diff", "--name-only", old, new, "--", LOCK_FILE)
        deps_changed = code != 0 or bool(changed)
        code, out = self._git("merge", "--ff-only", new)
        if code != 0:
            return UpdateResult("error", f"Aggiornamento non applicato: {out}", old, new)
        healthy, detail = (self._install_deps(), "dipendenze non installate") if deps_changed else (True, "")
        if healthy:
            healthy, detail = self.health_check()
        if healthy:
            return UpdateResult("updated", f"Jake aggiornato a {label}.", old, new)

        self._git("reset", "--keep", old)
        if deps_changed:
            self._install_deps()
        return UpdateResult("rolled_back", f"La nuova versione non supera il controllo ({detail[:300]}): "
                                           "ripristinata quella precedente.", old, new)


def keygen(path: Path = SIGNING_KEY_FILE) -> str:
    """Crea la chiave di firma delle release (mai sovrascritta) e restituisce la chiave pubblica."""
    from core.release_eval import ReleaseSigningKey

    if path.exists():
        key = ReleaseSigningKey.from_private_b64(path.read_text(encoding="utf-8"))
    else:
        key = ReleaseSigningKey.generate()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key.private_b64, encoding="utf-8")
    return key.public_b64


def sign_release(tag: str, runner: Runner = _run, key_path: Path = SIGNING_KEY_FILE,
                 out_path: Path = ROOT / MANIFEST_PATH) -> dict:
    """Firma il commit del tag come versione stabile e scrive releases/stable.json."""
    from core.release_eval import ReleaseManifest, ReleaseSigningKey

    code, commit = runner(["git", "rev-parse", f"{tag}^{{commit}}"])
    if code != 0:
        raise ValueError(f"tag {tag} non trovato")
    previous = None
    if out_path.exists():
        try:
            previous = json.loads(out_path.read_text(encoding="utf-8"))["manifest"]["version"]
        except (ValueError, KeyError):
            previous = None
    manifest = ReleaseManifest(channel="stable", version=tag, build_digest=commit, eval_summary={},
                               previous_version=previous, created_at=time.time())
    key = ReleaseSigningKey.from_private_b64(key_path.read_text(encoding="utf-8"))
    data = {"manifest": manifest.to_signable(), "signature": key.sign(manifest)}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def _configured_public_key() -> str | None:
    try:
        from core.config import Config

        value = Config().get("release_public_key")
    except Exception:
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggiorna Jake con rollback automatico.")
    parser.add_argument("--channel", choices=("stable", "dev"), default="stable")
    parser.add_argument("--check", action="store_true", help="dice solo se c'e' un aggiornamento")
    parser.add_argument("--keygen", action="store_true", help="chi pubblica: crea la chiave di firma")
    parser.add_argument("--sign", metavar="TAG", help="chi pubblica: firma TAG come versione stabile")
    args = parser.parse_args(argv)
    if args.keygen:
        print(f"Chiave privata in {SIGNING_KEY_FILE} (non condividerla).")
        print(f"release_public_key per le impostazioni di chi aggiorna: {keygen()}")
        return 0
    if args.sign:
        sign_release(args.sign)
        print(f"Scritto {MANIFEST_PATH}: fai commit e push su master per pubblicare {args.sign}.")
        return 0
    result = Updater(public_key=_configured_public_key()).update(args.channel, check_only=args.check)
    print(result.message)
    return 0 if result.status in {"up_to_date", "available", "updated"} else 1


if __name__ == "__main__":
    sys.exit(main())
