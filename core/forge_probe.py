"""Sonda eseguita in un processo separato (a integrita' ridotta, quando disponibile - vedi
`core/process_sandbox.py`) da `SkillForge._sandbox_import()`. Riceve il codice del plugin
candidato tramite un file di input e scrive l'esito come JSON su un file di output: niente
stdin/stdout/stderr, perche' un processo a integrita' Low puo' comunque leggere/ereditare le
pipe standard in modi poco affidabili, mentre un file con un mandatory label esplicito e' il
canale verificato nei proof-of-concept di questa sessione.

Non importare nulla di internal a Jake qui dentro se non tramite sys.path: questo file gira
come script standalone (`python forge_probe.py <input> <output>`), non come parte del pacchetto
`core`, cosi' da restare eseguibile anche a integrita' ridotta senza dipendere da import
relativi."""
import json
import sys


def main(input_path: str, output_path: str, project_root: str | None = None) -> None:
    if project_root:
        sys.path.insert(0, project_root)
    result: dict[str, object] = {"ok": False, "error": None, "registered": []}
    try:
        with open(input_path, "r", encoding="utf-8") as handle:
            code = handle.read()
        spec_module: dict = {}
        exec(compile(code, "forge_probe_plugin.py", "exec"), spec_module)
        registered = {}

        class _Registry:
            def register_skill(self, intent, skill):
                registered[intent] = skill

        register = spec_module.get("register")
        if register is None:
            raise AssertionError("il plugin non definisce register(registry)")
        register(_Registry())
        if not registered:
            raise AssertionError("register() non ha registrato nulla")
        for _intent, skill in registered.items():
            outcome = skill.execute({})
            if not hasattr(outcome, "success"):
                raise AssertionError("execute non ritorna SkillResult")
            if outcome.success and hasattr(skill, "format_result"):
                text = skill.format_result(outcome)
                if not isinstance(text, str):
                    raise AssertionError("format_result non ritorna una stringa")
        result["ok"] = True
        result["registered"] = list(registered)
    except Exception as exc:  # la sonda deve sempre scrivere un esito, mai propagare
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    try:
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle)
    except OSError:
        pass


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
