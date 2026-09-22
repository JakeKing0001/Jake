"""Manifest di una skill installabile (F8.1): il contratto che una skill di terze parti o forgiata deve dichiarare
PRIMA che una sola riga del suo codice venga importata.

Il manifest e' dati, non codice: JSON che questo modulo valida in modo esaustivo (raccoglie TUTTI gli errori, non
solo il primo, cosi' chi scrive la skill li corregge in un giro solo). Riusa i vocabolari che Jake ha gia':
`RiskLevel` (core/risk.py, le cinque classi di rischio) e `EFFECT_CLASSES` (core/action_contracts.py) - nessun
secondo elenco di livelli da tenere allineato.

Cosa rifiuta (F8.1, criterio di uscita "loader rifiuta skill incompleta, incompatibile o con capability
sconosciuta"):
- campi obbligatori mancanti, tipi sbagliati, chiavi sconosciute (un refuso non deve diventare un campo ignorato);
- id/versione/intent con formato non valido, intent che collidono con quelli integrati di Jake;
- una capability che non e' nel vocabolario `KNOWN_CAPABILITIES`;
- rischio incoerente con effetto e capability (una skill "read_only" che scrive file, una "destructive" senza undo
  dichiarato, una che modifica senza verifier);
- JSON Schema di input/output/errori fuori dal sottoinsieme supportato (un `$ref` o un `oneOf` sarebbe ignorato in
  silenzio da un validatore parziale: qui e' un errore esplicito) o con fixture che non lo rispettano;
- compatibilita' con Jake/Python/Windows non soddisfatta dall'ambiente indicato;
- dipendenze non espresse come intervallo di versioni (niente URL, VCS, percorsi);
- test e fixture assenti; hook di migrazione/disinstallazione fuori dai limiti (solo due, solo dati propri, tempo
  massimo).

Non esegue nulla del pacchetto: `validate_package_dir` controlla anche che entry e file di test esistano DENTRO la
cartella, senza importarli."""
from __future__ import annotations

import copy
import json
import platform
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.action_contracts import EFFECT_CLASSES, INTENT_EFFECT_CLASS
from core.risk import SKILL_RISK, RiskLevel, is_at_least
from core.version import VERSION

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_FILENAME = "skill.json"

# Vocabolario delle capability (derivato da quelle che PolicyEngine gia' sa restringere - filesystem, web, app,
# contatti, dispositivi smart, rete - piu' i canali sensibili del PC). Una capability qui e' un permesso che la
# skill CHIEDE; il nome non concede nulla da solo.
KNOWN_CAPABILITIES = frozenset({
    "filesystem.read", "filesystem.write", "network", "web", "apps", "contacts", "smart_home", "clipboard.read",
    "clipboard.write",
    "screen", "input", "microphone", "process", "system", "notifications", "memory.read", "memory.write",
    "subprocess",
})
# Capability che, se dichiarate, escludono un rischio "read_only" (cambiano qualcosa o escono dal PC).
_MUTATING_CAPABILITIES = frozenset({
    "filesystem.write", "network", "web", "apps", "contacts", "smart_home", "input", "process", "system",
    "subprocess", "memory.write", "clipboard.write",
})
# Capability che implicano almeno un'azione verso l'esterno.
_EXTERNAL_CAPABILITIES = frozenset({"network", "web", "contacts", "smart_home", "subprocess"})
_ADMIN_CAPABILITIES = frozenset({"system", "process"})

PROVENANCE_KINDS = frozenset({"builtin", "user", "forge", "registry"})
VERIFIER_KINDS = frozenset({"none", "declarative", "function"})
HOOK_NAMES = frozenset({"migrate", "uninstall"})
MAX_HOOK_TIMEOUT_SECONDS = 30
MAX_SCHEMA_DEPTH = 6

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_-]*){1,3}$")
_INTENT_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CLAUSE_RE = re.compile(r"^(>=|<=|==|>|<)\s*(\d+(?:\.\d+){0,2})$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})?)?$")

_TOP_KEYS = {
    "schema_version", "id", "name", "version", "description", "author", "provenance", "compatibility",
    "dependencies", "entry", "tests", "intents", "hooks", "changelog",
}
_INTENT_KEYS = {
    "intent", "description", "risk", "effect_class", "capabilities", "verifier", "undo", "input_schema",
    "output_schema", "errors", "fixtures",
}


