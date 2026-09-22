"""F8.1: manifest delle skill e loader che rifiuta PRIMA di importare. Nessun mock del contratto: pacchetti veri su
disco, un modulo che scrive un file quando viene importato (per provare che il rifiuto avviene prima dell'import) e
una skill vera i cui output vengono controllati contro lo schema dichiarato."""
import copy
import json
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from core.plugin_loader import load_skill_package
from core.skill_manifest import (
    KNOWN_CAPABILITIES, MAX_HOOK_TIMEOUT_SECONDS, Environment, ManifestError, check_schema, parse_manifest,
    permission_summary, render_docs, satisfies, spec_is_valid, validate_instance, validate_manifest, validate_package_dir,
)
from core.risk import RiskLevel

ENV = Environment(jake_version="5.9", python_version="3.12.6", windows_version="10.0")


def intent(**overrides) -> dict:
    base = {
        "intent": "TOSS_DEMO_COIN",
        "description": "Lancia una moneta.",
        "risk": "read_only",
        "effect_class": "read",
        "capabilities": [],
        "verifier": {"kind": "none", "reason": "funzione pura senza effetti"},
        "undo": {"supported": False, "reason": "nessun effetto da annullare"},
        "input_schema": {"type": "object", "properties": {"times": {"type": "integer", "minimum": 1, "maximum": 5}}, "additionalProperties": False},
        "output_schema": {"type": "object", "properties": {"result": {"type": "string", "enum": ["testa", "croce"]}}, "required": ["result"]},
        "errors": [{"code": "invalid_times", "description": "times fuori dall'intervallo"}],
        "fixtures": [
            {"name": "un lancio", "input": {"times": 1}, "output": {"result": "testa"}},
            {"name": "troppi lanci", "input": {"times": 99}, "error_code": "invalid_times"},
        ],
    }
    base.update(overrides)
    return base


def manifest(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "id": "davide.moneta",
        "name": "Moneta",
        "version": "1.0.0",
        "description": "Lancia una moneta virtuale.",
        "author": {"name": "Davide"},
        "provenance": {"kind": "user", "source": "scritta a mano", "created_at": "2026-09-22"},
        "compatibility": {"jake": ">=5.9,<6", "python": ">=3.11", "windows": ">=10"},
        "dependencies": [],
        "entry": "skill.py",
        "tests": {"files": ["test_skill.py"]},
        "intents": [intent()],
    }
    base.update(overrides)
    return base


def errors_of(data, **kwargs) -> list[str]:
    return validate_manifest(data, kwargs.pop("env", ENV), **kwargs)


