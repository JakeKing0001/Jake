import os
import shutil
import string
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path


@dataclass(frozen=True)
class AppMatch:
    requested: str
    matched_app: str
    score: float
    launcher: str


class AppResolver:
    """Scopre applicazioni Windows dallo Start Menu e dal PATH."""

    LAUNCHER_SUFFIXES = {".lnk", ".exe", ".bat", ".cmd"}

    def __init__(self, search_paths: list[Path] = None, threshold: float = 0.72):
        self.search_paths = search_paths or self._default_start_menu_paths()
        self.threshold = threshold
        self._applications = None
        self._display_names = {}

    def discover(self) -> dict[str, str]:
        """Restituisce una mappa nome normalizzato -> launcher."""
        applications = {}
        self._display_names = {}
        for search_path in self.search_paths:
            self._add_start_menu_entries(applications, Path(search_path))
        self._add_path_entries(applications)
        self._applications = applications
        return applications.copy()

    def resolve(self, app_name: str) -> AppMatch | None:
        """Restituisce il launcher piu simile sopra la soglia configurata."""
        normalized_name = self.normalize_name(app_name)
        if not normalized_name:
            return None
        applications = self._applications if self._applications is not None else self.discover()
        candidates = []
        for name, launcher in applications.items():
            score = self._similarity(normalized_name, name)
            if score >= self.threshold:
                candidates.append((score, name, launcher))
        if not candidates:
            return None

        score, name, launcher = max(candidates, key=lambda candidate: candidate[0])
        return AppMatch(
            requested=app_name,
            matched_app=self._display_names.get(name, name),
            score=score,
            launcher=launcher,
        )

    def refresh(self) -> dict[str, str]:
        """Forza una nuova scansione delle fonti disponibili."""
        self._applications = None
        return self.discover()

    @classmethod
    def _similarity(cls, requested: str, candidate: str) -> float:
        requested_compact = requested.replace(" ", "")
        candidate_compact = candidate.replace(" ", "")
        scores = [
            SequenceMatcher(None, requested, candidate).ratio(),
            SequenceMatcher(None, requested_compact, candidate_compact).ratio(),
        ]
        words = candidate.split()
        if len(words) > 1:
            abbreviated = "".join(word[0] for word in words[:-1]) + words[-1]
            scores.append(SequenceMatcher(None, requested_compact, abbreviated).ratio())
            if requested in words:
                scores.append(0.75)
        return max(scores)

    @staticmethod
    def normalize_name(name: str) -> str:
        """Normalizza nomi, estensioni launcher e punteggiatura."""
        normalized_name = Path(str(name)).stem.lower()
        normalized_name = normalized_name.replace("_", " ").replace("-", " ")
        normalized_name = normalized_name.translate(str.maketrans("", "", string.punctuation))
        return " ".join(normalized_name.split())

    @staticmethod
    def _default_start_menu_paths() -> list[Path]:
        paths = []
        app_data = os.environ.get("APPDATA")
        program_data = os.environ.get("PROGRAMDATA")
        if app_data:
            paths.append(Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
        if program_data:
            paths.append(Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
        return paths

    def _add_start_menu_entries(self, applications: dict[str, str], search_path: Path):
        if not search_path.is_dir():
            return
        for entry in search_path.rglob("*"):
            if entry.is_file() and entry.suffix.lower() in self.LAUNCHER_SUFFIXES:
                self._add_application(applications, entry.stem, str(entry))

    def _add_path_entries(self, applications: dict[str, str]):
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            path = Path(directory)
            if not path.is_dir():
                continue
            try:
                entries = path.iterdir()
            except OSError:
                continue
            for entry in entries:
                if entry.is_file() and entry.suffix.lower() in self.LAUNCHER_SUFFIXES:
                    launcher = shutil.which(entry.stem) or str(entry)
                    self._add_application(applications, entry.stem, launcher)

    def _add_application(self, applications: dict[str, str], name: str, launcher: str):
        normalized_name = self.normalize_name(name)
        if normalized_name:
            if normalized_name not in applications:
                applications[normalized_name] = launcher
                self._display_names[normalized_name] = name