class ManifestError(ValueError):
    """Manifest non valido. `errors` contiene TUTTI i problemi trovati, ciascuno con il suo percorso."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


# ---- versioni e compatibilita' -----------------------------------------------------------------------------------------


def parse_version(text: str) -> tuple[int, int, int]:
    parts = [int(part) for part in text.split(".")]
    return tuple((parts + [0, 0, 0])[:3])  # type: ignore[return-value]


def spec_is_valid(spec) -> bool:
    return isinstance(spec, str) and bool(spec.strip()) and all(
        _CLAUSE_RE.match(clause.strip()) for clause in spec.split(","))


def satisfies(version: str, spec: str) -> bool:
    """`version` soddisfa TUTTE le clausole di `spec` (">=5.9,<6"). Una spec non valida non e' mai soddisfatta."""
    if not spec_is_valid(spec):
        return False
    actual = parse_version(version)
    for clause in spec.split(","):
        match = _CLAUSE_RE.match(clause.strip())
        assert match is not None
        operator, wanted = match.group(1), parse_version(match.group(2))
        ok = {">=": actual >= wanted, "<=": actual <= wanted, "==": actual == wanted,
              ">": actual > wanted, "<": actual < wanted}[operator]
        if not ok:
            return False
    return True


@dataclass(frozen=True)
class Environment:
    """L'ambiente contro cui si valuta la compatibilita': iniettabile, cosi' i test non dipendono dalla macchina."""

    jake_version: str = VERSION
    python_version: str = field(default_factory=lambda: ".".join(str(part) for part in sys.version_info[:3]))
    windows_version: str | None = field(default_factory=lambda: _detect_windows_version())


def _detect_windows_version() -> str | None:
    if sys.platform != "win32":
        return None
    match = re.match(r"^(\d+)\.(\d+)", platform.version())
    return f"{match.group(1)}.{match.group(2)}" if match else None


# ---- sottoinsieme di JSON Schema ------------------------------------------------------------------------------------------

_SCHEMA_TYPES = {"string", "integer", "number", "boolean", "object", "array", "null"}
_SCHEMA_KEYWORDS = {
    "type", "properties", "required", "additionalProperties", "enum", "minimum", "maximum", "minLength",
    "maxLength", "items", "minItems", "maxItems", "description", "default",
}


def check_schema(schema, path: str = "schema", depth: int = 0) -> list[str]:
    """Errori di buona formazione di uno schema nel sottoinsieme supportato. Le parole chiave non supportate sono un
    errore (mai ignorate)."""
    if not isinstance(schema, dict):
        return [f"{path}: deve essere un oggetto"]
    if depth > MAX_SCHEMA_DEPTH:
        return [f"{path}: annidamento oltre {MAX_SCHEMA_DEPTH} livelli"]
    errors = [f"{path}: parola chiave non supportata '{key}'" for key in sorted(set(schema) - _SCHEMA_KEYWORDS)]
    kind = schema.get("type")
    if kind not in _SCHEMA_TYPES:
        errors.append(f"{path}.type: obbligatorio, uno tra {sorted(_SCHEMA_TYPES)}")
    if "properties" in schema:
        props = schema["properties"]
        if kind != "object" or not isinstance(props, dict):
            errors.append(f"{path}.properties: valido solo per type=object e deve essere un oggetto")
        else:
            for name, sub in props.items():
                errors.extend(check_schema(sub, f"{path}.properties.{name}", depth + 1))
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            errors.append(f"{path}.required: deve essere una lista di stringhe")
        elif isinstance(schema.get("properties"), dict):
            errors.extend(f"{path}.required: '{item}' non e' tra le properties" for item in required if item not in schema["properties"])
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        errors.append(f"{path}.additionalProperties: deve essere true/false")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        errors.append(f"{path}.enum: deve essere una lista non vuota")
    for key in ("minimum", "maximum"):
        if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], (int, float))):
            errors.append(f"{path}.{key}: deve essere un numero")
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], int) or schema[key] < 0):
            errors.append(f"{path}.{key}: deve essere un intero >= 0")
    if "items" in schema:
        if kind != "array":
            errors.append(f"{path}.items: valido solo per type=array")
        else:
            errors.extend(check_schema(schema["items"], f"{path}.items", depth + 1))
    return errors


