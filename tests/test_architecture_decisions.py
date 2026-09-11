"""F0.3.5: gli ADR critici devono esistere e restare decisioni verificabili, non note vaghe."""
from pathlib import Path
import unittest


ADR_DIR = Path(__file__).resolve().parent.parent / "docs" / "adr"
EXPECTED_ADRS = {
    "0001-windows-uia-adapter.md": "Windows UI Automation",
    "0002-plugin-sandbox-runtime.md": "sandbox",
    "0003-companion-transport.md": "trasporto",
    "0004-data-encryption.md": "cifratura",
    "0005-memory-storage.md": "memoria",
}
REQUIRED_SECTIONS = (
    "## Contesto",
    "## Decisione",
    "## Alternative considerate",
    "## Conseguenze",
    "## Piano di migrazione",
    "## Criterio di revisione",
)


class ArchitectureDecisionRecordTests(unittest.TestCase):
    def test_required_decisions_exist_with_a_stable_index(self):
        index = (ADR_DIR / "README.md").read_text(encoding="utf-8")
        for filename in EXPECTED_ADRS:
            with self.subTest(filename=filename):
                self.assertTrue((ADR_DIR / filename).is_file())
                self.assertIn(filename, index)

    def test_each_decision_has_status_and_required_sections(self):
        for filename, subject in EXPECTED_ADRS.items():
            with self.subTest(filename=filename):
                content = (ADR_DIR / filename).read_text(encoding="utf-8")
                self.assertIn(subject.lower(), content.lower())
                self.assertRegex(content, r"(?m)^- Stato: `(Proposed|Accepted)`$")
                self.assertRegex(content, r"(?m)^- Data: \d{4}-\d{2}-\d{2}$")
                for heading in REQUIRED_SECTIONS:
                    self.assertIn(heading, content)


if __name__ == "__main__":
    unittest.main()
