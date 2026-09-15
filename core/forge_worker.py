"""Worker persistente per skill forgiate (F1.6, "sandbox permanente per l'esecuzione delle skill
installate - Job Object/AppContainer"). Diverso da core/forge_probe.py (un processo USA E GETTA
per il solo passo di validazione della Forge, sempre execute({}) su parametri vuoti): questo
resta VIVO tra una chiamata e l'altra e serve OGNI invocazione reale, con parametri veri, di una
skill installata dopo l'approvazione - il gap dichiarato esplicitamente in
core/process_sandbox.py ("protegge solo il passo di validazione della Forge... non l'esecuzione
permanente in produzione di una skill gia' installata, che gira ancora con i privilegi normali").

Protocollo: una riga JSON per richiesta su stdin, una riga JSON per risposta su stdout - niente
altro sui due canali (mai un print() qui dentro fuori da _respond(), sporcherebbe il protocollo).
Richiesta: {"intent": str, "parameters": dict} oppure {"shutdown": true} per chiudere pulito
(anche l'EOF su stdin, senza un messaggio esplicito, ha lo stesso effetto - il chiamante lo usa
quando deve terminare in fretta). Risposta: {"success": bool, "data": dict, "error": str|None} -
le stesse tre chiavi di core.skill_result.SkillResult, cosi' core/sandboxed_skill_worker.py puo'
ricostruirne una senza un formato inventato apposta. Una riga malformata o un'eccezione durante
l'esecuzione producono comunque UNA risposta (mai una richiesta senza risposta, il chiamante
aspetta con un timeout): {"success": false, "data": {}, "error": "..."}.

Non importa nulla di interno a Jake se non tramite sys.path (project_root passato come primo
argomento): gira come script standalone, non come parte del pacchetto core, per restare
eseguibile anche a integrita' ridotta senza dipendere da import relativi - stesso principio di
forge_probe.py. Carica OGNI plugin gia' installato (non uno per chiamata): il worker e' condiviso
da tutte le skill forgiate, un solo processo sandboxato da gestire invece di N - lo stesso confine
di sicurezza (integrita' ridotta + Job Object, applicati dal chiamante) protegge comunque il
processo principale di Jake da qualunque delle skill che gira qui dentro, anche se piu' skill
forgiate condividono lo stesso worker tra loro (accettabile: il modello di minaccia e' "proteggere
Jake", non "isolare una skill forgiata dalle altre").

F1.6.5 ("montare soltanto directory dichiarate nel manifest") - gate a LIVELLO APPLICATIVO,
esplicitamente NON garantito dal kernel, decisione presa dopo un'indagine dedicata: un vero
confine imposto dal sistema operativo richiederebbe AppContainer (F1.6.4, gia' scartato - pywin32
non espone affatto le API necessarie) o un token ristretto via `CreateRestrictedToken` (pywin32 LO
espone, a differenza di AppContainer, ma `CreateProcessAsUser` con quel token fallisce con
ERROR_PRIVILEGE_NOT_HELD - un problema Windows irrisolto, non un vicolo cieco dimostrato ma
nemmeno una via nota e delimitata). **Decisione esplicita dell'utente**: costruire questo gate
applicativo invece di aspettare o investire tempo indefinito nel token ristretto, dichiarando
apertamente il limite - una skill forgiata gira come codice Python arbitrario in questo STESSO
processo, quindi puo' in teoria aggirare `builtins.open`/`os.open` (gli unici due punti
intercettati qui) chiamando `ctypes`/una syscall diretta/un sottoprocesso esterno (`cmd /c type`):
questo gate ferma un accesso a file non dichiarato fatto con le API Python normali - lo stesso
principio "degrado elegante ma MAI silenzioso" del resto del modulo, qui applicato dichiarando la
copertura invece di implicare una garanzia piu' forte di quella vera. Integrita' Low + Job Object
(gia' in produzione) restano la VERA difesa kernel-enforced contro scritture/CPU/memoria/processi;
questo gate aggiunge solo una difesa in profondita' sulle LETTURE, che altrimenti non avevano
alcuna restrizione oltre ai permessi NTFS gia' concessi all'account che esegue Jake.

Manifest dichiarato dalla skill stessa, un dizionario opzionale a livello di modulo nel file del
plugin: `MANIFEST = {"allowed_paths": ["C:/percorso/assoluto"]}`. Assente o vuoto per default:
NESSUN accesso a file concesso durante `execute()` (nega per default, stesso principio gia' usato
ovunque in F1) - una skill che non dichiara nulla nel manifest ma prova comunque ad aprire un file
riceve un `PermissionError` invece di un accesso silenzioso. Il gate e' installato SOLO per la
finestra di `execute()` (vedi `main()` sotto): il CARICAMENTO del plugin stesso (leggere il file
.py, gli import a livello di modulo) resta libero, cosi' come qualunque lettura che Jake/Python
stesso debba fare per funzionare - non e' la skill che gira ancora in quel momento."""
import builtins
import json
import os
import sys

