"""Selettori salvati che si invalidano quando l'app o la sua struttura cambiano (F3.3.5).

Un selettore procedurale (`ElementSelector.to_dict`, F3.3.4) descrive un elemento per nome/ruolo/id, non
per coordinate: sopravvive a resize e a un tema diverso. Ma puo' smettere di essere vero in due modi, e
usarlo alla cieca dopo che e' successo e' peggio di non averlo:

1. **l'app e' cambiata** (un aggiornamento sposta o rinomina i controlli): si confronta la FIRMA dell'app
   (percorso dell'eseguibile, versione del file, dimensione). Se non coincide, il selettore e' obsoleto
   e non si usa piu' senza una nuova conferma;
2. **la struttura e' cambiata** (stessa versione, ma la finestra ora e' un'altra o ha perso la meta' dei
   controlli): si confronta lo scheletro STRUTTURALE dell'albero (`structure_tokens`) con quello salvato,
   con la somiglianza di Jaccard; sotto soglia il selettore e' obsoleto.

Lo scheletro esclude di proposito il contenuto che cambia da solo - il testo di un campo, le voci di
una lista, il titolo della finestra - e i controlli ripetuti: altrimenti aggiungere un elemento a una
lista "cambierebbe la struttura" e ogni selettore salvato scadrebbe a ogni uso.

L'obsolescenza e' PERSISTENTE (`status="stale"` con il motivo): un selettore scaduto resta scaduto finche'
non viene risalvato, cosi' un riavvio non lo riabilita per errore."""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from core.computer_use.selector import ElementSelector
from core.computer_use.ui_automation_adapter import ElementInfo

DEFAULT_SIMILARITY_THRESHOLD = 0.75

# Tipi il cui NOME e' parte della struttura (etichette stabili); gli altri contribuiscono solo col ruolo.
_NAMED_TYPES = {
    "Button", "TabItem", "MenuItem", "Menu", "MenuBar", "CheckBox", "RadioButton", "Hyperlink", "ToolBar",
    "TitleBar", "SplitButton", "ComboBox", "Tab", "Group", "Tree", "Table", "List", "Spinner", "Slider", "ProgressBar",
}
# Tipi che rappresentano CONTENUTO ripetuto/variabile: si contano come un solo gettone, non uno per voce.
_CONTENT_TYPES = {"ListItem", "DataItem", "TreeItem", "Text", "Edit", "Document", "Image", "Custom", "Pane"}


@dataclass(frozen=True)
class AppSignature:
    executable: str
    version: str
    size: int

    def matches(self, other: AppSignature) -> tuple[bool, str]:
        if self.executable.lower() != other.executable.lower():
            return False, f"eseguibile diverso ({self.executable} -> {other.executable})"
        if self.version != other.version:
            return False, f"versione dell'app cambiata ({self.version} -> {other.version})"
        if self.size != other.size:
            return False, f"file dell'app modificato (dimensione {self.size} -> {other.size})"
        return True, "firma dell'app invariata"


def app_signature_for_pid(pid: int) -> AppSignature | None:
    """Firma dell'app che possiede il processo, oppure None se non e' leggibile. Versione dal file
    (risorsa VS_VERSIONINFO); se il file non ha risorsa di versione si usa la data di modifica."""
    try:
        import psutil
        import win32api

        exe = psutil.Process(pid).exe()
        stat = os.stat(exe)
        try:
            info = win32api.GetFileVersionInfo(exe, "\\")
            version = (
                f"{info['FileVersionMS'] >> 16}.{info['FileVersionMS'] & 0xFFFF}."
                f"{info['FileVersionLS'] >> 16}.{info['FileVersionLS'] & 0xFFFF}"
            )
        except Exception:
            version = f"mtime-{int(stat.st_mtime)}"
        return AppSignature(exe, version, stat.st_size)
    except Exception:
        return None


def structure_tokens(tree: ElementInfo | None) -> frozenset[str]:
    """Lo scheletro strutturale dell'albero: un gettone per nodo "stabile" (ruolo + automation id, oppure
    ruolo + nome per le etichette), e un solo gettone `ruolo*` per ogni tipo di contenuto ripetuto. Il
    percorso dal genitore e' incluso, cosi' lo stesso bottone in due pannelli diversi non collassa."""
    tokens: set[str] = set()

    def walk(node: ElementInfo, path: str) -> None:
        role = node.control_type
        if role in _CONTENT_TYPES and not node.automation_id:
            token = f"{path}/{role}*"
        elif node.automation_id:
            token = f"{path}/{role}#{node.automation_id}"
        elif role in _NAMED_TYPES:
            token = f"{path}/{role}:{node.name}"
        else:
            token = f"{path}/{role}"
        tokens.add(token)
        for child in node.children:
            walk(child, token)

    if tree is not None:
        walk(tree, "")
    return frozenset(tokens)


