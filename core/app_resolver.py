import json
import os
import re
import string
import subprocess
import threading
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path


@dataclass(frozen=True)
class AppMatch:
    requested: str
    matched_app: str
    score: float
    launcher: str


# Nomi "parlati" -> lancio diretto. Hanno priorita' su tutto: sono le app che si chiedono piu'
# spesso a voce e che il menu Start non elenca come collegamenti classici (app di Windows 11
# di Store, pagine delle impostazioni, strumenti di sistema).
KNOWN_APP_ALIASES = {
    "blocco note": "notepad.exe", "notepad": "notepad.exe", "note": "notepad.exe", "blocconote": "notepad.exe",
    "calcolatrice": "calc.exe", "calcolatore": "calc.exe", "calculator": "calc.exe",
    "esplora file": "explorer.exe", "esplora risorse": "explorer.exe", "file explorer": "explorer.exe",
    "explorer": "explorer.exe", "gestione file": "explorer.exe",
    "impostazioni": "ms-settings:", "settings": "ms-settings:", "impostazioni di windows": "ms-settings:",
    "pannello di controllo": "control.exe",
    "terminale": "wt.exe|cmd.exe", "terminal": "wt.exe|cmd.exe", "windows terminal": "wt.exe|cmd.exe",
    "prompt dei comandi": "cmd.exe", "prompt": "cmd.exe", "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "paint": "mspaint.exe",
    "task manager": "taskmgr.exe", "gestione attivita": "taskmgr.exe", "gestione attività": "taskmgr.exe",
    "browser": "__browser__", "internet": "__browser__", "il browser": "__browser__",
    "microsoft store": "ms-windows-store:", "store": "ms-windows-store:",
    "foto": "ms-photos:", "fotocamera": "microsoft.windows.camera:", "camera": "microsoft.windows.camera:",
    "calendario": "outlookcal:", "posta": "mailto:", "mail": "mailto:", "email": "mailto:",
    "strumento di cattura": "ms-screenclip:", "cattura": "ms-screenclip:", "snipping tool": "ms-screenclip:",
    "registro di sistema": "regedit.exe", "regedit": "regedit.exe",
    "wordpad": "wordpad.exe", "registratore": "ms-soundrecorder:", "orologio": "ms-clock:", "sveglia": "ms-clock:",
    "mappe": "bingmaps:", "meteo": "msnweather:", "bluetooth": "ms-settings:bluetooth", "wifi": "ms-settings:network-wifi",
    "xbox": "xbox:", "teams": "msteams:", "cestino": "shell:RecycleBinFolder",
}


