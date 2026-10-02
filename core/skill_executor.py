"""Esecuzione di una skill gia' scelta: policy minima fail-closed, percorsi parlati, lock per risorsa sulle mutazioni
filesystem, snapshot prima di una cancellazione e passaggio al worker sandboxato per le skill dei plugin.

Estratto da `SkillRegistry` (3.2 Reliability & Architecture): il registro sa QUALI skill esistono, questo modulo sa
COME eseguirne una in sicurezza. La storia dei buchi chiusi qui (F1.2.1 percorso 7, F1.3.4, F1.8.1) e' nei messaggi
dei commit e in ROADMAP.md."""
from __future__ import annotations

import contextlib
import os
from pathlib import Path

from core.action_snapshot import SnapshotStore, capture_snapshot
from core.path_resolver import resolve_user_path
from core.plugin_manager import PluginManager
from core.resource_lock import ResourceLockManager
from core.skill_result import SkillResult
from core.turn_cancellation import current_turn_cancelled


class SkillExecutor:
    PATH_PARAMETERS = ("path", "destination")
    # F1.8.1: le quattro mutazioni filesystem fanno controllo-poi-agisci non atomico (`exists()` e poi
    # `mkdir`/`rename`/`move`/`unlink`): due MOVE_PATH concorrenti verso la stessa cartella riportavano entrambi
    # success mentre un file spariva sovrascritto. Si serializzano per resource key, non con un lock globale.
    FILESYSTEM_MUTATION_INTENTS = frozenset({"CREATE_PATH", "RENAME_PATH", "MOVE_PATH", "DELETE_PATH"})

    def __init__(self, plugins: PluginManager, resource_locks: ResourceLockManager | None = None,
                 snapshot_store: SnapshotStore | None = None):
        self.plugins = plugins
        self.resource_locks = resource_locks or ResourceLockManager()
        self.snapshot_store = snapshot_store or SnapshotStore()

    def execute(self, skill, intent: str, parameters: dict | None, policy_engine, *,
                action_id: str | None = None, private: bool = False):
        """F1.2.1 (percorso 7): `policy_engine=None` e' FAIL-CLOSED - un chiamante che salta l'autorizzazione non
        esegue nulla; qui si ricontrolla solo `blocked_intents`, la decisione interattiva l'ha gia' presa chi chiama.
        F1.3.4: con `action_id`, DELETE_PATH salva uno snapshot del file DENTRO lo stesso lock della cancellazione."""
        if skill is None:
            return None
        if policy_engine is None or intent in policy_engine.blocked_intents:
            return SkillResult(success=False, data={}, error="POLICY_BLOCKED")
        # "Jake, basta": un turno vocale annullato non avvia piu' nessuna skill.
        if current_turn_cancelled():
            return SkillResult(success=False, data={}, error="CANCELLED")
        parameters = self.resolve_spoken_paths(intent, parameters)
        lock_keys = self.resource_lock_keys(intent, parameters)
        forged = self.plugins.is_forged(intent)

        def run():
            if forged:
                return self.plugins.execute(intent, parameters or {})
            self._maybe_capture_snapshot(intent, parameters, action_id, private)
            return skill.execute(parameters)

        if not lock_keys:
            return run()
        with self.acquire_all_writes(lock_keys):
            if current_turn_cancelled():  # annullato mentre si aspettava il lock
                return SkillResult(success=False, data={}, error="CANCELLED")
            return run()

    def resolve_spoken_paths(self, intent: str, parameters: dict | None) -> dict | None:
        """Percorsi "parlati" (v3.0): "desktop\\note.txt", "download" -> percorso reale. Mai in place."""
        if not parameters:
            return parameters
        for name in self.PATH_PARAMETERS:
            value = parameters.get(name)
            if isinstance(value, os.PathLike):
                value = os.fspath(value)
            if isinstance(value, str) and value.strip():
                resolved = resolve_user_path(value, prefer_existing=intent != "CREATE_PATH")
                if resolved != value:
                    parameters = dict(parameters)
                    parameters[name] = resolved
        return parameters

    def _maybe_capture_snapshot(self, intent: str, parameters: dict | None, action_id: str | None,
                                private: bool) -> None:
        """Solo DELETE_PATH: e' l'unica mutazione senza un inverso naturale."""
        if intent != "DELETE_PATH" or action_id is None or not parameters:
            return
        path = parameters.get("path")
        if not isinstance(path, str) or not path.strip():
            return
        snapshot = capture_snapshot(action_id, path, private=private)
        if snapshot is not None:
            self.snapshot_store.save(snapshot)

    def resource_lock_keys(self, intent: str, parameters: dict | None) -> tuple[str, ...]:
        """Resource key da serializzare per QUESTA chiamata; vuoto per ogni intent che non muta il filesystem.
        Limite dichiarato: "destination" e' la cartella (serializza piu' del necessario, mai meno) e `new_name` di
        RENAME_PATH non e' una chiave."""
        if intent not in self.FILESYSTEM_MUTATION_INTENTS or not parameters:
            return ()
        keys = []
        for name in self.PATH_PARAMETERS:
            value = parameters.get(name)
            if isinstance(value, os.PathLike):
                value = os.fspath(value)
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                resolved = Path(value).expanduser().resolve()
            except (OSError, ValueError):
                continue
            keys.append(f"filesystem:{os.path.normcase(str(resolved))}")
        return tuple(sorted(set(keys)))

    @contextlib.contextmanager
    def acquire_all_writes(self, resource_keys: tuple[str, ...]):
        """Lock in ordine ordinato (gia' garantito da resource_lock_keys): niente deadlock fra mutazioni inverse."""
        with contextlib.ExitStack() as stack:
            for key in resource_keys:
                stack.enter_context(self.resource_locks.acquire_write(key))
            yield