def validate_instance(schema: dict, value, path: str = "$") -> list[str]:
    """Errori di `value` rispetto a `schema` (gia' verificato con `check_schema`)."""
    errors: list[str] = []
    kind = schema.get("type")
    if kind == "string" and not isinstance(value, str):
        return [f"{path}: atteso string"]
    if kind == "boolean" and not isinstance(value, bool):
        return [f"{path}: atteso boolean"]
    if kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        return [f"{path}: atteso integer"]
    if kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        return [f"{path}: atteso number"]
    if kind == "null" and value is not None:
        return [f"{path}: atteso null"]
    if kind == "object" and not isinstance(value, dict):
        return [f"{path}: atteso object"]
    if kind == "array" and not isinstance(value, list):
        return [f"{path}: atteso array"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: valore fuori da enum")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: sotto il minimo {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: sopra il massimo {schema['maximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: piu' corto di {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: piu' lungo di {schema['maxLength']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: meno di {schema['minItems']} elementi")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: piu' di {schema['maxItems']} elementi")
        if "items" in schema:
            for index, item in enumerate(value):
                errors.extend(validate_instance(schema["items"], item, f"{path}[{index}]"))
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}.{name}: obbligatorio")
        for name, item in value.items():
            if name in props:
                errors.extend(validate_instance(props[name], item, f"{path}.{name}"))
            elif schema.get("additionalProperties", True) is False:
                errors.append(f"{path}.{name}: proprieta' non ammessa")
    return errors


# ---- manifest ----------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IntentSpec:
    intent: str
    description: str
    risk: RiskLevel
    effect_class: str
    capabilities: tuple[str, ...]
    verifier: dict
    undo: dict
    input_schema: dict
    output_schema: dict
    errors: tuple[str, ...]
    fixtures: tuple[dict, ...]


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    description: str
    author: dict
    provenance: dict
    compatibility: dict
    dependencies: tuple[dict, ...]
    entry: str
    tests: dict
    intents: tuple[IntentSpec, ...]
    hooks: dict
    raw: dict
    changelog: tuple[dict, ...] = ()

    def intent_names(self) -> frozenset[str]:
        return frozenset(spec.intent for spec in self.intents)

    def capabilities(self) -> frozenset[str]:
        return frozenset(cap for spec in self.intents for cap in spec.capabilities)

    def highest_risk(self) -> RiskLevel:
        level = RiskLevel.READ_ONLY
        for spec in self.intents:
            if is_at_least(spec.risk, level):
                level = spec.risk
        return level


def _is_relative_inside(path: str) -> bool:
    if not isinstance(path, str) or not path or path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path):
        return False
    return ".." not in re.split(r"[\\/]", path)


def _str_field(data: dict, key: str, path: str, errors: list[str], *, max_len: int = 200) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path}.{key}: obbligatorio, testo non vuoto")
        return ""
    if len(value) > max_len:
        errors.append(f"{path}.{key}: oltre {max_len} caratteri")
    return value.strip()


def _check_unknown(data: dict, allowed: set[str], path: str, errors: list[str]) -> None:
    errors.extend(f"{path}: chiave sconosciuta '{key}'" for key in sorted(set(data) - allowed))