class ValidManifestTests(unittest.TestCase):
    def test_a_complete_manifest_parses_into_a_typed_object(self):
        parsed = parse_manifest(manifest(), ENV)
        self.assertEqual((parsed.id, parsed.version, parsed.entry), ("davide.moneta", "1.0.0", "skill.py"))
        self.assertEqual(parsed.intent_names(), frozenset({"TOSS_DEMO_COIN"}))
        self.assertEqual(parsed.intents[0].risk, RiskLevel.READ_ONLY)
        self.assertEqual(parsed.highest_risk(), RiskLevel.READ_ONLY)
        self.assertEqual(errors_of(manifest()), [])

    def test_the_parsed_object_is_a_copy_not_a_view_of_the_input(self):
        data = manifest()
        parsed = parse_manifest(data, ENV)
        data["intents"][0]["description"] = "modificata dopo"
        data["author"]["name"] = "Altro"
        self.assertEqual(parsed.intents[0].description, "Lancia una moneta.")
        self.assertEqual(parsed.author["name"], "Davide")

    def test_every_required_top_level_field_is_reported_when_missing(self):
        for key in ("schema_version", "id", "name", "version", "description", "author", "provenance", "compatibility",
                    "entry", "tests", "intents"):
            data = manifest()
            del data[key]
            problems = errors_of(data)
            self.assertTrue(any(key in problem for problem in problems), f"{key}: {problems}")

    def test_all_problems_are_collected_in_one_pass(self):
        data = manifest(id="Maiuscolo", version="1", entry="../fuori.py", tests={"files": []}, intents=[])
        problems = errors_of(data)
        self.assertGreaterEqual(len(problems), 5)

    def test_not_an_object_and_unknown_keys(self):
        self.assertEqual(errors_of([1, 2]), ["manifest: deve essere un oggetto JSON"])
        self.assertTrue(any("chiave sconosciuta 'permissoins'" in p for p in errors_of(manifest(permissoins=["x"]))))
        bad_intent = intent(risky="si")
        self.assertTrue(any("chiave sconosciuta 'risky'" in p for p in errors_of(manifest(intents=[bad_intent]))))

    def test_identifier_and_version_formats(self):
        for bad_id in ("moneta", "Davide.moneta", "davide.", ".x", "davide moneta", "a.b.c.d.e", "davide..x"):
            self.assertTrue(errors_of(manifest(id=bad_id)), bad_id)
        for bad_version in ("1", "1.0", "v1.0.0", "1.0.0-beta", "1.0.x", ""):
            self.assertTrue(errors_of(manifest(version=bad_version)), bad_version)
        for bad_intent in ("toss", "TO", "TOSS COIN", "1TOSS", "T" * 70):
            self.assertTrue(errors_of(manifest(intents=[intent(intent=bad_intent)])), bad_intent)

    def test_an_intent_that_already_exists_is_rejected(self):
        problems = errors_of(manifest(intents=[intent(intent="OPEN_APP")]))
        self.assertTrue(any("esiste gia'" in p for p in problems))
        problems = errors_of(manifest(), existing_intents={"TOSS_DEMO_COIN"})
        self.assertTrue(any("esiste gia'" in p for p in problems))

    def test_duplicate_intents_in_the_same_manifest(self):
        problems = errors_of(manifest(intents=[intent(), intent()]))
        self.assertTrue(any("dichiarato due volte" in p for p in problems))

    def test_provenance_rules(self):
        self.assertTrue(errors_of(manifest(provenance={"kind": "amico", "source": "x", "created_at": "2026-09-22"})))
        self.assertTrue(errors_of(manifest(provenance={"kind": "user", "source": "x", "created_at": "ieri"})))
        self.assertTrue(errors_of(manifest(provenance={"kind": "user", "source": "x", "created_at": "2026-09-22", "sha256": "abc"})))
        self.assertEqual(errors_of(manifest(provenance={"kind": "registry", "source": "catalogo", "created_at": "2026-09-22T10:00:00Z", "sha256": "a" * 64})), [])


