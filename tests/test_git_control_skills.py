"""Test unitari per skills/git_control.py: nessuna suite esisteva finora. Usa repository git
VERI su cartelle temporanee (git init/commit reali), non un finto subprocess: e' proprio
l'interazione con git per davvero (branch di default, commit, modifiche non tracciate) a essere
la parte interessante da verificare."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from skills.git_control import GitBranchSkill, GitDiffSkill, GitLogSkill, GitPullSkill, GitStatusSkill


def _run_git(repo: Path, *args) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


class _WithRealRepo(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="jake_git_control_test_"))
        _run_git(self.repo, "init", "-q", "-b", "main")
        _run_git(self.repo, "config", "user.email", "test@example.com")
        _run_git(self.repo, "config", "user.name", "Jake Test")
        (self.repo / "README.md").write_text("ciao", encoding="utf-8")
        _run_git(self.repo, "add", "README.md")
        _run_git(self.repo, "commit", "-q", "-m", "iniziale")


class PathResolutionTests(unittest.TestCase):
    def test_nonexistent_path_fails_for_every_skill(self):
        bogus = {"path": r"C:\percorso\che\non\esiste\davvero\xyz"}
        for skill_cls in (GitStatusSkill, GitPullSkill, GitLogSkill, GitBranchSkill, GitDiffSkill):
            result = skill_cls().execute(bogus)
            self.assertEqual(result.error, "PATH_NOT_FOUND", msg=skill_cls.__name__)

    def test_a_directory_that_is_not_a_git_repo_reports_not_a_git_repo(self):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jake_git_control_nonrepo_test_"))
        for skill_cls in (GitStatusSkill, GitLogSkill, GitBranchSkill, GitDiffSkill):
            result = skill_cls().execute({"path": str(tmp_dir)})
            self.assertEqual(result.error, "NOT_A_GIT_REPO", msg=skill_cls.__name__)


class GitStatusTests(_WithRealRepo):
    def test_a_clean_repo_has_only_the_branch_header_line(self):
        """'--branch' aggiunge sempre una riga '## <branch>' anche a costo zero: uno stato
        pulito significa nessuna riga di FILE oltre a quella, non una lista vuota del tutto."""
        result = GitStatusSkill().execute({"path": str(self.repo)})
        self.assertTrue(result.success)
        self.assertEqual(result.data["status_lines"], ["## main"])

    def test_an_untracked_file_shows_up_in_status(self):
        (self.repo / "nuovo.txt").write_text("x", encoding="utf-8")
        result = GitStatusSkill().execute({"path": str(self.repo)})
        self.assertTrue(result.success)
        self.assertTrue(any("nuovo.txt" in line for line in result.data["status_lines"]))


class GitLogTests(_WithRealRepo):
    def test_shows_the_initial_commit(self):
        result = GitLogSkill().execute({"path": str(self.repo)})
        self.assertTrue(result.success)
        self.assertEqual(len(result.data["commits"]), 1)
        self.assertIn("iniziale", result.data["commits"][0])

    def test_respects_the_count_parameter(self):
        (self.repo / "b.txt").write_text("x", encoding="utf-8")
        _run_git(self.repo, "add", "b.txt")
        _run_git(self.repo, "commit", "-q", "-m", "secondo commit")

        result = GitLogSkill().execute({"path": str(self.repo), "count": 1})
        self.assertEqual(len(result.data["commits"]), 1)
        self.assertIn("secondo commit", result.data["commits"][0])

    def test_a_non_numeric_count_fails_gracefully_instead_of_crashing(self):
        result = GitLogSkill().execute({"path": str(self.repo), "count": "molti"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")


class GitBranchTests(_WithRealRepo):
    def test_returns_the_current_branch_name(self):
        result = GitBranchSkill().execute({"path": str(self.repo)})
        self.assertTrue(result.success)
        self.assertEqual(result.data["branch"], "main")


class GitDiffTests(_WithRealRepo):
    def test_no_changes_reports_not_found(self):
        result = GitDiffSkill().execute({"path": str(self.repo)})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_a_modified_tracked_file_shows_a_summary(self):
        (self.repo / "README.md").write_text("modificato", encoding="utf-8")
        result = GitDiffSkill().execute({"path": str(self.repo)})
        self.assertTrue(result.success)
        self.assertIn("README.md", result.data["summary"])


class GitPullTests(_WithRealRepo):
    def test_pull_without_a_configured_remote_fails_gracefully(self):
        result = GitPullSkill().execute({"path": str(self.repo)})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")
        self.assertTrue(result.data.get("message"))


if __name__ == "__main__":
    unittest.main()