def _validate_intent(raw, index: int, errors: list[str], existing_intents: frozenset[str]) -> IntentSpec | None:
    path = f"intents[{index}]"
    if not isinstance(raw, dict):
        errors.append(f"{path}: deve essere un oggetto")
        return None
    before = len(errors)
    _check_unknown(raw, _INTENT_KEYS, path, errors)
    name = raw.get("intent")
    if not isinstance(name, str) or not _INTENT_RE.match(name):
        errors.append(f"{path}.intent: formato non valido (MAIUSCOLO_CON_UNDERSCORE, 3-64 caratteri)")
        name = ""
    elif name in existing_intents or name in SKILL_RISK or name in INTENT_EFFECT_CLASS:
        errors.append(f"{path}.intent: '{name}' esiste gia' tra gli intent di Jake")
    description = _str_field(raw, "description", path, errors, max_len=300)

    risk_raw = raw.get("risk")
    try:
        risk = RiskLevel(risk_raw)
    except ValueError:
        errors.append(f"{path}.risk: obbligatorio, uno tra {[level.value for level in RiskLevel]}")
        risk = RiskLevel.ADMIN
    effect = raw.get("effect_class")
    if effect not in EFFECT_CLASSES:
        errors.append(f"{path}.effect_class: obbligatorio, uno tra {sorted(EFFECT_CLASSES)}")
        effect = ""

    caps_raw = raw.get("capabilities")
    caps: tuple[str, ...] = ()
    if not isinstance(caps_raw, list) or not all(isinstance(item, str) for item in caps_raw):
        errors.append(f"{path}.capabilities: obbligatorio, lista di stringhe (anche vuota)")
    else:
        unknown = [item for item in caps_raw if item not in KNOWN_CAPABILITIES]
        errors.extend(f"{path}.capabilities: capability sconosciuta '{item}'" for item in unknown)
        if len(set(caps_raw)) != len(caps_raw):
            errors.append(f"{path}.capabilities: duplicati")
        caps = tuple(caps_raw)

    verifier = raw.get("verifier")
    if not isinstance(verifier, dict) or verifier.get("kind") not in VERIFIER_KINDS:
        errors.append(f"{path}.verifier: obbligatorio, {{'kind': {sorted(VERIFIER_KINDS)}}}")
        verifier = {}
    else:
        if verifier["kind"] == "function" and not (isinstance(verifier.get("function"), str) and _IDENT_RE.match(verifier["function"])):
            errors.append(f"{path}.verifier.function: nome di funzione obbligatorio per kind=function")
        if verifier["kind"] == "declarative" and not (isinstance(verifier.get("expect"), str) and verifier["expect"].strip()):
            errors.append(f"{path}.verifier.expect: obbligatorio per kind=declarative")
        if verifier["kind"] == "none" and not (isinstance(verifier.get("reason"), str) and verifier["reason"].strip()):
            errors.append(f"{path}.verifier.reason: 'none' richiede il motivo")
    undo = raw.get("undo")
    if not isinstance(undo, dict) or not isinstance(undo.get("supported"), bool):
        errors.append(f"{path}.undo: obbligatorio, {{'supported': true|false, ...}}")
        undo = {}
    elif undo["supported"] and not (isinstance(undo.get("function"), str) and _IDENT_RE.match(undo["function"])):
        errors.append(f"{path}.undo.function: obbligatorio quando supported=true")
    elif not undo["supported"] and not (isinstance(undo.get("reason"), str) and undo["reason"].strip()):
        errors.append(f"{path}.undo.reason: obbligatorio quando supported=false")

    # coerenza rischio / effetto / capability
    if risk == RiskLevel.READ_ONLY:
        if effect and effect != "read":
            errors.append(f"{path}: risk=read_only richiede effect_class=read (trovato '{effect}')")
        bad = sorted(set(caps) & _MUTATING_CAPABILITIES)
        if bad:
            errors.append(f"{path}: risk=read_only e' incompatibile con le capability {bad}")
    elif effect == "read" and not (set(caps) & _MUTATING_CAPABILITIES) and risk != RiskLevel.READ_ONLY:
        errors.append(f"{path}: un effetto 'read' senza capability che modificano non puo' dichiarare rischio {risk.value}")
    if set(caps) & _EXTERNAL_CAPABILITIES and not is_at_least(risk, RiskLevel.EXTERNAL_ACTION):
        errors.append(f"{path}: le capability {sorted(set(caps) & _EXTERNAL_CAPABILITIES)} richiedono rischio almeno external_action")
    if set(caps) & _ADMIN_CAPABILITIES and not is_at_least(risk, RiskLevel.DESTRUCTIVE):
        errors.append(f"{path}: le capability {sorted(set(caps) & _ADMIN_CAPABILITIES)} richiedono rischio almeno destructive")
    if effect in ("delete",) and not is_at_least(risk, RiskLevel.LOCAL_REVERSIBLE):
        errors.append(f"{path}: effect_class=delete richiede un rischio di almeno local_reversible")
    if risk != RiskLevel.READ_ONLY:
        if verifier and verifier.get("kind") == "none":
            errors.append(f"{path}.verifier: una skill che cambia qualcosa (risk={risk.value}) deve avere un verifier")

    input_schema: Any = raw.get("input_schema")
    output_schema: Any = raw.get("output_schema")
    schema_errors = check_schema(input_schema, f"{path}.input_schema") + check_schema(output_schema, f"{path}.output_schema")
    errors.extend(schema_errors)
    if not schema_errors and input_schema.get("type") != "object":
        errors.append(f"{path}.input_schema.type: l'input di una skill e' un oggetto")

    declared_errors: list[str] = []
    errors_raw = raw.get("errors")
    if not isinstance(errors_raw, list):
        errors.append(f"{path}.errors: obbligatorio, lista di {{'code', 'description'}} (anche vuota)")
    else:
        for position, item in enumerate(errors_raw):
            if not (isinstance(item, dict) and isinstance(item.get("code"), str) and _ERROR_CODE_RE.match(item["code"])
                    and isinstance(item.get("description"), str) and item["description"].strip()):
                errors.append(f"{path}.errors[{position}]: serve {{'code': snake_case, 'description': testo}}")
            elif item["code"] in declared_errors:
                errors.append(f"{path}.errors[{position}]: codice duplicato '{item['code']}'")
            else:
                declared_errors.append(item["code"])

    fixtures = raw.get("fixtures")
    checked_fixtures: list[dict] = []
    if not isinstance(fixtures, list) or not fixtures:
        errors.append(f"{path}.fixtures: obbligatorie, almeno una per intent (F8.1.5)")
    else:
        successes = failures = 0
        for position, fixture in enumerate(fixtures):
            fpath = f"{path}.fixtures[{position}]"
            if not (isinstance(fixture, dict) and isinstance(fixture.get("name"), str) and isinstance(fixture.get("input"), dict)):
                errors.append(f"{fpath}: serve {{'name', 'input', 'output' | 'error_code'}}")
                continue
            if ("output" in fixture) == ("error_code" in fixture):
                errors.append(f"{fpath}: esattamente uno tra 'output' e 'error_code'")
                continue
            if not schema_errors:
                errors.extend(f"{fpath}.input {msg}" for msg in validate_instance(input_schema, fixture["input"]) if fixture.get("error_code") is None)
            if "output" in fixture:
                successes += 1
                if not schema_errors:
                    errors.extend(f"{fpath}.output {msg}" for msg in validate_instance(output_schema, fixture["output"]))
            else:
                failures += 1
                if fixture["error_code"] not in declared_errors:
                    errors.append(f"{fpath}.error_code: '{fixture['error_code']}' non e' tra gli errori dichiarati")
            checked_fixtures.append(fixture)
        if not successes:
            errors.append(f"{path}.fixtures: manca una fixture di successo (con 'output')")
        if declared_errors and not failures:
            errors.append(f"{path}.fixtures: sono dichiarati degli errori ma nessuna fixture li esercita")

    if len(errors) > before:
        return None
    return IntentSpec(name, description, risk, effect, caps, verifier, undo, copy.deepcopy(input_schema),
                      copy.deepcopy(output_schema), tuple(declared_errors), tuple(copy.deepcopy(checked_fixtures)))