class CapabilityAndRiskTests(unittest.TestCase):
    def test_an_unknown_capability_is_rejected(self):
        problems = errors_of(manifest(intents=[intent(capabilities=["telepatia"])]))
        self.assertTrue(any("capability sconosciuta 'telepatia'" in p for p in problems))

    def test_every_known_capability_is_accepted_with_a_coherent_risk(self):
        for capability in sorted(KNOWN_CAPABILITIES):
            candidate = intent(risk="admin", effect_class="external", capabilities=[capability],
                               verifier={"kind": "declarative", "expect": "l'effetto e' visibile"},
                               undo={"supported": False, "reason": "non reversibile"})
            self.assertEqual(errors_of(manifest(intents=[candidate])), [], capability)

    def test_read_only_cannot_write_or_reach_out(self):
        for capability in ("filesystem.write", "network", "apps", "clipboard.write", "input"):
            problems = errors_of(manifest(intents=[intent(capabilities=[capability])]))
            self.assertTrue(any("read_only" in p for p in problems), capability)
        self.assertEqual(errors_of(manifest(intents=[intent(capabilities=["filesystem.read", "clipboard.read", "screen"])])), [])

    def test_read_only_requires_a_read_effect(self):
        problems = errors_of(manifest(intents=[intent(effect_class="modify")]))
        self.assertTrue(any("richiede effect_class=read" in p for p in problems))

    def test_outbound_and_admin_capabilities_need_a_high_enough_risk(self):
        problems = errors_of(manifest(intents=[intent(risk="local_reversible", effect_class="modify", capabilities=["network"],
                                                       verifier={"kind": "declarative", "expect": "x"})]))
        self.assertTrue(any("almeno external_action" in p for p in problems))
        problems = errors_of(manifest(intents=[intent(risk="external_action", effect_class="modify", capabilities=["system"],
                                                       verifier={"kind": "declarative", "expect": "x"})]))
        self.assertTrue(any("almeno destructive" in p for p in problems))

    def test_a_skill_that_changes_things_needs_a_verifier(self):
        problems = errors_of(manifest(intents=[intent(risk="local_reversible", effect_class="modify", capabilities=["filesystem.write"])]))
        self.assertTrue(any("deve avere un verifier" in p for p in problems))

    def test_verifier_shapes(self):
        for verifier in ({}, {"kind": "magico"}, {"kind": "none"}, {"kind": "function"}, {"kind": "function", "function": "no spazi"},
                         {"kind": "declarative"}, "sempre"):
            self.assertTrue(errors_of(manifest(intents=[intent(verifier=verifier)])), verifier)
        self.assertEqual(errors_of(manifest(intents=[intent(verifier={"kind": "function", "function": "verify_result"})])), [])

    def test_undo_shapes(self):
        for undo in ({}, {"supported": "si"}, {"supported": True}, {"supported": False}, {"supported": False, "reason": " "}):
            self.assertTrue(errors_of(manifest(intents=[intent(undo=undo)])), undo)
        self.assertEqual(errors_of(manifest(intents=[intent(undo={"supported": True, "function": "undo_toss"})])), [])

    def test_a_read_effect_without_mutating_capabilities_cannot_claim_a_higher_risk(self):
        problems = errors_of(manifest(intents=[intent(risk="local_reversible", verifier={"kind": "declarative", "expect": "x"})]))
        self.assertTrue(problems)


class SchemaTests(unittest.TestCase):
    def test_unsupported_keywords_are_errors_not_silently_ignored(self):
        for schema in ({"type": "string", "$ref": "#/x"}, {"type": "object", "oneOf": []}, {"type": "string", "pattern": "^a"},
                       {"type": "string", "format": "email"}):
            self.assertTrue(check_schema(schema), schema)

    def test_malformed_schemas(self):
        for schema in ({}, {"type": "stringa"}, {"type": "object", "properties": []}, {"type": "object", "required": "a"},
                       {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["b"]},
                       {"type": "string", "minLength": -1}, {"type": "integer", "minimum": "1"}, {"type": "string", "enum": []},
                       {"type": "array", "items": {"type": "nulla"}}, {"type": "string", "items": {"type": "string"}}, "string"):
            self.assertTrue(check_schema(schema), schema)

    def test_nesting_depth_is_limited(self):
        schema: dict = {"type": "string"}
        for _ in range(9):
            schema = {"type": "array", "items": schema}
        self.assertTrue(any("annidamento" in p for p in check_schema(schema)))

    def test_the_input_of_a_skill_must_be_an_object(self):
        problems = errors_of(manifest(intents=[intent(input_schema={"type": "string"}, fixtures=[{"name": "x", "input": {}, "output": {"result": "testa"}}])]))
        self.assertTrue(any("l'input di una skill e' un oggetto" in p for p in problems))

    def test_instance_validation(self):
        schema = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 5}, "s": {"type": "string", "minLength": 2, "maxLength": 4, "enum": ["ab", "abc", "abcde"]},
                                                   "l": {"type": "array", "items": {"type": "number"}, "minItems": 1, "maxItems": 2}},
                  "required": ["n"], "additionalProperties": False}
        self.assertEqual(validate_instance(schema, {"n": 3, "s": "abc", "l": [1, 2.5]}), [])
        cases = [({}, "$.n: obbligatorio"), ({"n": 0}, "sotto il minimo"), ({"n": 6}, "sopra il massimo"), ({"n": True}, "atteso integer"),
                 ({"n": 1.5}, "atteso integer"), ({"n": 1, "x": 1}, "non ammessa"), ({"n": 1, "s": "zz"}, "fuori da enum"),
                 ({"n": 1, "s": "abcde"}, "piu' lungo"), ({"n": 1, "l": []}, "meno di 1"), ({"n": 1, "l": [1, 2, 3]}, "piu' di 2"),
                 ({"n": 1, "l": ["a"]}, "atteso number"), ([], "atteso object")]
        for value, expected in cases:
            self.assertTrue(any(expected in p for p in validate_instance(schema, value)), (value, expected))


