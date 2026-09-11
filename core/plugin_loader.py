import importlib.util
import logging
from pathlib import Path


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

        register(registry)
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
