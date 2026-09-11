"""Risoluzione dei percorsi "parlati" (v3.0): "desktop", "download", "documenti\\tesi.pdf".

A voce nessuno detta "C:\\Users\\david\\Desktop": si dice "sul desktop", "nei download".
Le skill sui file ricevono quindi percorsi relativi a cartelle note, e qui vengono
tradotti nel percorso reale (tenendo conto di OneDrive, che su molti PC Windows sposta
Desktop e Documenti dentro la propria cartella)."""
import os
import re
from pathlib import Path

_KNOWN = {
    "desktop": "Desktop", "scrivania": "Desktop",
    "download": "Downloads", "downloads": "Downloads", "scaricati": "Downloads",
    "documenti": "Documents", "documents": "Documents", "documento": "Documents",
    "immagini": "Pictures", "foto": "Pictures", "pictures": "Pictures",
    "video": "Videos", "videos": "Videos", "filmati": "Videos",
    "musica": "Music", "music": "Music",
    "home": "", "utente": "", "casa": "",
}

_SPECIAL = {
    "cestino": "shell:RecycleBinFolder",
    "pannello di controllo": "shell:ControlPanelFolder",
    "questo pc": "shell:MyComputerFolder",
    "computer": "shell:MyComputerFolder",
}


def known_folder(name: str) -> Path:
    """Percorso reale di una cartella utente nota ('Desktop'...), anche se spostata in OneDrive."""
    home = Path.home()
    if name == "":
        return home
    candidates = [home / name]
    one_drive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    if one_drive:
        candidates.append(Path(one_drive) / name)
    candidates.append(home / "OneDrive" / name)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return home / name


def resolve_user_path(raw: str, prefer_existing: bool = True) -> str:
    """Traduce un percorso 'parlato' in un percorso assoluto. Se e' gia' assoluto lo lascia."""
    if not isinstance(raw, str):
        return raw
    text = raw.strip().strip('"').strip("'")
    if not text:
        return raw
    lowered = text.lower()
    if lowered.startswith("shell:"):
        return text
    if lowered in _SPECIAL:
        return _SPECIAL[lowered]

    expanded = Path(os.path.expandvars(text)).expanduser()
    if expanded.is_absolute() or re.match(r"^[a-zA-Z]:", text):
        return str(expanded)

    # articoli/preposizioni residue: "la cartella download", "cartella documenti"
    lowered = re.sub(r"^(?:la |il |nella |nel |sul |sulla |dentro |in )?(?:cartella |folder |file )?", "", lowered).strip()
    parts = [part for part in re.split(r"[\\/]+", lowered) if part]
    if not parts:
        return raw

    first = parts[0]
    if first in _KNOWN:
        base = known_folder(_KNOWN[first])
        rest = parts[1:]
        return str(base.joinpath(*rest)) if rest else str(base)

    if not prefer_existing:
        return str(known_folder("Desktop").joinpath(*parts))

    # Percorso relativo senza cartella nota: cerca dove esiste davvero, altrimenti sul Desktop.
    search_roots = [Path.cwd(), known_folder("Desktop"), Path.home(), known_folder("Documents"), known_folder("Downloads")]
    for root in search_roots:
        candidate = root.joinpath(*parts)
        if candidate.exists():
            return str(candidate)
    # Ricerca superficiale per nome (es. "jake" -> Desktop\Informatica\jake)
    if len(parts) == 1:
        for root in (known_folder("Desktop"), known_folder("Documents")):
            try:
                for entry in root.iterdir():
                    if entry.is_dir() and entry.name.lower() == first:
                        return str(entry)
                for entry in root.iterdir():
                    if entry.is_dir():
                        for sub in entry.iterdir():
                            if sub.name.lower() == first:
                                return str(sub)
            except OSError:
                continue
    return str(known_folder("Desktop").joinpath(*parts))
