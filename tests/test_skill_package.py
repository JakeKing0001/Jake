"""F8.2: pacchetti firmati, catalogo, quarantena, pin e rollback. Crittografia VERA (`cryptography`), archivi ZIP veri
(anche costruiti a mano per essere malevoli), cartelle vere su disco e il loader vero: il criterio e' che un pacchetto
alterato o con firma sconosciuta viene rifiutato PRIMA dell'import - lo si prova con un modulo che scrive un file
quando viene importato."""
import ast
import json
import shutil
import stat
import tempfile
import unittest
import warnings
import zipfile
from io import BytesIO
from pathlib import Path

from core import skill_package
from core.skill_manifest import Environment, validate_instance
from core.skill_package import (
    KEEP_VERSIONS, MAX_FILE_BYTES, MAX_FILES, PackageError, PublisherKey, SkillStore, TrustStore, approve,
    build_package, installed_python_version, key_id_of, pack_files, read_package, sha256_hex, verify_package,
)
from tests.test_skill_manifest import SKILL_SOURCE, intent, manifest

ENV = Environment(jake_version="5.9", python_version="3.12.6", windows_version="10.0")
MARKER_SOURCE = SKILL_SOURCE


class FakeRegistry:
    def __init__(self):
        self.skills = {}

    def register_skill(self, intent_name, skill, plugin_path=None):
        self.skills[intent_name] = skill


class PackageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jake_skill_package_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.key = PublisherKey.generate()
        self.trust = TrustStore(self.tmp / "trust.json")
        self.trust.add("Davide", self.key.public_b64(), "davide.")
        self.store = SkillStore(self.tmp / "skills", self.trust, ENV, installed_versions=lambda name: None)

    def source(self, name="src", version="1.0.0", skill_id="davide.moneta", source=MARKER_SOURCE, extra=None, **overrides) -> Path:
        directory = self.tmp / name
        directory.mkdir(parents=True, exist_ok=True)
        data = manifest(id=skill_id, version=version, **overrides)
        (directory / "skill.json").write_text(json.dumps(data), encoding="utf-8")
        (directory / "skill.py").write_text(source, encoding="utf-8")
        (directory / "test_skill.py").write_text("# test\n", encoding="utf-8")
        for relative, content in (extra or {}).items():
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return directory

    def signed(self, key=None, **kwargs) -> tuple[bytes, dict]:
        package = build_package(self.source(**kwargs))
        return package, (key or self.key).sign_package(package)

    def install(self, package, signature, store=None, **approval):
        store = store or self.store
        plan = store.plan_install(package, signature)
        return store.install(plan, approve(plan, "davide", **approval))

    def publish(self, version, store=None, **kwargs):
        package, signature = self.signed(name=f"src_{version}", version=version, **kwargs)
        return self.install(package, signature, store)


# ---- F8.2.1 ---------------------------------------------------------------------------------------------------------------------


