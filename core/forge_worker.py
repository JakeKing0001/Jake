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
Jake", non "isolare una skill forgiata dalle altre")."""
import json
import sys


def _load_skills(plugin_paths: list) -> dict:
    skills: dict = {}

    class _Registry:
        def register_skill(self, intent, skill):
            skills[intent] = skill

    registry = _Registry()
    for plugin_path in plugin_paths:
        try:
            with open(plugin_path, "r", encoding="utf-8") as handle:
                code = handle.read()
            spec_module: dict = {}
            exec(compile(code, plugin_path, "exec"), spec_module)
            register = spec_module.get("register")
            if register is not None:
                register(registry)
        except Exception:
            continue  # un plugin rotto al caricamento non deve impedire agli altri di servire
    return skills


def _respond(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(project_root: str, plugin_paths: list) -> None:
    if project_root:
        sys.path.insert(0, project_root)
    skills = _load_skills(plugin_paths)
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
        try:
            result = skill.execute(parameters)
            _respond({"success": bool(result.success), "data": dict(result.data or {}), "error": result.error})
        except Exception as exc:
            _respond({"success": False, "data": {}, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "", sys.argv[2:])
