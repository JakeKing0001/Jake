import importlib.util
import logging
from pathlib import Path


def load_plugins(registry, plugins_dir: Path = None, logger: logging.Logger = None) -> list[str]:
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
        try:
            spec = importlib.util.spec_from_file_location(f"jake_plugin_{plugin_file.stem}", plugin_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            register = getattr(module, "register", None)
            if register is None:
                if logger:
                    logger.warning("Plugin %s ignorato: manca una funzione register(registry).", plugin_file.name)
                continue

            register(registry)
            loaded.append(plugin_file.stem)
        except Exception:
            if logger:
                logger.exception("Errore caricando il plugin %s", plugin_file.name)

    return loaded
