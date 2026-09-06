import os
import time
from pathlib import Path

from core.skill_result import SkillResult

# Cartelle piu' probabili per un file dell'utente, controllate PRIMA di una scansione totale
# della home: la maggior parte dei file che si cercano a voce sta li', ed evita di scandire
# per intero alberi enormi e poco pertinenti (AppData, cache dei browser, .git, node_modules,
# ambienti virtuali Python...).
_PRIORITY_SUBDIRS = ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music", "OneDrive")
_SKIP_DIR_NAMES = {
    "appdata", "programdata", "windows", "$recycle.bin", "node_modules", "__pycache__",
    ".venv", "venv", "env", ".cache", "cache", "temp", "tmp", "system volume information",
}
# Limite di tempo per ciascuna chiamata (v3.1): senza questo, cercare senza indicare una
# cartella scandiva l'intera home dell'utente, che su un profilo reale (AppData, cache dei
# browser, ambienti virtuali, file OneDrive) puo' contenere centinaia di migliaia di voci e
# bloccare Jake per minuti su un singolo comando (osservato dal vivo: un agente che cercava
# "command.py" senza percorso e' rimasto fermo, in attesa di I/O, ben oltre un minuto). Meglio
# restituire quello che si e' trovato entro il budget (o NOT_FOUND) che restare bloccati.
TIME_BUDGET_SECONDS = 5.0


def _scan_level(directories: list, deadline: float) -> tuple[list, list]:
    """Analizza il contenuto DIRETTO di ciascuna cartella indicata (senza scendere oltre) e
    restituisce (file trovati a questo livello, sottocartelle da esplorare al livello dopo)."""
    files = []
    next_level = []
    for directory in directories:
        if time.monotonic() > deadline:
            break
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    if time.monotonic() > deadline:
                        break
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.lower() not in _SKIP_DIR_NAMES and not entry.name.startswith("."):
                                next_level.append(Path(entry.path))
                        else:
                            files.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return files, next_level


class FindFileSkill:
    """Ricerca locale per nome file, senza indice (per la ricerca via NEST vedi SEARCH_FILES).

    Senza una cartella esplicita, cerca prima nelle cartelle utente piu' probabili (Desktop,
    Documenti, Download...) esplorandole in AMPIEZZA, un livello di profondita' alla volta e
    tutte insieme, sempre entro un tempo limite condiviso: mai piu' una scansione che blocca
    Jake a tempo indeterminato.

    L'ampiezza (invece della profondita' di os.walk/rglob) risolve due problemi osservati su
    un vero Desktop pieno di cartelle disparate: (1) tra le cartelle a un livello di profondita'
    possono convivere un piccolo progetto e una raccolta di giochi/ROM da centinaia di migliaia
    di file, e un attraversamento in profondita' rischia di restare intrappolato per tutto il
    tempo a disposizione dentro la prima cartella enorme incontrata, senza mai raggiungere le
    altre; (2) analizzando tutte le radici insieme livello per livello, appena un livello intero
    produce almeno un risultato la ricerca si ferma subito, senza scendere piu' a fondo del
    necessario: un file poco annidato in una radice qualsiasi (es. un documento in Documenti)
    salta fuori quasi subito, invece di aspettare comunque l'intero budget di tempo."""

    metadata = {
        "intent": "FIND_FILE",
        "description": "Cerca file per nome: in una cartella se indicata, altrimenti nelle cartelle "
        "utente piu' comuni (Desktop, Documenti, Download...).",
        "parameters": {
            "name": {
                "type": "string",
                "required": True,
                "description": "Nome, o parte del nome, del file da cercare.",
            },
            "path": {
                "type": "string",
                "required": False,
                "description": "Cartella in cui cercare. Se omessa, cerca nelle cartelle utente piu' comuni.",
            },
        },
    }

    MAX_RESULTS = 15

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        name = (parameters.get("name") or "").strip().lower()
        raw_root = (parameters.get("path") or "").strip()
        if not name:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        if raw_root:
            root = Path(raw_root).expanduser()
            if not root.is_dir():
                return SkillResult(success=False, data={"path": str(root)}, error="PATH_NOT_FOUND")
            current_level = [root]
        else:
            home = Path.home()
            current_level = [home / sub for sub in _PRIORITY_SUBDIRS if (home / sub).is_dir()]
            current_level.append(home)  # ultima spiaggia: tutta la home, stesso budget di tempo

        deadline = time.monotonic() + TIME_BUDGET_SECONDS
        matches = []
        seen = set()
        while current_level and time.monotonic() < deadline:
            files, current_level = _scan_level(current_level, deadline)
            for entry in files:
                if name in entry.name.lower() and entry not in seen:
                    seen.add(entry)
                    matches.append(str(entry))
                    if len(matches) >= self.MAX_RESULTS:
                        break
            if matches:
                break  # trovato qualcosa in questo livello: non serve scendere piu' a fondo

        if not matches:
            return SkillResult(success=False, data={"name": name}, error="NOT_FOUND")
        return SkillResult(success=True, data={"name": name, "results": matches})
