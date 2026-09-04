import os
from pathlib import Path


def _subtree_blocked_roots() -> list[Path]:
    """Cartelle di sistema il cui intero contenuto non deve mai essere spostato/rinominato/eliminato."""
    roots = []
    for env_var in ("WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        value = os.environ.get(env_var)
        if value:
            roots.append(Path(value))
    return roots


def is_protected_path(path: Path) -> bool:
    """Vero se il percorso e' una radice critica (home utente, radice del disco) o e' dentro
    una cartella di sistema il cui contenuto non deve essere toccato. Non blocca il resto del disco."""
    resolved = path.resolve()

    home = Path.home().resolve()
    system_drive = Path(f"{os.environ.get('SystemDrive', 'C:')}\\").resolve()
    if resolved in (home, system_drive):
        return True

    for root in _subtree_blocked_roots():
        root_resolved = root.resolve()
        if resolved == root_resolved or root_resolved in resolved.parents:
            return True

    return False
