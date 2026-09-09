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
    """Vero se il percorso e' una radice critica (home utente, radice di QUALUNQUE disco - non
    solo quello di sistema) o e' dentro una cartella di sistema il cui contenuto non deve essere
    toccato. Non blocca il resto del disco.

    F1: buco reale trovato e corretto - la docstring ha sempre promesso "radice del disco" in
    generale, ma il controllo verificava solo il disco di sistema (SystemDrive, tipicamente
    C:\\): la radice di un secondo disco (D:\\, un SSD esterno, una chiavetta...) non era mai
    protetta. DELETE_PATH su D:\\ passava dalla normale conferma si'/no invece di essere
    rifiutato a priori come C:\\ - una singola risposta affermativa (anche fraintesa da un
    comando vocale) avrebbe cancellato un intero disco. `resolved.parent == resolved` e' vero
    per QUALSIASI radice di filesystem (ogni lettera di unita' su Windows, "/" su POSIX), non
    solo per quella calcolata da SystemDrive."""
    resolved = path.resolve()

    home = Path.home().resolve()
    if resolved == home or resolved.parent == resolved:
        return True

    for root in _subtree_blocked_roots():
        root_resolved = root.resolve()
        if resolved == root_resolved or root_resolved in resolved.parents:
            return True

    return False
