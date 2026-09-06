import subprocess
from pathlib import Path

from core.skill_result import SkillResult


def _resolve_repo_path(raw_path: str) -> Path | None:
    path = Path(raw_path).expanduser() if raw_path else Path.cwd()
    if not path.is_dir():
        return None
    return path


class GitStatusSkill:
    metadata = {
        "intent": "GIT_STATUS",
        "description": "Mostra lo stato git (file modificati, non tracciati) di un repository locale.",
        "parameters": {
            "path": {
                "type": "string",
                "required": False,
                "description": "Percorso della cartella del repository. Se omesso, usa la cartella corrente.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        repo_path = _resolve_repo_path(parameters.get("path"))
        if repo_path is None:
            return SkillResult(success=False, data={"path": parameters.get("path", "")}, error="PATH_NOT_FOUND")

        try:
            result = subprocess.run(
                ["git", "status", "--short", "--branch"],
                cwd=repo_path, capture_output=True, timeout=15, text=True, encoding="utf-8", errors="ignore",
            )
        except Exception:
            return SkillResult(success=False, data={"path": str(repo_path)}, error="OPERATION_FAILED")

        if result.returncode != 0:
            return SkillResult(success=False, data={"path": str(repo_path)}, error="NOT_A_GIT_REPO")

        lines = [line for line in result.stdout.splitlines() if line.strip()]
        return SkillResult(success=True, data={"path": str(repo_path), "status_lines": lines})


class GitPullSkill:
    metadata = {
        "intent": "GIT_PULL",
        "description": "Aggiorna un repository git locale scaricando le modifiche dal remoto (git pull).",
        "parameters": {
            "path": {
                "type": "string",
                "required": False,
                "description": "Percorso della cartella del repository. Se omesso, usa la cartella corrente.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        repo_path = _resolve_repo_path(parameters.get("path"))
        if repo_path is None:
            return SkillResult(success=False, data={"path": parameters.get("path", "")}, error="PATH_NOT_FOUND")

        try:
            result = subprocess.run(
                ["git", "pull"],
                cwd=repo_path, capture_output=True, timeout=60, text=True, encoding="utf-8", errors="ignore",
            )
        except Exception:
            return SkillResult(success=False, data={"path": str(repo_path)}, error="OPERATION_FAILED")

        if result.returncode != 0:
            return SkillResult(
                success=False,
                data={"path": str(repo_path), "message": (result.stderr or result.stdout).strip()},
                error="OPERATION_FAILED",
            )

        return SkillResult(success=True, data={"path": str(repo_path), "message": result.stdout.strip()})


class GitLogSkill:
    metadata = {
        "intent": "GIT_LOG",
        "description": "Mostra gli ultimi commit di un repository git locale.",
        "parameters": {
            "path": {"type": "string", "required": False, "description": "Percorso del repository. Se omesso usa la cartella corrente."},
            "count": {"type": "integer", "required": False, "description": "Quanti commit mostrare (default 5)."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        repo_path = _resolve_repo_path(parameters.get("path"))
        if repo_path is None:
            return SkillResult(success=False, data={"path": parameters.get("path", "")}, error="PATH_NOT_FOUND")
        count = parameters.get("count") or 5

        try:
            result = subprocess.run(
                ["git", "log", f"-{int(count)}", "--pretty=format:%h %s"],
                cwd=repo_path, capture_output=True, timeout=15, text=True, encoding="utf-8", errors="ignore",
            )
        except Exception:
            return SkillResult(success=False, data={"path": str(repo_path)}, error="OPERATION_FAILED")

        if result.returncode != 0:
            return SkillResult(success=False, data={"path": str(repo_path)}, error="NOT_A_GIT_REPO")

        commits = [line for line in result.stdout.splitlines() if line.strip()]
        return SkillResult(success=True, data={"path": str(repo_path), "commits": commits})


class GitBranchSkill:
    metadata = {
        "intent": "GIT_BRANCH",
        "description": "Restituisce il branch git attualmente attivo in un repository locale.",
        "parameters": {
            "path": {"type": "string", "required": False, "description": "Percorso del repository. Se omesso usa la cartella corrente."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        repo_path = _resolve_repo_path(parameters.get("path"))
        if repo_path is None:
            return SkillResult(success=False, data={"path": parameters.get("path", "")}, error="PATH_NOT_FOUND")

        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=repo_path, capture_output=True, timeout=15, text=True, encoding="utf-8", errors="ignore",
            )
        except Exception:
            return SkillResult(success=False, data={"path": str(repo_path)}, error="OPERATION_FAILED")

        branch = result.stdout.strip()
        if result.returncode != 0 or not branch:
            return SkillResult(success=False, data={"path": str(repo_path)}, error="NOT_A_GIT_REPO")

        return SkillResult(success=True, data={"path": str(repo_path), "branch": branch})


class GitDiffSkill:
    metadata = {
        "intent": "GIT_DIFF",
        "description": "Mostra un riepilogo delle modifiche non ancora committate in un repository locale.",
        "parameters": {
            "path": {"type": "string", "required": False, "description": "Percorso del repository. Se omesso usa la cartella corrente."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        repo_path = _resolve_repo_path(parameters.get("path"))
        if repo_path is None:
            return SkillResult(success=False, data={"path": parameters.get("path", "")}, error="PATH_NOT_FOUND")

        try:
            result = subprocess.run(
                ["git", "diff", "--stat"],
                cwd=repo_path, capture_output=True, timeout=15, text=True, encoding="utf-8", errors="ignore",
            )
        except Exception:
            return SkillResult(success=False, data={"path": str(repo_path)}, error="OPERATION_FAILED")

        if result.returncode != 0:
            return SkillResult(success=False, data={"path": str(repo_path)}, error="NOT_A_GIT_REPO")

        summary = result.stdout.strip()
        if not summary:
            return SkillResult(success=False, data={"path": str(repo_path)}, error="NOT_FOUND")
        return SkillResult(success=True, data={"path": str(repo_path), "summary": summary})