# F1.6.5: percorsi assoluti concessi alla CHIAMATA CORRENTE, svuotati per default (nega per
# default) e impostati da main() subito prima di ogni skill.execute() in base al manifest della
# skill che sta per girare - mai lasciati "aperti" tra una chiamata e l'altra.
_active_allowed_paths: list[str] = []
_real_open = builtins.open
_real_os_open = os.open


def _path_is_allowed(path) -> bool:
    try:
        resolved = os.path.abspath(os.fspath(path))
    except TypeError:
        return True  # non un percorso vero (es. un file descriptor int): non e' questo il confine da controllare
    return any(
        resolved == allowed or resolved.startswith(allowed + os.sep)
        for allowed in _active_allowed_paths
    )


def _guarded_open(file, *args, **kwargs):
    if isinstance(file, (str, bytes, os.PathLike)) and not _path_is_allowed(file):
        raise PermissionError(f"F1.6.5: percorso non dichiarato nel manifest della skill: {file}")
    return _real_open(file, *args, **kwargs)


def _guarded_os_open(path, *args, **kwargs):
    if isinstance(path, (str, bytes, os.PathLike)) and not _path_is_allowed(path):
        raise PermissionError(f"F1.6.5: percorso non dichiarato nel manifest della skill: {path}")
    return _real_os_open(path, *args, **kwargs)


def _install_path_gate() -> None:
    """Chiamata una sola volta, DOPO che tutti i plugin sono gia' stati caricati (vedi main()):
    il caricamento stesso non deve mai passare da questo gate, solo l'esecuzione vera di una
    skill. `builtins.open` copre anche `pathlib.Path.open/read_text/write_text/read_bytes/
    write_bytes` (delegano tutti a `io.open`, lo stesso oggetto di `builtins.open` in CPython)."""
    builtins.open = _guarded_open
    os.open = _guarded_os_open


def _load_skills(plugin_paths: list) -> tuple[dict, dict]:
    skills: dict = {}
    manifests: dict = {}  # intent -> lista di percorsi assoluti concessi (F1.6.5)

    class _Registry:
        def __init__(self, allowed_paths: list[str]):
            self._allowed_paths = allowed_paths

        def register_skill(self, intent, skill):
            skills[intent] = skill
            manifests[intent] = self._allowed_paths

    for plugin_path in plugin_paths:
        try:
            with open(plugin_path, "r", encoding="utf-8") as handle:
                code = handle.read()
            spec_module: dict = {}
            exec(compile(code, plugin_path, "exec"), spec_module)
            manifest = spec_module.get("MANIFEST") or {}
            allowed_paths = [os.path.abspath(p) for p in manifest.get("allowed_paths", [])]
            registry = _Registry(allowed_paths)
            register = spec_module.get("register")
            if register is not None:
                register(registry)
        except Exception:
            continue  # un plugin rotto al caricamento non deve impedire agli altri di servire
    return skills, manifests


def _respond(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(project_root: str, plugin_paths: list) -> None:
    if project_root:
        sys.path.insert(0, project_root)
    skills, manifests = _load_skills(plugin_paths)
    # F1.6.5: il gate va installato SOLO dopo che il caricamento (sopra) e' finito - vedi il
    # docstring del modulo sul perche'.
    _install_path_gate()
    _respond({"success": True, "data": {}, "error": None, "ready": True})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _respond({"success": False, "data": {}, "error": "richiesta non e' JSON valido"})
            continue
        if request.get("shutdown"):
            return
        intent = request.get("intent")
        parameters = request.get("parameters") or {}
        skill = skills.get(intent)
        if skill is None:
            _respond({"success": False, "data": {}, "error": f"intent sconosciuto al worker: {intent}"})
            continue
        # F1.6.5: il manifest della skill che sta per girare, mai lasciato impostato dalla
        # chiamata precedente - svuotato subito dopo (finally sotto), non solo prima di questa.
        _active_allowed_paths[:] = manifests.get(intent, [])
        try:
            result = skill.execute(parameters)
            _respond({"success": bool(result.success), "data": dict(result.data or {}), "error": result.error})
        except Exception as exc:
            _respond({"success": False, "data": {}, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            _active_allowed_paths.clear()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "", sys.argv[2:])