class DeterministicFormatTests(PackageTestCase):
    def test_the_same_files_give_the_same_bytes_regardless_of_mtime_location_or_creation_order(self):
        first = build_package(self.source())
        directory = self.source(name="copia")
        for path in directory.rglob("*"):
            path.touch()
        second = build_package(directory)
        reordered = self.tmp / "ordine"
        reordered.mkdir()
        for name in ("test_skill.py", "skill.py", "skill.json"):
            shutil.copy(self.tmp / "src" / name, reordered / name)
        self.assertEqual(first, second)
        self.assertEqual(first, build_package(reordered))
        self.assertEqual(sha256_hex(first), sha256_hex(second))

    def test_the_archive_is_stored_sorted_with_fixed_dates_and_no_extras(self):
        package = build_package(self.source())
        with zipfile.ZipFile(BytesIO(package)) as archive:
            infos = archive.infolist()
        self.assertEqual([info.filename for info in infos], sorted(info.filename for info in infos))
        for info in infos:
            self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
            self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
            self.assertEqual(info.extra, b"")
            self.assertTrue(stat.S_ISREG(info.external_attr >> 16))

    def test_bytecode_and_vcs_folders_never_enter_the_package_and_content_changes_the_digest(self):
        directory = self.source()
        baseline = build_package(directory)
        (directory / "__pycache__").mkdir()
        (directory / "__pycache__" / "skill.cpython-312.pyc").write_bytes(b"\x00\x01")
        (directory / ".git").mkdir()
        (directory / ".git" / "config").write_text("x", encoding="utf-8")
        self.assertEqual(build_package(directory), baseline)
        (directory / "skill.py").write_text(MARKER_SOURCE + "# uno\n", encoding="utf-8")
        self.assertNotEqual(sha256_hex(build_package(directory)), sha256_hex(baseline))

    def test_a_symlink_is_refused_at_build_time(self):
        directory = self.source()
        try:
            (directory / "link.py").symlink_to(directory / "skill.py")
        except (OSError, NotImplementedError):
            self.skipTest("collegamenti simbolici non consentiti su questa macchina")
        with self.assertRaises(PackageError) as ctx:
            build_package(directory)
        self.assertEqual(ctx.exception.code, "unsafe_package")

    def test_building_without_a_manifest_is_refused(self):
        empty = self.tmp / "vuoto"
        empty.mkdir()
        with self.assertRaises(PackageError) as ctx:
            build_package(empty)
        self.assertEqual(ctx.exception.code, "manifest_missing")