class AppResolver:
    """Scopre applicazioni Windows dal menu Start, dal PATH e (v3.0) dall'elenco delle app di
    Start incluse quelle di Store/UWP (Get-StartApps), che non hanno un .lnk sul disco."""

    LAUNCHER_SUFFIXES = {".lnk", ".exe", ".bat", ".cmd"}
    # I nomi degli eseguibili nel PATH (locate, where, find, curl...) sono rumore per il matching
    # per somiglianza: valgono solo se quasi identici a quanto chiesto.
    PATH_MIN_SCORE = 0.92
    START_APPS_TIMEOUT = 12

    def __init__(
        self,
        search_paths: list[Path] | None = None,
        threshold: float = 0.72,
        include_start_apps: bool = True,
        include_path: bool = True,
    ):
        self.search_paths = self._default_start_menu_paths() if search_paths is None else search_paths
        self.threshold = threshold
        self.include_start_apps = include_start_apps
        self.include_path = include_path
        self._applications: dict[str, str] | None = None
        self._display_names: dict[str, str] = {}
        self._sources: dict[str, str] = {}
        self._lock = threading.RLock()
        self._discovery_thread: threading.Thread | None = None

    # ---- scoperta ----------------------------------------------------------------------

    def discover(self) -> dict[str, str]:
        """Restituisce una mappa nome normalizzato -> launcher."""
        applications: dict[str, str] = {}
        display_names: dict[str, str] = {}
        sources: dict[str, str] = {}
        for search_path in self.search_paths:
            self._add_start_menu_entries(applications, display_names, sources, Path(search_path))
        if self.include_start_apps:
            self._add_start_apps(applications, display_names, sources)
        if self.include_path:
            self._add_path_entries(applications, display_names, sources)
        with self._lock:
            self._applications = applications
            self._display_names = display_names
            self._sources = sources
        return applications.copy()

    def start_background_discovery(self) -> None:
        """Avvia la scoperta su un thread (Get-StartApps costa 1-3 s): all'avvio non blocca,
        e il primo 'apri ...' aspetta al massimo il tempo che manca."""
        with self._lock:
            if self._discovery_thread is not None or self._applications is not None:
                return
            self._discovery_thread = threading.Thread(target=self._safe_discover, daemon=True)
            self._discovery_thread.start()

    def _safe_discover(self) -> None:
        try:
            self.discover()
        except Exception:
            with self._lock:
                if self._applications is None:
                    self._applications = {}

    def _ensure_discovered(self) -> dict[str, str]:
        thread = self._discovery_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=self.START_APPS_TIMEOUT + 3)
        with self._lock:
            if self._applications is None:
                self.discover()
            # discover() imposta sempre self._applications prima di tornare (mai un return
            # anticipato che lo salti): questo "or {}" e' solo un ripiego difensivo, mai
            # atteso a scattare davvero, per non restituire None se quell'invariante si
            # rompesse in futuro.
            return self._applications or {}

    def display_names(self, wait: bool = False) -> list[str]:
        """Nomi leggibili delle app trovate (v3.0: vocabolario per correggere le trascrizioni).
        Non bloccante di default: se la scoperta e' ancora in corso restituisce una lista vuota,
        cosi' il primo comando non aspetta Get-StartApps."""
        if wait:
            self._ensure_discovered()
        with self._lock:
            if self._applications is None and not wait:
                self.start_background_discovery()
                return []
            return list(self._display_names.values())

    def refresh(self) -> dict[str, str]:
        """Forza una nuova scansione delle fonti disponibili."""
        with self._lock:
            self._applications = None
            self._discovery_thread = None
        return self.discover()

    # ---- risoluzione -------------------------------------------------------------------

    # L'elisione con apostrofo ("l'esplora file") va riconosciuta sul testo GREZZO, prima che
    # normalize_name() tolga la punteggiatura: normalize_name("l'esplora file") produce
    # "lesplora file" (l'apostrofo sparisce senza lasciare uno spazio), quindi il controllo
    # storico "normalized_name.startswith('l ')" piu' sotto non scattava MAI per questo caso -
    # riprodotto per davvero: resolve("l'esplora file") tornava None nonostante "esplora file"
    # sia in KNOWN_APP_ALIASES, mentre resolve("il pannello di controllo") (articolo con spazio,
    # non elisione) funzionava gia' correttamente. Il vecchio controllo su "il "/"la "/"lo "/"l "
    # resta com'era per il caso senza apostrofo, questo lo affianca per quello con apostrofo.
    _ELIDED_ARTICLE_PATTERN = re.compile(r"^l['’]\s*(.+)$", re.IGNORECASE)

    def resolve(self, app_name: str) -> AppMatch | None:
        """Restituisce il launcher piu' simile sopra la soglia configurata."""
        normalized_name = self.normalize_name(app_name)
        if not normalized_name:
            return None

        alias = KNOWN_APP_ALIASES.get(normalized_name)
        if alias is None and normalized_name.startswith(("il ", "la ", "lo ", "l ")):
            alias = KNOWN_APP_ALIASES.get(normalized_name.split(" ", 1)[1])
        if alias is None:
            elided = self._ELIDED_ARTICLE_PATTERN.match(app_name.strip())
            if elided:
                alias = KNOWN_APP_ALIASES.get(self.normalize_name(elided.group(1)))
        if alias is not None:
            return AppMatch(requested=app_name, matched_app=app_name.strip().capitalize(), score=1.0, launcher=alias)

        applications = self._ensure_discovered()
        candidates = []
        with self._lock:
            items = list(applications.items())
            sources = dict(self._sources)
        for name, launcher in items:
            score = self._similarity(normalized_name, name)
            minimum = self.PATH_MIN_SCORE if sources.get(name) == "path" else self.threshold
            if score >= minimum:
                candidates.append((score, name, launcher))
        if not candidates:
            return None

        score, name, launcher = max(candidates, key=lambda candidate: (candidate[0], self._sources.get(candidate[1]) != "path"))
        return AppMatch(
            requested=app_name,
            matched_app=self._display_names.get(name, name),
            score=score,
            launcher=launcher,
        )

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
            # "visual studio code" chiesto come "visual studio": prefisso di parole intere
            if candidate.startswith(requested + " "):
                scores.append(0.9)
        return max(scores)

    @staticmethod
    def normalize_name(name: str) -> str:
        """Normalizza nomi, estensioni launcher e punteggiatura."""
        normalized_name = Path(str(name)).stem.lower() if "\\" in str(name) or "/" in str(name) else str(name).lower()
        for suffix in (".lnk", ".exe", ".bat", ".cmd"):
            if normalized_name.endswith(suffix):
                normalized_name = normalized_name[: -len(suffix)]
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

    def _add_start_menu_entries(self, applications, display_names, sources, search_path: Path):
        if not search_path.is_dir():
            return
        for entry in search_path.rglob("*"):
            if entry.is_file() and entry.suffix.lower() in self.LAUNCHER_SUFFIXES:
                self._add_application(applications, display_names, sources, entry.stem, str(entry), "start_menu")

    START_APPS_CACHE = Path(__file__).resolve().parent.parent / "data" / "start_apps_cache.json"
    START_APPS_CACHE_TTL = 24 * 3600  # Get-StartApps costa 5-15 s: una volta al giorno basta

    def _load_start_apps(self) -> list[dict]:
        import time

        try:
            if self.START_APPS_CACHE.is_file() and time.time() - self.START_APPS_CACHE.stat().st_mtime < self.START_APPS_CACHE_TTL:
                cached = json.loads(self.START_APPS_CACHE.read_text(encoding="utf-8"))
                if isinstance(cached, list) and cached:
                    return cached
        except (OSError, json.JSONDecodeError):
            pass
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=self.START_APPS_TIMEOUT, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            payload = json.loads(completed.stdout.strip() or "[]")
        except Exception:
            return []
        if isinstance(payload, dict):
            payload = [payload]
        entries = [entry for entry in payload if isinstance(entry, dict) and entry.get("Name") and entry.get("AppID")]
        try:
            self.START_APPS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            self.START_APPS_CACHE.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return entries

    def _add_start_apps(self, applications, display_names, sources):
        """App elencate in Start (comprese quelle di Store, senza .lnk): si lanciano con
        shell:AppsFolder\\<AppID>. Un fallimento qui non deve mai bloccare la scoperta."""
        for entry in self._load_start_apps():
            self._add_application(applications, display_names, sources, entry["Name"], f"shell:AppsFolder\\{entry['AppID']}", "start_apps")

    def _add_path_entries(self, applications, display_names, sources):
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory:
                continue
            path = Path(directory)
            try:
                if not path.is_dir():
                    continue
                # Path.iterdir() e' lazy: PermissionError/FileNotFoundError possono emergere
                # durante il consumo, non alla creazione dell'iteratore. Materializzare qui
                # mantiene tutta l'operazione I/O dentro la protezione.
                entries = list(path.iterdir())
            except OSError:
                continue
            for entry in entries:
                # Niente shutil.which per ogni voce: con centinaia di eseguibili nel PATH costava
                # decine di secondi (ogni which rifa' la ricerca su tutte le cartelle del PATH).
                try:
                    is_launcher_file = entry.suffix.lower() in self.LAUNCHER_SUFFIXES and entry.is_file()
                except OSError:
                    continue
                if is_launcher_file:
                    self._add_application(applications, display_names, sources, entry.stem, str(entry), "path")

    def _add_application(self, applications, display_names, sources, name: str, launcher: str, source: str):
        normalized_name = self.normalize_name(name)
        if normalized_name and normalized_name not in applications:
            applications[normalized_name] = launcher
            display_names[normalized_name] = name
            sources[normalized_name] = source