class FixtureTests(unittest.TestCase):
    def test_fixtures_are_mandatory_and_must_cover_success_and_declared_errors(self):
        self.assertTrue(any("fixtures: obbligatorie" in p for p in errors_of(manifest(intents=[intent(fixtures=[])]))))
        only_error = [{"name": "x", "input": {"times": 99}, "error_code": "invalid_times"}]
        self.assertTrue(any("manca una fixture di successo" in p for p in errors_of(manifest(intents=[intent(fixtures=only_error)]))))
        only_success = [{"name": "x", "input": {"times": 1}, "output": {"result": "testa"}}]
        self.assertTrue(any("nessuna fixture li esercita" in p for p in errors_of(manifest(intents=[intent(fixtures=only_success)]))))
        self.assertEqual(errors_of(manifest(intents=[intent(errors=[], fixtures=only_success)])), [])

    def test_fixtures_must_respect_the_declared_schemas_and_errors(self):
        bad_input = [{"name": "x", "input": {"times": "molti"}, "output": {"result": "testa"}}]
        self.assertTrue(any("atteso integer" in p for p in errors_of(manifest(intents=[intent(fixtures=bad_input)]))))
        bad_output = [{"name": "x", "input": {"times": 1}, "output": {"result": "dado"}}]
        self.assertTrue(any("fuori da enum" in p for p in errors_of(manifest(intents=[intent(fixtures=bad_output)]))))
        unknown_error = [{"name": "x", "input": {"times": 1}, "output": {"result": "testa"}}, {"name": "y", "input": {}, "error_code": "boom"}]
        self.assertTrue(any("non e' tra gli errori dichiarati" in p for p in errors_of(manifest(intents=[intent(fixtures=unknown_error)]))))
        both = [{"name": "x", "input": {}, "output": {"result": "testa"}, "error_code": "invalid_times"}]
        self.assertTrue(any("esattamente uno" in p for p in errors_of(manifest(intents=[intent(fixtures=both)]))))

    def test_error_declarations(self):
        for errors in ("x", [{"code": "Maiuscolo", "description": "d"}], [{"code": "ok_code"}], [{"code": "a_b", "description": "d"}, {"code": "a_b", "description": "e"}]):
            self.assertTrue(errors_of(manifest(intents=[intent(errors=errors)])), errors)


class CompatibilityTests(unittest.TestCase):
    def test_version_specs(self):
        table = [("5.9", ">=5.9,<6", True), ("5.10", ">=5.9,<6", True), ("6.0", ">=5.9,<6", False), ("5.8.9", ">=5.9", False),
                 ("3.12.6", ">=3.11", True), ("3.10.0", ">=3.11", False), ("10.0", ">=10", True), ("10.0", ">10", False),
                 ("1.2.3", "==1.2.3", True), ("1.2.3", "==1.2", False), ("2", "<=2.0.0", True)]
        for version, spec, expected in table:
            self.assertEqual(satisfies(version, spec), expected, (version, spec))
        for bad in ("", "5.9", ">=", ">=a.b", "~=1.0", ">=1.0;python", ">=1.0,,<2", None, 5):
            self.assertFalse(spec_is_valid(bad), bad)
            self.assertFalse(satisfies("5.9", bad) if isinstance(bad, str) else False)

    def test_incompatible_jake_python_or_windows_are_rejected(self):
        for key, spec in (("jake", ">=6"), ("python", ">=3.13"), ("windows", ">=11")):
            compat = {"jake": ">=5.9,<6", "python": ">=3.11", "windows": ">=10", key: spec}
            problems = errors_of(manifest(compatibility=compat))
            self.assertTrue(any("incompatibile" in p and key in p for p in problems), (key, problems))

    def test_a_non_windows_environment_is_incompatible(self):
        problems = errors_of(manifest(), env=Environment("5.9", "3.12.6", None))
        self.assertTrue(any("non e' Windows" in p for p in problems))

    def test_compatibility_must_declare_all_three_with_valid_specs(self):
        for compat in ({"jake": ">=5.9"}, {"jake": "5.9", "python": ">=3.11", "windows": ">=10"}, {"jake": ">=5.9", "python": ">=3.11", "windows": ">=10", "gpu": ">=1"}, "tutto"):
            self.assertTrue(errors_of(manifest(compatibility=compat)), compat)

    def test_dependencies_are_version_ranges_never_urls(self):
        good = [{"name": "requests", "spec": ">=2.31,<3"}]
        self.assertEqual(errors_of(manifest(dependencies=good)), [])
        for dep in ({"name": "git+https://evil.example/x.git", "spec": ">=1"}, {"name": "x", "spec": "@ file:///c:/x"},
                    {"name": "..\\x", "spec": ">=1"}, {"name": "x"}, {"name": "x", "spec": ">=1", "extra": 1}, "requests>=2"):
            self.assertTrue(errors_of(manifest(dependencies=[dep])), dep)
        self.assertTrue(any("due volte" in p for p in errors_of(manifest(dependencies=[good[0], {"name": "Requests", "spec": ">=1"}]))))


