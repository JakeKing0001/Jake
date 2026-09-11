"""F0.2.7: regressioni della diagnostica pubblicata dalla CI."""
from pathlib import Path
import re
import unittest


WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


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


if __name__ == "__main__":
    unittest.main()