def raw_zip(entries: list[tuple[str, bytes, dict]]) -> bytes:
    """Archivio costruito a mano, anche malevolo. Ogni voce: (nome, contenuto, opzioni)."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content, options in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = options.get("compress", zipfile.ZIP_STORED)
            info.external_attr = options.get("attr", (stat.S_IFREG | 0o644) << 16)
            archive.writestr(info, content)
    return buffer.getvalue()


class HostileArchiveTests(unittest.TestCase):
    def rejected(self, data: bytes, code: str = "unsafe_package"):
        with self.assertRaises(PackageError) as ctx:
            read_package(data)
        self.assertEqual(ctx.exception.code, code)

    def test_dangerous_member_names(self):
        for name in ("../evil.py", "a/../../evil.py", "/etc/evil.py", "C:/evil.py", "..\\evil.py", "a//b.py", "con.py", "nul", "x./y.py",
                     "a:b.py", " lead.py", "dir/", "./x.py"):
            self.rejected(raw_zip([(name, b"x", {})]))

    def test_duplicates_including_case_variants(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # zipfile avvisa di proposito di un nome duplicato: e' proprio il caso da provare
            self.rejected(raw_zip([("a.py", b"1", {}), ("a.py", b"2", {})]))
        self.rejected(raw_zip([("A.py", b"1", {}), ("a.py", b"2", {})]))

    def test_compressed_symlink_and_non_regular_entries(self):
        self.rejected(raw_zip([("a.py", b"x" * 1000, {"compress": zipfile.ZIP_DEFLATED})]))
        self.rejected(raw_zip([("link.py", b"target.py", {"attr": (stat.S_IFLNK | 0o777) << 16})]))
        self.rejected(raw_zip([("dev.py", b"", {"attr": (stat.S_IFCHR | 0o644) << 16})]))

    def test_size_and_count_limits(self):
        self.rejected(raw_zip([("big.bin", b"\x00" * (MAX_FILE_BYTES + 1), {})]), "package_too_large")
        self.rejected(raw_zip([(f"f{index}.py", b"x", {}) for index in range(MAX_FILES + 1)]))
        with self.assertRaises(PackageError):
            pack_files({f"f{index}.py": b"x" for index in range(MAX_FILES + 1)})

    def test_garbage_and_empty_archives(self):
        for data in (b"", b"not a zip", b"PK\x03\x04" + b"\x00" * 30, raw_zip([])):
            self.rejected(data)

    def test_a_clean_archive_is_read_back_exactly(self):
        files = {"skill.json": b"{}", "dir/skill.py": b"print(1)"}
        self.assertEqual(read_package(pack_files(files)), files)


# ---- F8.2.2 ---------------------------------------------------------------------------------------------------------------------


class SignatureTests(PackageTestCase):
    def rejected(self, package, signature, code, trust=None):
        with self.assertRaises(PackageError) as ctx:
            verify_package(package, signature, trust or self.trust, ENV)
        self.assertEqual(ctx.exception.code, code, ctx.exception)

    def test_a_signed_package_from_a_trusted_key_verifies(self):
        package, signature = self.signed()
        verified = verify_package(package, signature, self.trust, ENV)
        self.assertEqual(verified.digest, sha256_hex(package))
        self.assertEqual(verified.key_id, self.key.key_id)
        self.assertEqual((verified.manifest.id, verified.manifest.version), ("davide.moneta", "1.0.0"))

    def test_an_unknown_signer_is_refused(self):
        package, _ = self.signed()
        stranger = PublisherKey.generate()
        self.rejected(package, stranger.sign_package(package), "unknown_signer")

    def test_a_revoked_signer_is_refused_and_cannot_be_re_added(self):
        package, signature = self.signed()
        self.trust.revoke(self.key.key_id, "chiave rubata")
        self.rejected(package, signature, "signer_revoked")
        with self.assertRaises(PackageError) as ctx:
            self.trust.add("Davide", self.key.public_b64(), "davide.")
        self.assertEqual(ctx.exception.code, "signer_revoked")

    def test_any_altered_byte_is_refused_before_anything_is_read(self):
        package, signature = self.signed()
        for position in (0, len(package) // 2, len(package) - 1):
            altered = bytearray(package)
            altered[position] ^= 0x01
            self.rejected(bytes(altered), signature, "package_altered")

    def test_a_rebuilt_package_with_different_content_does_not_inherit_the_signature(self):
        package, signature = self.signed()
        files = read_package(package)
        files["skill.py"] = files["skill.py"] + b"\nimport os\n"
        self.rejected(pack_files(files), signature, "package_altered")

    def test_a_signature_made_with_another_key_under_a_trusted_key_id_is_invalid(self):
        package, signature = self.signed()
        forger = PublisherKey.generate()
        forged = forger.sign_package(package)
        forged["key_id"] = self.key.key_id
        self.rejected(package, forged, "bad_signature")

    def test_the_signature_binds_id_and_version(self):
        package, signature = self.signed()
        for field_name, value in (("version", "9.9.9"), ("id", "davide.altro")):
            relabeled = {**signature, field_name: value}
            self.rejected(package, relabeled, "bad_signature")

    def test_a_trusted_key_signing_a_manifest_that_disagrees_is_refused(self):
        package, _ = self.signed()
        wrong = self.key._private.sign(skill_package._signed_payload(self.key.key_id, sha256_hex(package), "davide.moneta", "2.0.0"))
        signature = {"v": 1, "key_id": self.key.key_id, "package_sha256": sha256_hex(package), "id": "davide.moneta",
                     "version": "2.0.0", "signature": skill_package._b64(wrong)}
        self.rejected(package, signature, "manifest_mismatch")

    def test_a_key_can_only_sign_ids_under_its_prefix(self):
        package, signature = self.signed(skill_id="mario.strumento")
        self.rejected(package, signature, "id_not_allowed")

    def test_malformed_signatures(self):
        package, signature = self.signed()
        for bad in (None, {}, [], "firma", {**signature, "v": 2}, {**signature, "key_id": 5}, {**signature, "signature": "%%%"},
                    {**signature, "signature": 7}, {k: v for k, v in signature.items() if k != "package_sha256"}):
            with self.assertRaises(PackageError):
                verify_package(package, bad, self.trust, ENV)
        flipped = dict(signature)
        raw = bytearray(skill_package._unb64(signature["signature"]))
        raw[0] ^= 0x01
        flipped["signature"] = skill_package._b64(bytes(raw))
        self.rejected(package, flipped, "bad_signature")

    def test_a_validly_signed_package_with_an_invalid_manifest_is_still_refused(self):
        package, signature = self.signed(intents=[intent(capabilities=["telepatia"])])
        self.rejected(package, signature, "manifest_invalid")
        package, signature = self.signed(compatibility={"jake": ">=6", "python": ">=3.11", "windows": ">=10"})
        self.rejected(package, signature, "manifest_invalid")

    def test_files_declared_by_the_manifest_must_be_in_the_package(self):
        directory = self.source()
        (directory / "test_skill.py").unlink()
        package = build_package(directory)
        self.rejected(package, self.key.sign_package(package), "manifest_invalid")

    def test_the_trust_store_persists_and_validates_keys(self):
        reloaded = TrustStore(self.tmp / "trust.json")
        self.assertEqual([key.key_id for key in reloaded.keys()], [self.key.key_id])
        self.assertEqual(reloaded.get(self.key.key_id).id_prefix, "davide.")
        for public, prefix in (("AAAA", "davide."), (self.key.public_b64(), "Davide"), (self.key.public_b64(), "davide"), ("%%%", "d.")):
            with self.assertRaises(PackageError):
                reloaded.add("x", public, prefix)
        self.assertEqual(key_id_of(skill_package._unb64(self.key.public_b64())), self.key.key_id)


# ---- F8.2.3 / F8.2.4 / F8.2.5 -----------------------------------------------------------------------------------------------


class InstallTests(PackageTestCase):
    def test_install_requires_an_explicit_approval_bound_to_the_package(self):
        package, signature = self.signed()
        plan = self.store.plan_install(package, signature)
        for approval, code in ((None, "approval_required"), (approve(plan, "  "), "approval_required")):
            with self.assertRaises(PackageError) as ctx:
                self.store.install(plan, approval)
            self.assertEqual(ctx.exception.code, code)
        other_package, other_signature = self.signed(name="altro", skill_id="davide.altra", intents=[intent(intent="TOSS_DEMO_OTHER")])
        other_plan = self.store.plan_install(other_package, other_signature)
        with self.assertRaises(PackageError) as ctx:
            self.store.install(plan, approve(other_plan, "davide"))
        self.assertEqual(ctx.exception.code, "approval_mismatch")
        self.assertEqual(self.store.skills(), [])
        self.assertFalse((self.tmp / "skills" / "store").exists())

    def test_a_planned_install_shows_permissions_and_a_real_install_lands_on_disk_and_in_the_catalog(self):
        package, signature = self.signed(changelog=[{"version": "1.0.0", "notes": "prima versione"}],
                                         intents=[intent(risk="external_action", effect_class="read", capabilities=["network"],
                                                         verifier={"kind": "declarative", "expect": "risposta ottenuta"})])
        plan = self.store.plan_install(package, signature)
        self.assertIn("accede alla rete", plan.summary)
        self.assertIn(self.key.key_id, plan.summary)
        result = self.store.install(plan, approve(plan, "davide"))
        self.assertEqual(result, {"id": "davide.moneta", "version": "1.0.0", "activated": True, "reason": ""})
        self.assertTrue((self.tmp / "skills" / "store" / "davide.moneta" / "1.0.0" / "skill.py").is_file())
        info = self.store.describe("davide.moneta")["versions"]["1.0.0"]
        self.assertEqual((info["status"], info["capabilities"], info["highest_risk"], info["changelog"], info["key_id"]),
                         ("ok", ["network"], "external_action", "prima versione", self.key.key_id))
        self.assertEqual(info["digest"], sha256_hex(package))
        self.assertEqual(self.store.active_version("davide.moneta"), "1.0.0")

    def test_installing_never_executes_package_code_or_setup_scripts(self):
        marker = self.tmp / "ESEGUITO.marker"
        boom = f"from pathlib import Path\nPath(r'{marker}').write_text('x')\n"
        package, signature = self.signed(source=boom, extra={"setup.py": boom, "conftest.py": boom, "hook.pth": f"import os; open(r'{marker}','w')"})
        self.install(package, signature)
        self.assertFalse(marker.exists())

    def test_a_published_version_is_immutable(self):
        first, first_signature = self.signed(name="a")
        self.install(first, first_signature)
        second, second_signature = self.signed(name="b", source=MARKER_SOURCE + "\n# contenuto diverso\n")
        plan = self.store.plan_install(second, second_signature)
        self.assertTrue(any(item.startswith("version_conflict") for item in plan.blockers))
        with self.assertRaises(PackageError) as ctx:
            self.store.install(plan, approve(plan, "davide"))
        self.assertEqual(ctx.exception.code, "blocked")
        again = self.store.plan_install(first, first_signature)
        self.assertTrue(any(item.startswith("already_installed") for item in again.blockers))

    def test_an_update_that_adds_permissions_needs_a_separate_approval(self):
        self.publish("1.0.0")
        risky = intent(risk="local_reversible", effect_class="modify", capabilities=["filesystem.write"],
                       verifier={"kind": "declarative", "expect": "file scritto"})
        package, signature = self.signed(name="v2", version="1.1.0", intents=[risky])
        plan = self.store.plan_install(package, signature)
        self.assertEqual(plan.added_capabilities, ["filesystem.write"])
        self.assertTrue(plan.risk_increased and plan.permissions_increased)
        self.assertIn("nuove capability", plan.summary)
        with self.assertRaises(PackageError) as ctx:
            self.store.install(plan, approve(plan, "davide"))
        self.assertEqual(ctx.exception.code, "permissions_increased")
        self.assertEqual(self.store.active_version("davide.moneta"), "1.0.0")
        self.store.install(plan, approve(plan, "davide", allow_permission_increase=True))
        self.assertEqual(self.store.active_version("davide.moneta"), "1.1.0")

    def test_an_update_with_the_same_or_lower_permissions_needs_no_extra_approval(self):
        self.publish("1.0.0")
        self.assertTrue(self.publish("1.1.0")["activated"])

    def test_a_downgrade_needs_its_own_approval(self):
        self.publish("2.0.0")
        package, signature = self.signed(name="old", version="1.0.0")
        plan = self.store.plan_install(package, signature)
        self.assertTrue(plan.is_downgrade)
        with self.assertRaises(PackageError) as ctx:
            self.store.install(plan, approve(plan, "davide"))
        self.assertEqual(ctx.exception.code, "downgrade_not_approved")
        self.store.install(plan, approve(plan, "davide", allow_downgrade=True))

    def test_another_skill_cannot_take_an_installed_intent(self):
        self.publish("1.0.0")
        package, signature = self.signed(name="clone", skill_id="davide.clone")
        plan = self.store.plan_install(package, signature)
        self.assertTrue(any(item.startswith("intent_collision") for item in plan.blockers))
        with self.assertRaises(PackageError):
            self.store.install(plan, approve(plan, "davide"))

    def test_dependencies_are_resolved_from_installed_packages_and_nothing_is_ever_fetched(self):
        dep = [{"name": "requests", "spec": ">=2.31,<3"}]
        package, signature = self.signed(dependencies=dep)
        cases = ((None, "dependency_missing"), ("2.20.0", "dependency_conflict"), ("3.0.0", "dependency_conflict"))
        for installed, expected in cases:
            store = SkillStore(self.tmp / f"s_{installed}", self.trust, ENV, installed_versions=lambda name, v=installed: v)
            plan = store.plan_install(package, signature)
            self.assertTrue(any(item.startswith(expected) for item in plan.blockers), (installed, plan.blockers))
            with self.assertRaises(PackageError):
                store.install(plan, approve(plan, "davide"))
        satisfied = SkillStore(self.tmp / "s_ok", self.trust, ENV, installed_versions=lambda name: "2.32.3")
        plan = satisfied.plan_install(package, signature)
        self.assertEqual(plan.blockers, [])

    def test_python_package_versions_come_from_metadata_without_importing_them(self):
        self.assertRegex(installed_python_version("cryptography") or "", r"^\d+\.\d+\.\d+$")
        self.assertIsNone(installed_python_version("questo-pacchetto-non-esiste-jake"))

    def test_the_module_contains_no_network_or_process_code(self):
        tree = ast.parse(Path(skill_package.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        forbidden = {"urllib", "http", "socket", "ssl", "requests", "ftplib", "smtplib", "subprocess", "asyncio", "webbrowser", "pip"}
        self.assertEqual(imported & forbidden, set())

    def test_state_survives_a_restart(self):
        self.publish("1.0.0")
        reopened = SkillStore(self.tmp / "skills", TrustStore(self.tmp / "trust.json"), ENV, installed_versions=lambda name: None)
        self.assertEqual(reopened.describe("davide.moneta"), self.store.describe("davide.moneta"))


# ---- integrita' all'uso e F8.2.6 ---------------------------------------------------------------------------------------------


class IntegrityAndQuarantineTests(PackageTestCase):
    def marker(self, version="1.0.0") -> Path:
        return self.tmp / "skills" / "store" / "davide.moneta" / version / "IMPORTED.marker"

    def test_a_healthy_installed_skill_loads_and_its_output_matches_the_declared_schema(self):
        self.publish("1.0.0")
        registry = FakeRegistry()
        result = self.store.load(registry, "davide.moneta")
        self.assertTrue(result.ok, result.errors)
        spec = result.manifest.intents[0]
        output = registry.skills["TOSS_DEMO_COIN"].execute({"times": 1})
        self.assertEqual(validate_instance(spec.output_schema, output), [])
        self.assertFalse(list((self.tmp / "skills" / "store").rglob("__pycache__")), "il caricamento non deve scrivere bytecode")

    def test_a_file_altered_after_installation_is_quarantined_and_never_imported(self):
        self.publish("1.0.0")
        skill_file = self.tmp / "skills" / "store" / "davide.moneta" / "1.0.0" / "skill.py"
        skill_file.write_text(MARKER_SOURCE + "\nimport os  # alterato\n", encoding="utf-8")
        registry = FakeRegistry()
        with self.assertRaises(PackageError) as ctx:
            self.store.load(registry, "davide.moneta")
        self.assertEqual(ctx.exception.code, "no_usable_version")
        self.assertFalse(self.marker().exists(), "il codice alterato NON deve essere stato importato")
        self.assertEqual(registry.skills, {})
        info = self.store.describe("davide.moneta")["versions"]["1.0.0"]
        self.assertEqual(info["status"], "quarantined")
        self.assertIn("alterato", info["status_reason"])

    def test_an_altered_latest_version_falls_back_to_the_previous_healthy_one(self):
        self.publish("1.0.0")
        self.publish("1.1.0")
        (self.tmp / "skills" / "store" / "davide.moneta" / "1.1.0" / "skill.py").write_text("raise SystemExit\n", encoding="utf-8")
        registry = FakeRegistry()
        result = self.store.load(registry, "davide.moneta")
        self.assertTrue(result.ok)
        self.assertEqual(self.store.active_version("davide.moneta"), "1.0.0")
        self.assertEqual(self.store.describe("davide.moneta")["versions"]["1.1.0"]["status"], "quarantined")

    def test_a_planted_extra_file_or_missing_file_is_detected(self):
        self.publish("1.0.0")
        base = self.tmp / "skills" / "store" / "davide.moneta" / "1.0.0"
        (base / "backdoor.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(self.store.verify_installed("davide.moneta", "1.0.0"), ["backdoor.py: file estraneo non presente nel pacchetto firmato"])
        (base / "backdoor.py").unlink()
        (base / "test_skill.py").unlink()
        self.assertEqual(self.store.verify_installed("davide.moneta", "1.0.0"), ["test_skill.py: mancante"])

    def test_a_planted_bytecode_cache_is_deleted_not_trusted(self):
        self.publish("1.0.0")
        cache = self.tmp / "skills" / "store" / "davide.moneta" / "1.0.0" / "__pycache__"
        cache.mkdir()
        (cache / "skill.cpython-312.pyc").write_bytes(b"\x00garbage")
        result = self.store.load(FakeRegistry(), "davide.moneta")
        self.assertTrue(result.ok, result.errors)
        self.assertFalse(cache.exists())

    def test_quarantine_is_reversible_only_for_intact_versions_and_falls_back(self):
        self.publish("1.0.0")
        self.publish("1.1.0")
        self.store.quarantine("davide.moneta", "1.1.0", "comportamento sospetto")
        self.assertEqual(self.store.active_version("davide.moneta"), "1.0.0")
        self.store.release("davide.moneta", "1.1.0")
        self.assertEqual(self.store.describe("davide.moneta")["versions"]["1.1.0"]["status"], "ok")
        self.store.quarantine("davide.moneta", "1.1.0", "di nuovo")
        (self.tmp / "skills" / "store" / "davide.moneta" / "1.1.0" / "skill.py").write_text("x=1\n", encoding="utf-8")
        with self.assertRaises(PackageError) as ctx:
            self.store.release("davide.moneta", "1.1.0")
        self.assertEqual(ctx.exception.code, "tampered")

    def test_a_revoked_version_is_never_released_and_its_digest_is_never_reinstalled(self):
        package, signature = self.signed()
        self.install(package, signature)
        self.store.revoke_version("davide.moneta", "1.0.0", "vulnerabilita' nota")
        with self.assertRaises(PackageError) as ctx:
            self.store.release("davide.moneta", "1.0.0")
        self.assertEqual(ctx.exception.code, "revoked")
        self.assertIsNone(self.store.active_version("davide.moneta"))
        plan = self.store.plan_install(package, signature)
        self.assertTrue(any(item.startswith("digest_revoked") for item in plan.blockers))
        with self.assertRaises(PackageError):
            self.store.load(FakeRegistry(), "davide.moneta")

    def test_a_signed_revocation_list_revokes_installed_and_future_digests(self):
        self.publish("1.0.0")
        package_2, signature_2 = self.signed(name="v2", version="2.0.0")
        digest_1 = self.store.describe("davide.moneta")["versions"]["1.0.0"]["digest"]
        signed = self.key.sign_revocations([
            {"id": "davide.moneta", "digest": digest_1, "reason": "CVE-2026-0001"},
            {"id": "davide.moneta", "digest": sha256_hex(package_2), "reason": "mai rilasciare"},
            {"id": "mario.altro", "digest": "f" * 64, "reason": "fuori dal prefisso"},
        ])
        self.assertEqual(self.store.apply_revocation_list(signed), 2)
        self.assertEqual(self.store.describe("davide.moneta")["versions"]["1.0.0"]["status"], "revoked")
        plan = self.store.plan_install(package_2, signature_2)
        self.assertTrue(any(item.startswith("digest_revoked") for item in plan.blockers))
        self.assertNotIn("f" * 64, json.loads((self.tmp / "skills" / "catalog.json").read_text(encoding="utf-8"))["denied_digests"])

    def test_forged_or_tampered_revocation_lists_are_rejected(self):
        self.publish("1.0.0")
        digest = self.store.describe("davide.moneta")["versions"]["1.0.0"]["digest"]
        signed = self.key.sign_revocations([{"id": "davide.moneta", "digest": digest, "reason": "x"}])
        stranger = PublisherKey.generate().sign_revocations([{"id": "davide.moneta", "digest": digest, "reason": "x"}])
        tampered = {**signed, "revocations": [{"id": "davide.moneta", "digest": "0" * 64, "reason": "x"}]}
        for bad, code in ((stranger, "unknown_signer"), (tampered, "bad_signature"), ({}, "bad_signature"), ("x", "bad_signature")):
            with self.assertRaises(PackageError) as ctx:
                self.store.apply_revocation_list(bad)
            self.assertEqual(ctx.exception.code, code)
        self.assertEqual(self.store.describe("davide.moneta")["versions"]["1.0.0"]["status"], "ok")

    def test_revoking_a_signer_quarantines_everything_it_signed_and_nothing_else(self):
        self.publish("1.0.0")
        other_key = PublisherKey.generate()
        self.trust.add("Mario", other_key.public_b64(), "mario.")
        package, signature = self.signed(key=other_key, name="mario", skill_id="mario.strumento", intents=[intent(intent="MARIO_DEMO_TOOL")])
        self.install(package, signature)
        affected = self.store.revoke_signer(self.key.key_id, "chiave compromessa")
        self.assertEqual(affected, [("davide.moneta", "1.0.0")])
        self.assertEqual(self.store.describe("davide.moneta")["versions"]["1.0.0"]["status"], "quarantined")
        self.assertIsNone(self.store.active_version("davide.moneta"))
        self.assertEqual(self.store.describe("mario.strumento")["versions"]["1.0.0"]["status"], "ok")
        new_package, new_signature = self.signed(name="dopo", version="1.2.0")
        with self.assertRaises(PackageError) as ctx:
            self.store.plan_install(new_package, new_signature)
        self.assertEqual(ctx.exception.code, "signer_revoked")


# ---- F8.2.7 ---------------------------------------------------------------------------------------------------------------------


class PinAndRollbackTests(PackageTestCase):
    def test_rollback_returns_to_the_previous_version_and_pins_it(self):
        self.publish("1.0.0")
        self.publish("1.1.0")
        self.assertEqual(self.store.rollback("davide.moneta"), "1.0.0")
        description = self.store.describe("davide.moneta")
        self.assertEqual((description["active"], description["pinned"]), ("1.0.0", "1.0.0"))
        result = self.publish("1.2.0")
        self.assertFalse(result["activated"])
        self.assertIn("1.0.0", result["reason"])
        self.assertEqual(self.store.active_version("davide.moneta"), "1.0.0")
        self.store.pin("davide.moneta", "1.2.0")
        self.assertEqual(self.store.active_version("davide.moneta"), "1.2.0")
        self.store.unpin("davide.moneta")
        self.assertIsNone(self.store.describe("davide.moneta")["pinned"])

    def test_rollback_skips_an_altered_version_and_refuses_without_a_healthy_one(self):
        self.publish("1.0.0")
        self.publish("1.1.0")
        self.publish("1.2.0")
        (self.tmp / "skills" / "store" / "davide.moneta" / "1.1.0" / "skill.py").write_text("x=1\n", encoding="utf-8")
        self.assertEqual(self.store.rollback("davide.moneta"), "1.0.0")
        solo = SkillStore(self.tmp / "solo", self.trust, ENV, installed_versions=lambda name: None)
        self.publish("1.0.0", store=solo)
        with self.assertRaises(PackageError) as ctx:
            solo.rollback("davide.moneta")
        self.assertEqual(ctx.exception.code, "no_previous_version")

    def test_a_pin_needs_an_installed_healthy_version(self):
        self.publish("1.0.0")
        with self.assertRaises(PackageError):
            self.store.pin("davide.moneta", "9.9.9")
        self.store.quarantine("davide.moneta", "1.0.0", "x")
        with self.assertRaises(PackageError):
            self.store.pin("davide.moneta", "1.0.0")

    def test_old_versions_are_pruned_but_the_active_and_pinned_are_kept(self):
        for version in ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"):
            self.publish(version)
        versions = set(self.store.describe("davide.moneta")["versions"])
        self.assertLessEqual(len(versions), KEEP_VERSIONS)
        self.assertIn("1.4.0", versions)
        for version in ("1.0.0", "1.1.0"):
            self.assertNotIn(version, versions)
            self.assertFalse((self.tmp / "skills" / "store" / "davide.moneta" / version).exists())

    def test_end_to_end_build_sign_install_load_rollback_load(self):
        self.publish("1.0.0", source=MARKER_SOURCE.replace('"testa", "croce"', '"testa", "testa"'))
        self.publish("1.1.0")
        registry = FakeRegistry()
        self.assertTrue(self.store.load(registry, "davide.moneta").ok)
        self.store.rollback("davide.moneta")
        registry_v1 = FakeRegistry()
        result = self.store.load(registry_v1, "davide.moneta")
        self.assertTrue(result.ok)
        self.assertEqual(registry_v1.skills["TOSS_DEMO_COIN"].execute({"times": 1}), {"result": "testa"})
        self.assertEqual(result.manifest.version, "1.0.0")


if __name__ == "__main__":
    unittest.main()