class EntryTestsAndHooksTests(unittest.TestCase):
    def test_entry_and_test_paths_cannot_escape_the_package(self):
        for entry in ("../evil.py", "/etc/evil.py", "C:\\evil.py", "\\\\srv\\x.py", "skill.txt", "a/../../b.py", ""):
            self.assertTrue(errors_of(manifest(entry=entry)), entry)
        for files in (["../t.py"], ["/abs/t.py"], [], ["t.txt"], "t.py"):
            self.assertTrue(errors_of(manifest(tests={"files": files})), files)
        self.assertEqual(errors_of(manifest(entry="pkg/skill.py", tests={"files": ["tests/test_a.py", "tests/test_b.py"]})), [])

    def test_hooks_are_limited(self):
        ok = {"migrate": {"function": "migrate", "timeout_s": 5, "touches": "own_data"}, "uninstall": {"function": "cleanup", "timeout_s": MAX_HOOK_TIMEOUT_SECONDS, "touches": "own_data"}}
        self.assertEqual(errors_of(manifest(hooks=ok)), [])
        bad_cases = [
            {"install": {"function": "f", "timeout_s": 5, "touches": "own_data"}},
            {"migrate": {"function": "f", "timeout_s": MAX_HOOK_TIMEOUT_SECONDS + 1, "touches": "own_data"}},
            {"migrate": {"function": "f", "timeout_s": 0, "touches": "own_data"}},
            {"migrate": {"function": "f", "timeout_s": 5, "touches": "system"}},
            {"migrate": {"function": "f", "timeout_s": 5}},
            {"migrate": {"function": "os.system", "timeout_s": 5, "touches": "own_data"}},
            {"migrate": {"function": "f", "timeout_s": 5, "touches": "own_data", "shell": "rm -rf /"}},
            {"migrate": "f"}, "migrate",
        ]
        for hooks in bad_cases:
            self.assertTrue(errors_of(manifest(hooks=hooks)), hooks)


class PackageDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jake_skill_manifest_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write_package(self, data=None, name="pkg", entry_source="def register(registry):\n    pass\n", with_tests=True) -> Path:
        directory = self.tmp / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "skill.json").write_text(json.dumps(data if data is not None else manifest()), encoding="utf-8")
        (directory / "skill.py").write_text(entry_source, encoding="utf-8")
        if with_tests:
            (directory / "test_skill.py").write_text("# test\n", encoding="utf-8")
        return directory

    def test_a_complete_package_validates(self):
        self.assertEqual(validate_package_dir(self.write_package(), ENV).id, "davide.moneta")

    def test_missing_or_broken_manifest_file(self):
        empty = self.tmp / "vuota"
        empty.mkdir()
        with self.assertRaises(ManifestError) as ctx:
            validate_package_dir(empty, ENV)
        self.assertIn("mancante", ctx.exception.errors[0])
        broken = self.write_package(name="rotto")
        (broken / "skill.json").write_text("{non json", encoding="utf-8")
        with self.assertRaises(ManifestError) as ctx:
            validate_package_dir(broken, ENV)
        self.assertIn("JSON non valido", ctx.exception.errors[0])

    def test_missing_entry_or_test_files_are_reported(self):
        directory = self.write_package(with_tests=False)
        with self.assertRaises(ManifestError) as ctx:
            validate_package_dir(directory, ENV)
        self.assertTrue(any("test_skill.py: file mancante" in e for e in ctx.exception.errors))
        (directory / "skill.py").unlink()
        with self.assertRaises(ManifestError) as ctx:
            validate_package_dir(directory, ENV)
        self.assertTrue(any("skill.py: file mancante" in e for e in ctx.exception.errors))


class GeneratedDocumentationTests(unittest.TestCase):
    def dangerous(self):
        candidate = intent(intent="ERASE_DEMO_FILES", risk="destructive", effect_class="delete", capabilities=["filesystem.write"],
                           verifier={"kind": "function", "function": "verify_deleted"}, undo={"supported": False, "reason": "cestino non usato"})
        return parse_manifest(manifest(intents=[intent(), candidate], dependencies=[{"name": "send2trash", "spec": ">=1.8"}],
                                       hooks={"uninstall": {"function": "cleanup", "timeout_s": 5, "touches": "own_data"}}), ENV)

    def test_the_permission_summary_says_what_the_skill_can_do_and_that_it_cannot_be_undone(self):
        text = permission_summary(self.dangerous())
        self.assertIn("scrive o cancella file", text)
        self.assertIn("Rischio massimo: distruttiva", text)
        self.assertIn("ERASE_DEMO_FILES: distruttiva", text)
        self.assertIn("NON annullabile", text)
        self.assertIn("send2trash>=1.8", text)
        self.assertIn("uninstall (solo sui dati della skill)", text)

    def test_a_harmless_skill_says_it_needs_no_permissions(self):
        text = permission_summary(parse_manifest(manifest(), ENV))
        self.assertIn("Permessi richiesti: nessuno", text)
        self.assertIn("Rischio massimo: sola lettura", text)

    def test_docs_are_deterministic_and_derived_from_the_manifest(self):
        first = render_docs(self.dangerous())
        second = render_docs(parse_manifest(copy.deepcopy(self.dangerous().raw), ENV))
        self.assertEqual(first, second)
        self.assertIn("### TOSS_DEMO_COIN", first)
        self.assertIn("`invalid_times`", first)
        self.assertTrue(first.endswith("\n"))


# ---- loader --------------------------------------------------------------------------------------------------------------------

SKILL_SOURCE = textwrap.dedent('''
    import random
    from pathlib import Path

    Path(__file__).with_name("IMPORTED.marker").write_text("importato", encoding="utf-8")


    class TossSkill:
        metadata = {"intent": "TOSS_DEMO_COIN", "description": "moneta", "parameters": {}}

        def execute(self, parameters=None):
            times = (parameters or {}).get("times", 1)
            if not 1 <= times <= 5:
                return {"error": "invalid_times"}
            return {"result": random.choice(["testa", "croce"])}


    def register(registry):
        registry.register_skill("TOSS_DEMO_COIN", TossSkill())
''')


class FakeRegistry:
    def __init__(self, skills=None):
        self.skills = dict(skills or {})
        self.paths = {}

    def register_skill(self, intent, skill, plugin_path=None):
        self.skills[intent] = skill
        self.paths[intent] = plugin_path


