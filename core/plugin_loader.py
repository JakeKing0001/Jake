import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path

from core.skill_manifest import Environment, Manifest, ManifestError, validate_package_dir


class _PluginPathTrackingRegistry:
    """Proxy visto da un plugin durante register() (F1.6): inoltra register_skill() alla vera
    SkillRegistry aggiungendo `plugin_path`, cosi' il worker sandboxato (core/sandboxed_skill_
    worker.py) sa quali intent devono eseguire li' invece che in processo. Il contratto
    `register(registry): registry.register_skill(intent, skill)` che ogni plugin/skill forgiata
    gia' rispetta (due soli argomenti, mai un terzo) resta INVARIATO - nessun plugin esistente o
    generato dalla Skill Forge deve sapere che questo proxy esiste. `__getattr__` inoltra
    qualunque altro metodo (has_skill/get_skill) se mai un plugin li chiamasse."""

    def __init__(self, real_registry, plugin_path: str):
        self._real_registry = real_registry
        self._plugin_path = plugin_path

    def register_skill(self, intent, skill) -> None:
        self._real_registry.register_skill(intent, skill, plugin_path=self._plugin_path)

    def __getattr__(self, name):
        return getattr(self._real_registry, name)


def load_plugin_file(registry, plugin_file: Path, logger: logging.Logger | None = None) -> bool:
    """Carica un singolo plugin (v3.0: usato anche dalla fucina per attivare a caldo una skill
    appena generata). True se register() e' stata eseguita senza errori."""
    plugin_file = Path(plugin_file)
    try:
        spec = importlib.util.spec_from_file_location(f"jake_plugin_{plugin_file.stem}", plugin_file)
        # spec_from_file_location/spec.loader possono essere None per un file che non sembra un
        # modulo valido (raro, ma documentato): un plugin del genere e' comunque da trattare come
        # rotto, non da far esplodere con un AttributeError generico - stesso esito pratico
        # (except Exception sotto), ma esplicito invece di affidarsi a un'eccezione accidentale.
        if spec is None or spec.loader is None:
            raise ImportError(f"impossibile determinare lo spec del modulo per {plugin_file}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        register = getattr(module, "register", None)
        if register is None:
            if logger:
                logger.warning("Plugin %s ignorato: manca una funzione register(registry).", plugin_file.name)
            return False

        register(_PluginPathTrackingRegistry(registry, str(plugin_file)))
        return True
    except Exception:
        if logger:
            logger.exception("Errore caricando il plugin %s", plugin_file.name)
        return False


def load_plugins(registry, plugins_dir: Path | None = None, logger: logging.Logger | None = None) -> list[str]:
    """Carica plugin di terze parti (v2.0: skill/plugin installabili).

    Ogni file .py dentro plugins_dir con una funzione register(registry) viene importato e
    registrato via registry.register_skill(...). Un plugin rotto non deve mai impedire
    l'avvio di Jake: l'errore viene isolato e loggato, non propagato."""
    directory = Path(plugins_dir) if plugins_dir else Path(__file__).resolve().parent.parent / "plugins"
    directory.mkdir(parents=True, exist_ok=True)

    loaded = []
    for plugin_file in sorted(directory.glob("*.py")):
        if plugin_file.stem.startswith("_"):
            continue
        if load_plugin_file(registry, plugin_file, logger=logger):
            loaded.append(plugin_file.stem)

    return loaded


class _StagingRegistry:
    """Registro visto da un pacchetto con manifest durante register() (F8.1): raccoglie le registrazioni SENZA
    toccare il registro vero. Solo se gli intent registrati coincidono ESATTAMENTE con quelli dichiarati nel
    manifest vengono poi trasferiti (`commit`): un pacchetto che ne registra uno in piu' o in meno non lascia
    nulla a meta'."""

    def __init__(self, real_registry, plugin_path: str, declared: frozenset[str]):
        self._real_registry = real_registry
        self._plugin_path = plugin_path
        self._declared = declared
        self.staged: dict[str, object] = {}

    def register_skill(self, intent, skill) -> None:
        if intent not in self._declared:
            raise ManifestError([f"il pacchetto registra l'intent '{intent}' che non dichiara nel manifest"])
        if intent in self.staged:
            raise ManifestError([f"il pacchetto registra due volte l'intent '{intent}'"])
        self.staged[intent] = skill

    def missing(self) -> frozenset[str]:
        return self._declared - set(self.staged)

    def commit(self) -> None:
        for intent, skill in self.staged.items():
            self._real_registry.register_skill(intent, skill, plugin_path=self._plugin_path)

    def __getattr__(self, name):
        return getattr(self._real_registry, name)


@dataclass
class PackageLoadResult:
    ok: bool
    manifest: Manifest | None = None
    errors: list[str] = field(default_factory=list)
    # True se il codice del pacchetto e' stato importato (falso per ogni rifiuto dovuto al manifest).
    imported: bool = False


def load_skill_package(registry, directory: Path, env: Environment | None = None,
                       logger: logging.Logger | None = None) -> PackageLoadResult:
    """Carica una skill impacchettata con `skill.json` (F8.1). Il manifest e' validato PRIMA di importare una sola riga
    del pacchetto: una skill incompleta, incompatibile, con capability sconosciuta o che collide con un intent
    esistente e' rifiutata senza che il suo codice giri. Le registrazioni sono atomiche (vedi _StagingRegistry)."""
    directory = Path(directory)
    existing = frozenset(getattr(registry, "skills", {}) or ())
    try:
        manifest = validate_package_dir(directory, env, existing)
    except ManifestError as exc:
        if logger:
            logger.warning("Pacchetto %s rifiutato: %s", directory.name, "; ".join(exc.errors))
        return PackageLoadResult(False, None, exc.errors)
    entry = directory / manifest.entry
    staging = _StagingRegistry(registry, str(entry), manifest.intent_names())
    imported = False
    try:
        spec = importlib.util.spec_from_file_location(f"jake_skill_{manifest.id.replace('.', '_')}", entry)
        if spec is None or spec.loader is None:
            raise ImportError(f"impossibile determinare lo spec del modulo per {entry}")
        module = importlib.util.module_from_spec(spec)
        imported = True
        spec.loader.exec_module(module)
        register = getattr(module, "register", None)
        if register is None:
            raise ManifestError(["il pacchetto non espone register(registry)"])
        register(staging)
        if staging.missing():
            raise ManifestError([f"intent dichiarati ma non registrati: {sorted(staging.missing())}"])
        staging.commit()
    except ManifestError as exc:
        return PackageLoadResult(False, manifest, exc.errors, imported)
    except Exception as exc:
        if logger:
            logger.exception("Errore caricando il pacchetto %s", directory.name)
        return PackageLoadResult(False, manifest, [f"errore nel pacchetto: {type(exc).__name__}"], imported)
    return PackageLoadResult(True, manifest, [], imported)