def structure_similarity(saved: frozenset[str] | set[str], current: frozenset[str] | set[str]) -> float:
    """Somiglianza di Jaccard tra due scheletri, in [0, 1]. Due scheletri vuoti sono identici (1.0)."""
    if not saved and not current:
        return 1.0
    union = len(saved | current)
    return len(saved & current) / union if union else 1.0


@dataclass
class StoredSelector:
    name: str
    selector: dict
    app: dict | None
    tokens: list[str]
    saved_at: float
    status: str = "valid"  # "valid" | "stale"
    stale_reason: str = ""


@dataclass(frozen=True)
class Validation:
    usable: bool
    status: str
    reason: str
    similarity: float | None = None


class SelectorStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._clock = clock

    def _load(self) -> dict[str, StoredSelector]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return {name: StoredSelector(**entry) for name, entry in raw["entries"].items()}
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def _save_all(self, entries: dict[str, StoredSelector]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": {name: asdict(entry) for name, entry in entries.items()}}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---- API ---------------------------------------------------------------------------------------------

    def save(self, name: str, selector: ElementSelector, app: AppSignature | None, tree: ElementInfo | None) -> StoredSelector:
        """Salva (o RISALVA, riabilitando) un selettore con la firma dell'app e lo scheletro correnti."""
        entries = self._load()
        entry = StoredSelector(
            name, selector.to_dict(), asdict(app) if app else None, sorted(structure_tokens(tree)), self._clock(),
        )
        entries[name] = entry
        self._save_all(entries)
        return entry

    def get(self, name: str) -> StoredSelector | None:
        return self._load().get(name)

    def selector(self, name: str) -> ElementSelector | None:
        """Il selettore, SOLO se e' ancora valido: uno scaduto non si consegna per errore."""
        entry = self._load().get(name)
        if entry is None or entry.status != "valid":
            return None
        return ElementSelector.from_dict(entry.selector)

    def check(self, name: str, current_app: AppSignature | None, current_tree: ElementInfo | None,
              threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> Validation:
        """Confronta il selettore salvato con lo stato attuale; se non regge lo segna scaduto (persistente)."""
        entries = self._load()
        entry = entries.get(name)
        if entry is None:
            return Validation(False, "missing", "selettore non salvato")
        if entry.status == "stale":
            return Validation(False, "stale", entry.stale_reason or "gia' segnato come obsoleto")
        if entry.app is not None:
            if current_app is None:
                return self._mark_stale(entries, entry, "non riesco a leggere la firma dell'app: non posso confermare che sia la stessa")
            ok, reason = AppSignature(**entry.app).matches(current_app)
            if not ok:
                return self._mark_stale(entries, entry, reason)
        similarity = structure_similarity(set(entry.tokens), structure_tokens(current_tree))
        if current_tree is not None and similarity < threshold:
            return self._mark_stale(
                entries, entry,
                f"la struttura della finestra e' cambiata (somiglianza {similarity:.2f} < {threshold:.2f})", similarity,
            )
        return Validation(True, "valid", "app e struttura coerenti con quando e' stato salvato", similarity)

    def _mark_stale(self, entries: dict[str, StoredSelector], entry: StoredSelector, reason: str,
                    similarity: float | None = None) -> Validation:
        entry.status = "stale"
        entry.stale_reason = reason
        self._save_all(entries)
        return Validation(False, "stale", reason, similarity)

    def invalidate_app(self, executable: str, reason: str) -> int:
        """Segna scaduti TUTTI i selettori di un'app (es. dopo un aggiornamento noto). Ritorna quanti."""
        entries = self._load()
        count = 0
        for entry in entries.values():
            if entry.status == "valid" and entry.app and entry.app.get("executable", "").lower() == executable.lower():
                entry.status, entry.stale_reason = "stale", reason
                count += 1
        if count:
            self._save_all(entries)
        return count

    def names(self, only_valid: bool = False) -> list[str]:
        return [n for n, e in self._load().items() if not only_valid or e.status == "valid"]

    def delete(self, name: str) -> bool:
        entries = self._load()
        if name not in entries:
            return False
        del entries[name]
        self._save_all(entries)
        return True