class LoaderTests(PackageDirectoryTests):
    def marker(self, directory: Path) -> Path:
        return directory / "IMPORTED.marker"

    def test_a_valid_package_is_imported_and_registered_and_its_output_matches_the_declared_schema(self):
        directory = self.write_package(entry_source=SKILL_SOURCE)
        registry = FakeRegistry()
        result = load_skill_package(registry, directory, ENV)
        self.assertTrue(result.ok, result.errors)
        self.assertTrue(result.imported)
        self.assertEqual(set(registry.skills), {"TOSS_DEMO_COIN"})
        self.assertTrue(registry.paths["TOSS_DEMO_COIN"].endswith("skill.py"))
        spec = result.manifest.intents[0]
        skill = registry.skills["TOSS_DEMO_COIN"]
        for fixture in spec.fixtures:
            output = skill.execute(fixture["input"])
            if "error_code" in fixture:
                self.assertEqual(output, {"error": fixture["error_code"]})
            else:
                self.assertEqual(validate_instance(spec.output_schema, output), [])

    def rejected_before_import(self, data, env=ENV, registry=None):
        directory = self.write_package(data, entry_source=SKILL_SOURCE, name="rifiutato")
        registry = registry if registry is not None else FakeRegistry()
        before = dict(registry.skills)
        result = load_skill_package(registry, directory, env)
        self.assertFalse(result.ok)
        self.assertFalse(result.imported, "il codice del pacchetto NON deve essere stato importato")
        self.assertFalse(self.marker(directory).exists())
        self.assertEqual(registry.skills, before)
        return result

    def test_an_incomplete_manifest_is_rejected_before_the_import(self):
        data = manifest()
        del data["provenance"]
        result = self.rejected_before_import(data)
        self.assertTrue(any("provenance" in e for e in result.errors))

    def test_an_unknown_capability_is_rejected_before_the_import(self):
        result = self.rejected_before_import(manifest(intents=[intent(capabilities=["telepatia"])]))
        self.assertTrue(any("telepatia" in e for e in result.errors))

    def test_an_incompatible_skill_is_rejected_before_the_import(self):
        result = self.rejected_before_import(manifest(compatibility={"jake": ">=6", "python": ">=3.11", "windows": ">=10"}))
        self.assertTrue(any("incompatibile" in e for e in result.errors))

    def test_a_collision_with_an_existing_intent_is_rejected_before_the_import(self):
        registry = FakeRegistry({"TOSS_DEMO_COIN": object()})
        result = self.rejected_before_import(manifest(), registry=registry)
        self.assertTrue(any("esiste gia'" in e for e in result.errors))

    def test_registering_an_undeclared_intent_leaves_nothing_behind(self):
        source = SKILL_SOURCE.replace('def register(registry):\n    registry.register_skill("TOSS_DEMO_COIN", TossSkill())',
                                      'def register(registry):\n    registry.register_skill("TOSS_DEMO_COIN", TossSkill())\n    registry.register_skill("SYSTEM_POWER", TossSkill())')
        directory = self.write_package(entry_source=source)
        registry = FakeRegistry()
        result = load_skill_package(registry, directory, ENV)
        self.assertFalse(result.ok)
        self.assertTrue(any("non dichiara nel manifest" in e for e in result.errors))
        self.assertEqual(registry.skills, {})

    def test_declaring_two_intents_and_registering_one_leaves_nothing_behind(self):
        second = intent(intent="TOSS_DEMO_TWICE")
        directory = self.write_package(manifest(intents=[intent(), second]), entry_source=SKILL_SOURCE)
        registry = FakeRegistry()
        result = load_skill_package(registry, directory, ENV)
        self.assertFalse(result.ok)
        self.assertTrue(any("dichiarati ma non registrati" in e for e in result.errors))
        self.assertEqual(registry.skills, {})

    def test_a_package_that_raises_or_has_no_register_is_reported_without_registering(self):
        directory = self.write_package(entry_source="raise RuntimeError('boom')\n", name="esplode")
        registry = FakeRegistry()
        result = load_skill_package(registry, directory, ENV)
        self.assertEqual((result.ok, result.imported, registry.skills), (False, True, {}))
        self.assertEqual(result.errors, ["errore nel pacchetto: RuntimeError"])
        no_register = self.write_package(entry_source="x = 1\n", name="senza")
        result = load_skill_package(registry, no_register, ENV)
        self.assertFalse(result.ok)
        self.assertTrue(any("register(registry)" in e for e in result.errors))


if __name__ == "__main__":
    unittest.main()
