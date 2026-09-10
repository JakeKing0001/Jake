"""F0.3: contratto unico tra manifest, Python, CLI, README e release notes."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

from core.version import PROTOCOL_VERSION, VERSION


ROOT = Path(__file__).resolve().parent.parent


class ReleaseManifestTests(unittest.TestCase):
    def test_manifest_drives_the_python_version_constants(self):
        manifest = json.loads((ROOT / "config" / "release.json").read_text(encoding="utf-8"))

        self.assertEqual(VERSION, manifest["version"])
        self.assertEqual(PROTOCOL_VERSION, manifest["protocol_version"])

    def test_current_version_has_release_notes(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## [{VERSION}]", changelog)

    def test_readme_points_to_the_canonical_version_command(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("main.py --version", readme)


class VersionCommandTests(unittest.TestCase):
    def test_version_command_reports_product_and_protocol_versions(self):
        completed = subprocess.run(
            [sys.executable, "main.py", "--version"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), f"Jake {VERSION} (protocollo {PROTOCOL_VERSION})")


if __name__ == "__main__":
    unittest.main()