def _validate_hooks(hooks, errors: list[str]) -> dict:
    if hooks is None:
        return {}
    if not isinstance(hooks, dict):
        errors.append("hooks: deve essere un oggetto")
        return {}
    for name, hook in hooks.items():
        path = f"hooks.{name}"
        if name not in HOOK_NAMES:
            errors.append(f"{path}: hook non ammesso (solo {sorted(HOOK_NAMES)})")
            continue
        if not isinstance(hook, dict):
            errors.append(f"{path}: deve essere un oggetto")
            continue
        _check_unknown(hook, {"function", "timeout_s", "touches"}, path, errors)
        if not (isinstance(hook.get("function"), str) and _IDENT_RE.match(hook["function"])):
            errors.append(f"{path}.function: nome di funzione obbligatorio")
        timeout = hook.get("timeout_s")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= MAX_HOOK_TIMEOUT_SECONDS:
            errors.append(f"{path}.timeout_s: obbligatorio, tra 0 e {MAX_HOOK_TIMEOUT_SECONDS} secondi")
        if hook.get("touches") != "own_data":
            errors.append(f"{path}.touches: un hook puo' toccare solo 'own_data' (i dati della skill)")
    return dict(hooks)


def _validate_changelog(changelog, errors: list[str]) -> tuple[dict, ...]:
    """F8.2.3: note di versione facoltative ({'version', 'notes'}), una per versione."""
    if changelog is None:
        return ()
    if not isinstance(changelog, list):
        errors.append("changelog: deve essere una lista")
        return ()
    seen: set[str] = set()
    for index, item in enumerate(changelog):
        path = f"changelog[{index}]"
        if not (isinstance(item, dict) and set(item) == {"version", "notes"} and isinstance(item["version"], str)
                and _VERSION_RE.match(item["version"]) and isinstance(item["notes"], str) and item["notes"].strip()
                and len(item["notes"]) <= 500):
            errors.append(f"{path}: serve {{'version': X.Y.Z, 'notes': testo fino a 500 caratteri}}")
        elif item["version"] in seen:
            errors.append(f"{path}.version: '{item['version']}' dichiarata due volte")
        else:
            seen.add(item["version"])
    return tuple(copy.deepcopy(item) for item in changelog) if not errors else ()


