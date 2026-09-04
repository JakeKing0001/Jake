import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class NestError(Exception):
    """Errore riportato dalla CLI di NEST."""


@dataclass(frozen=True)
class NestSearchResult:
    path: str
    snippet: str
    score: float


class NestClient:
    """Espone la CLI di NEST (motore di ricerca personale locale) come tool di Jake."""

    def __init__(self, cli_path: Path = None, database_path: Path = None, timeout: float = 15):
        self.cli_path = cli_path or self._discover_cli_path()
        self.database_path = database_path
        self.timeout = timeout

    @classmethod
    def _discover_cli_path(cls) -> Path | None:
        """Cerca nest-cli: variabile d'ambiente, PATH, poi la build più recente accanto a Jake."""
        env_path = os.environ.get("JAKE_NEST_CLI_PATH")
        if env_path and Path(env_path).is_file():
            return Path(env_path)

        which_path = shutil.which("nest-cli") or shutil.which("nest")
        if which_path:
            return Path(which_path)

        nest_dist = Path(__file__).resolve().parent.parent.parent / "NEST" / "dist"
        if nest_dist.is_dir():
            candidates = list(nest_dist.glob("NEST-*-windows-x64/nest-cli.exe"))
            if candidates:
                candidates.sort(key=lambda candidate: cls._parse_version(candidate.parent.name))
                return candidates[-1]

        return None

    @staticmethod
    def _parse_version(directory_name: str) -> tuple:
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", directory_name)
        return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)

    def is_available(self) -> bool:
        return self.cli_path is not None and Path(self.cli_path).is_file()

    def search(self, query: str, limit: int = 10) -> list[NestSearchResult]:
        """Ricerca per nome/contenuto tra i file indicizzati da NEST."""
        return self._run_search_command("search", query, limit, "Score")

    def semantic_search(self, query: str, limit: int = 10) -> list[NestSearchResult]:
        """Ricerca per significato (embedding locali di NEST): richiede che l'indice semantico
        sia stato preparato con build_semantic_index(), altrimenti non trova nulla."""
        return self._run_search_command("semantic-search", query, limit, "Similarità")

    def hybrid_search(self, query: str, limit: int = 10) -> list[NestSearchResult]:
        """Combina corrispondenze esatte e ricerca per significato."""
        return self._run_search_command("hybrid-search", query, limit, "Rilevanza combinata")

    def build_semantic_index(self, timeout: float = 300) -> str:
        """Prepara/aggiorna l'indice semantico locale. Puo' richiedere tempo su tante sorgenti."""
        args = [str(self.cli_path)]
        if self.database_path:
            args += ["--database", str(self.database_path)]
        args += ["semantic-index"]

        try:
            completed = subprocess.run(
                args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NestError(str(exc)) from exc

        if completed.returncode != 0:
            raise NestError(completed.stderr.strip() or "Errore sconosciuto da NEST")
        return completed.stdout.strip()

    def _run_search_command(self, subcommand: str, query: str, limit: int, score_label: str) -> list[NestSearchResult]:
        args = [str(self.cli_path)]
        if self.database_path:
            args += ["--database", str(self.database_path)]
        args += [subcommand, query, "--limit", str(limit)]

        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NestError(str(exc)) from exc

        if completed.returncode != 0:
            raise NestError(completed.stderr.strip() or "Errore sconosciuto da NEST")

        return self._parse_results(completed.stdout, score_label)

    # Il CLI stampa "path\n  {snippet}\n  {score_label}: X.XX" per ogni risultato (l'etichetta
    # cambia per comando: "Score", "Similarità", "Rilevanza combinata"). Lo snippet puo'
    # contenere newline non indentate al suo interno (es. l'inizio di una classe nel sorgente),
    # quindi il parsing si ancora alla riga dell'etichetta (unica e affidabile) invece che
    # all'indentazione della prima riga di ciascun blocco.
    @staticmethod
    def _parse_results(output: str, score_label: str) -> list[NestSearchResult]:
        if output.strip().startswith("Nessun risultato"):
            return []

        score_line = re.compile(rf"^  {re.escape(score_label)}: ([0-9.]+)\s*$", re.MULTILINE)

        results = []
        block_start = 0
        for match in score_line.finditer(output):
            block_lines = output[block_start:match.start()].split("\n")
            while block_lines and not block_lines[0].strip():
                block_lines.pop(0)
            if not block_lines:
                block_start = match.end()
                continue

            path = block_lines[0].strip()
            snippet_lines = block_lines[1:]
            if snippet_lines and snippet_lines[0].startswith("  "):
                snippet_lines[0] = snippet_lines[0][2:]

            try:
                score = float(match.group(1))
            except ValueError:
                score = 0.0

            results.append(NestSearchResult(path=path, snippet="\n".join(snippet_lines).strip(), score=score))
            block_start = match.end()

        return results
