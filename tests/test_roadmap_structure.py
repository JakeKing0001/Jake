"""F0.3.6: ogni pacchetto attivo della roadmap compatta ha owner e stato espliciti."""
from pathlib import Path
import re
import unittest


ROADMAP = Path(__file__).resolve().parent.parent / "ROADMAP_EXECUTION.md"
ALLOWED_STATES = {"DONE", "VERIFY", "DOING", "READY", "BLOCKED", "BACKLOG", "MOONSHOT"}


class RoadmapOwnershipTests(unittest.TestCase):
    def test_every_package_has_exactly_one_owner_and_state(self):
        content = ROADMAP.read_text(encoding="utf-8")
        package_ids = re.findall(r"(?m)^### (F\d+\.\d+)\b", content)
        self.assertTrue(package_ids, "nessun pacchetto attivo: il registro non deve passare per vacuita'")
        self.assertEqual(len(package_ids), len(set(package_ids)), "sezioni pacchetto duplicate")
        registry_match = re.search(
            r"(?ms)^## Registro owner e stato dei pacchetti attivi\s+(.*?)(?=^## |\Z)", content,
        )
        self.assertIsNotNone(registry_match, "registro owner/stato assente")
        rows = re.findall(
            r"(?m)^\| `(F\d+\.\d+)` \| ([^|]+) \| `([A-Z]+)` \|$",
            registry_match.group(1),
        )

        registered_ids = [package_id for package_id, _owner, _state in rows]
        self.assertEqual(len(registered_ids), len(set(registered_ids)), "ID duplicato nel registro")
        self.assertEqual(set(registered_ids), set(package_ids))
        for package_id, owner, state in rows:
            with self.subTest(package_id=package_id):
                self.assertTrue(owner.strip())
                self.assertIn(state, ALLOWED_STATES)


if __name__ == "__main__":
    unittest.main()