def validate_manifest(data, env: Environment | None = None, existing_intents=()) -> list[str]:
    """Tutti gli errori del manifest (lista vuota = valido). Non importa e non esegue nulla."""
    try:
        parse_manifest(data, env, existing_intents)
    except ManifestError as exc:
        return exc.errors
    return []


def parse_manifest(data, env: Environment | None = None, existing_intents=()) -> Manifest:
    env = env or Environment()
    existing = frozenset(existing_intents)
    errors: list[str] = []
    if not isinstance(data, dict):
        raise ManifestError(["manifest: deve essere un oggetto JSON"])
    _check_unknown(data, _TOP_KEYS, "manifest", errors)
    if data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append(f"schema_version: obbligatorio, deve essere {MANIFEST_SCHEMA_VERSION}")

    skill_id = data.get("id")
    if not isinstance(skill_id, str) or not _ID_RE.match(skill_id):
        errors.append("id: formato non valido (es. 'davide.meteo', minuscolo, 2-4 segmenti separati da punti)")
        skill_id = ""
    name = _str_field(data, "name", "manifest", errors, max_len=80)
    version = data.get("version")
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        errors.append("version: obbligatoria, formato MAGGIORE.MINORE.PATCH")
        version = ""
    description = _str_field(data, "description", "manifest", errors, max_len=500)

    author = data.get("author")
    if not isinstance(author, dict):
        errors.append("author: obbligatorio, {'name': ...}")
        author = {}
    else:
        _check_unknown(author, {"name", "contact"}, "author", errors)
        _str_field(author, "name", "author", errors, max_len=80)
    provenance = data.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("provenance: obbligatoria, {'kind', 'source', 'created_at'}")
        provenance = {}
    else:
        _check_unknown(provenance, {"kind", "source", "created_at", "sha256"}, "provenance", errors)
        if provenance.get("kind") not in PROVENANCE_KINDS:
            errors.append(f"provenance.kind: obbligatorio, uno tra {sorted(PROVENANCE_KINDS)}")
        _str_field(provenance, "source", "provenance", errors, max_len=300)
        created = provenance.get("created_at")
        if not isinstance(created, str) or not _ISO_DATE_RE.match(created):
            errors.append("provenance.created_at: obbligatoria, data ISO 8601")
        if "sha256" in provenance and not (isinstance(provenance["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", provenance["sha256"])):
            errors.append("provenance.sha256: 64 caratteri esadecimali minuscoli")

    compat = data.get("compatibility")
    if not isinstance(compat, dict):
        errors.append("compatibility: obbligatoria, {'jake', 'python', 'windows'}")
        compat = {}
    else:
        _check_unknown(compat, {"jake", "python", "windows"}, "compatibility", errors)
        for key, actual in (("jake", env.jake_version), ("python", env.python_version), ("windows", env.windows_version)):
            spec: Any = compat.get(key)
            if not spec_is_valid(spec):
                errors.append(f"compatibility.{key}: obbligatoria, intervallo come '>=5.9,<6'")
            elif actual is None:
                errors.append(f"compatibility.{key}: l'ambiente non e' Windows, la skill non e' compatibile")
            elif not satisfies(actual, spec):
                errors.append(f"compatibility.{key}: richiede '{spec}', l'ambiente ha {actual} (incompatibile)")

    dependencies = data.get("dependencies", [])
    if not isinstance(dependencies, list):
        errors.append("dependencies: deve essere una lista")
        dependencies = []
    seen_deps: set[str] = set()
    for index, dep in enumerate(dependencies):
        dpath = f"dependencies[{index}]"
        if not (isinstance(dep, dict) and set(dep) == {"name", "spec"}):
            errors.append(f"{dpath}: serve esattamente {{'name', 'spec'}}")
            continue
        if not (isinstance(dep["name"], str) and _PACKAGE_NAME_RE.match(dep["name"])):
            errors.append(f"{dpath}.name: nome di pacchetto non valido (niente URL, VCS o percorsi)")
        elif dep["name"].lower() in seen_deps:
            errors.append(f"{dpath}.name: '{dep['name']}' dichiarata due volte")
        else:
            seen_deps.add(dep["name"].lower())
        if not spec_is_valid(dep["spec"]):
            errors.append(f"{dpath}.spec: solo intervalli di versione come '>=2.31,<3'")

    entry = data.get("entry")
    if not (isinstance(entry, str) and entry.endswith(".py") and _is_relative_inside(entry)):
        errors.append("entry: obbligatorio, percorso .py relativo dentro il pacchetto (niente '..' ne' percorsi assoluti)")
        entry = ""
    tests = data.get("tests")
    if not isinstance(tests, dict) or not isinstance(tests.get("files"), list) or not tests["files"]:
        errors.append("tests.files: obbligatori, almeno un file di test (F8.1.5)")
        tests = {}
    else:
        _check_unknown(tests, {"files"}, "tests", errors)
        for item in tests["files"]:
            if not (isinstance(item, str) and item.endswith(".py") and _is_relative_inside(item)):
                errors.append(f"tests.files: '{item}' non e' un percorso .py relativo dentro il pacchetto")

    specs: list[IntentSpec] = []
    intents = data.get("intents")
    if not isinstance(intents, list) or not intents:
        errors.append("intents: obbligatori, almeno un intent")
    else:
        names_seen: set[str] = set()
        for index, raw_intent in enumerate(intents):
            spec = _validate_intent(raw_intent, index, errors, existing)
            if spec is not None:
                if spec.intent in names_seen:
                    errors.append(f"intents[{index}].intent: '{spec.intent}' dichiarato due volte")
                names_seen.add(spec.intent)
                specs.append(spec)
    hooks = _validate_hooks(data.get("hooks"), errors)
    changelog = _validate_changelog(data.get("changelog"), errors)
    if errors:
        raise ManifestError(errors)
    return Manifest(skill_id, name, version, description, copy.deepcopy(author), copy.deepcopy(provenance),
                    copy.deepcopy(compat), tuple(copy.deepcopy(dep) for dep in dependencies), entry, copy.deepcopy(tests),
                    tuple(specs), copy.deepcopy(hooks), copy.deepcopy(data), changelog)


def validate_package_dir(directory: Path, env: Environment | None = None, existing_intents=()) -> Manifest:
    """Legge `skill.json` e verifica che entry e file di test esistano DENTRO la cartella - senza importarli.
    Un collegamento simbolico che punta fuori dalla cartella e' rifiutato."""
    directory = Path(directory)
    manifest_path = directory / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise ManifestError([f"{MANIFEST_FILENAME}: mancante in {directory}"])
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ManifestError([f"{MANIFEST_FILENAME}: JSON non valido ({exc})"]) from exc
    manifest = parse_manifest(data, env, existing_intents)
    root = directory.resolve()
    errors = []
    for relative in [manifest.entry, *manifest.tests["files"]]:
        target = (directory / relative)
        try:
            resolved = target.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            errors.append(f"{relative}: esce dalla cartella del pacchetto")
            continue
        if not target.is_file():
            errors.append(f"{relative}: file mancante nel pacchetto")
    if errors:
        raise ManifestError(errors)
    return manifest


# ---- documentazione e permessi generati dal manifest (F8.1.7) ---------------------------------------------------------------

_RISK_TEXT = {
    RiskLevel.READ_ONLY: "sola lettura", RiskLevel.LOCAL_REVERSIBLE: "modifica locale reversibile",
    RiskLevel.EXTERNAL_ACTION: "azione verso l'esterno", RiskLevel.DESTRUCTIVE: "distruttiva", RiskLevel.ADMIN: "amministrativa",
}
_CAPABILITY_TEXT = {
    "filesystem.read": "legge file", "filesystem.write": "scrive o cancella file", "network": "accede alla rete",
    "web": "apre o legge pagine web", "apps": "avvia applicazioni", "contacts": "usa la rubrica / invia messaggi",
    "smart_home": "controlla dispositivi della casa", "clipboard.read": "legge gli appunti", "clipboard.write": "scrive negli appunti", "screen": "guarda lo schermo",
    "input": "simula tastiera e mouse", "microphone": "usa il microfono", "process": "gestisce processi",
    "system": "cambia impostazioni di sistema", "notifications": "invia notifiche", "memory.read": "legge la memoria di Jake",
    "memory.write": "scrive nella memoria di Jake", "subprocess": "avvia programmi esterni",
}


def permission_summary(manifest: Manifest) -> str:
    """Riepilogo dei permessi in italiano, generato SOLO dai campi del manifest (nessun testo scritto a mano
    dall'autore finisce qui, a parte il nome): e' cio' che l'utente vede prima di approvare l'installazione."""
    lines = [(f"{manifest.name} {manifest.version} ({manifest.id}) - autore: {manifest.author['name']}, "
              f"origine: {manifest.provenance['kind']} ({manifest.provenance['source']})")]
    caps = sorted(manifest.capabilities())
    lines.append("Permessi richiesti: " + (", ".join(_CAPABILITY_TEXT[cap] for cap in caps) if caps else "nessuno"))
    lines.append(f"Rischio massimo: {_RISK_TEXT[manifest.highest_risk()]}")
    for spec in manifest.intents:
        undo = "annullabile" if spec.undo["supported"] else "NON annullabile"
        lines.append(f"- {spec.intent}: {_RISK_TEXT[spec.risk]}, effetto '{spec.effect_class}', {undo}, verifica: {spec.verifier['kind']}")
    if manifest.dependencies:
        lines.append("Dipendenze: " + ", ".join(f"{dep['name']}{dep['spec']}" for dep in manifest.dependencies))
    if manifest.hooks:
        lines.append("Hook: " + ", ".join(sorted(manifest.hooks)) + " (solo sui dati della skill)")
    return "\n".join(lines)


def render_docs(manifest: Manifest) -> str:
    """Pagina Markdown della skill, deterministica (stesso manifest = stessi byte)."""
    out = [f"# {manifest.name} `{manifest.version}`", "", manifest.description, "",
           f"- id: `{manifest.id}`", f"- autore: {manifest.author['name']}",
           f"- origine: {manifest.provenance['kind']} - {manifest.provenance['source']} ({manifest.provenance['created_at']})",
           "- compatibilita': " + ", ".join(f"{key} `{value}`" for key, value in sorted(manifest.compatibility.items())), "",
           "## Permessi", "", permission_summary(manifest), "", "## Intent", ""]
    for spec in manifest.intents:
        out += [f"### {spec.intent}", "", spec.description, "", f"- rischio: `{spec.risk.value}`, effetto: `{spec.effect_class}`",
                f"- capability: {', '.join(f'`{cap}`' for cap in spec.capabilities) or 'nessuna'}",
                "- input: `" + json.dumps(spec.input_schema, sort_keys=True, ensure_ascii=False) + "`",
                "- output: `" + json.dumps(spec.output_schema, sort_keys=True, ensure_ascii=False) + "`"]
        if spec.errors:
            out.append("- errori: " + ", ".join(f"`{code}`" for code in spec.errors))
        out.append("")
    return "\n".join(out).rstrip() + "\n"
