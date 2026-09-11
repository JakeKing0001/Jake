"""F0.2.7: regressioni della diagnostica pubblicata dalla CI."""
from pathlib import Path
import re
import unittest


WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
KNOWN_LIMITATIONS = Path(__file__).resolve().parent.parent / "docs" / "known-limitations.md"


class CiWorkflowTests(unittest.TestCase):
    def test_hidden_test_diagnostics_are_included_in_the_artifact(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        upload_step = re.search(
            r"(?ms)^      - name: Pubblica diagnostica test\s+"
            r".*?(?=^      - name:|^  [a-zA-Z][\w-]*:|\Z)",
            workflow,
        )

        self.assertIsNotNone(upload_step, "passo upload della diagnostica assente")
        block = upload_step.group(0)
        self.assertIn("uses: actions/upload-artifact@v4", block)
        self.assertIn("path: .ci-artifacts/", block)
        self.assertIn("include-hidden-files: true", block)
        self.assertIn("if-no-files-found: error", block)

    def test_diagnostics_upload_cannot_prevent_the_cli_smoke_test(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        smoke_position = workflow.index("- name: Smoke test")
        upload_position = workflow.index("- name: Pubblica diagnostica test")
        self.assertLess(smoke_position, upload_position)

    def test_unavailable_branch_protection_is_recorded_for_g0(self):
        limitations = KNOWN_LIMITATIONS.read_text(encoding="utf-8")

        self.assertIn("KL-001", limitations)
        self.assertIn("F0.2.6", limitations)
        self.assertIn("HTTP `403`", limitations)
        self.assertIn("GitHub Pro", limitations)
        self.assertIn("repository sia pubblico", limitations)


if __name__ == "__main__":
    unittest.main()
