"""F0.6: aggiornamento atomico. Runner finto: nessun git/pip reale viene eseguito."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.updater import Updater, keygen, sign_release


class FakeRunner:
    def __init__(self, dirty="", lock_changed=True, healthy=True, tags="v1.1\nv1.0"):
        self.calls = []
        self.dirty, self.lock_changed, self.healthy, self.tags = dirty, lock_changed, healthy, tags

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ["git", "status"]:
            return 0, self.dirty
        if args[:2] == ["git", "tag"]:
            return 0, self.tags
        if args[:2] == ["git", "show"]:
            return (0, self.manifest) if getattr(self, "manifest", None) else (128, "fatal")
        if args[:2] == ["git", "rev-parse"]:
            return 0, "old" if args[2] == "HEAD" else "new"
        if args[:2] == ["git", "diff"]:
            return 0, "requirements/all.lock.txt" if self.lock_changed else ""
        if len(args) > 1 and args[1] == "-c":
            return (0, "") if self.healthy else (1, "ImportError: boom")
        return 0, ""


class UpdaterTests(unittest.TestCase):
    def test_local_changes_are_never_overwritten(self):
        runner = FakeRunner(dirty=" M core/agent.py")
        result = Updater(runner, python="py").update()
        self.assertEqual(result.status, "refused")
        self.assertFalse(any(a[:2] == ["git", "merge"] for a in runner.calls))

    def test_healthy_update_fast_forwards_to_latest_tag_and_installs_changed_deps(self):
        runner = FakeRunner()
        result = Updater(runner, python="py").update("stable")
        self.assertEqual(result.status, "updated")
        self.assertIn(["git", "rev-parse", "v1.1^{commit}"], runner.calls)
        self.assertIn(["git", "merge", "--ff-only", "new"], runner.calls)
        self.assertTrue(any("--require-hashes" in a for a in runner.calls))
        self.assertNotIn(["git", "reset", "--keep", "old"], runner.calls)

    def test_failed_health_check_rolls_back_code_and_dependencies(self):
        runner = FakeRunner(healthy=False)
        result = Updater(runner, python="py").update("dev")
        self.assertEqual(result.status, "rolled_back")
        reset_at = runner.calls.index(["git", "reset", "--keep", "old"])
        self.assertTrue(any("--require-hashes" in a for a in runner.calls[reset_at:]))


class SignedStableTests(unittest.TestCase):
    """F0.6.5: chiavi Ed25519 vere, git finto."""

    def _signed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        key_path, out = Path(tmp.name) / "key.txt", Path(tmp.name) / "stable.json"
        public = keygen(key_path)
        sign_release("v2.0", runner=lambda args: (0, "new"), key_path=key_path, out_path=out)
        return public, json.loads(out.read_text(encoding="utf-8"))

    def test_a_validly_signed_stable_release_is_installed(self):
        public, data = self._signed()
        runner = FakeRunner()
        runner.manifest = json.dumps(data)
        result = Updater(runner, python="py", public_key=public).update("stable")
        self.assertEqual(result.status, "updated")
        self.assertIn("v2.0", result.message)

    def test_a_tampered_manifest_is_refused_without_touching_the_code(self):
        public, data = self._signed()
        data["manifest"]["build_digest"] = "evil"
        runner = FakeRunner()
        runner.manifest = json.dumps(data)
        result = Updater(runner, python="py", public_key=public).update("stable")
        self.assertEqual(result.status, "refused")
        self.assertFalse(any(a[:2] == ["git", "merge"] for a in runner.calls))


if __name__ == "__main__":
    unittest.main()
