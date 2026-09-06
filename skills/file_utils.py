import shutil
from datetime import datetime
from pathlib import Path

from core.skill_result import SkillResult


class CompressPathSkill:
    metadata = {
        "intent": "COMPRESS_PATH",
        "description": "Comprime una cartella o un file in un archivio .zip.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Percorso della cartella o del file da comprimere.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        if not raw_path:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        path = Path(raw_path).expanduser()
        if not path.exists():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")

        try:
            if path.is_dir():
                archive_path = shutil.make_archive(str(path), "zip", root_dir=str(path))
            else:
                archive_path = shutil.make_archive(str(path.with_suffix("")), "zip", root_dir=str(path.parent), base_dir=path.name)
        except OSError:
            return SkillResult(success=False, data={"path": raw_path}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"path": raw_path, "archive_path": archive_path})


class ExtractArchiveSkill:
    metadata = {
        "intent": "EXTRACT_ARCHIVE",
        "description": "Estrae un archivio .zip in una cartella.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Percorso del file .zip da estrarre.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        if not raw_path:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        path = Path(raw_path).expanduser()
        if not path.is_file():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")

        destination = path.with_suffix("")
        try:
            shutil.unpack_archive(str(path), str(destination))
        except (OSError, shutil.ReadError, ValueError):
            return SkillResult(success=False, data={"path": raw_path}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"path": raw_path, "destination": str(destination)})


class GetFileInfoSkill:
    metadata = {
        "intent": "GET_FILE_INFO",
        "description": "Restituisce dimensione e data di modifica di un file o di una cartella.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Percorso del file o della cartella.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        if not raw_path:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        path = Path(raw_path).expanduser()
        if not path.exists():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")

        stats = path.stat()
        return SkillResult(success=True, data={
            "path": raw_path,
            "size_kb": round(stats.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stats.st_mtime).strftime("%d/%m/%Y %H:%M"),
            "is_folder": path.is_dir(),
        })


class CountWordsInFileSkill:
    metadata = {
        "intent": "COUNT_WORDS_IN_FILE",
        "description": "Conta le parole in un file di testo.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Percorso del file di testo.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        if not raw_path:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        path = Path(raw_path).expanduser()
        if not path.is_file():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return SkillResult(success=False, data={"path": raw_path}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"path": raw_path, "words": len(content.split())})


class ReadFileTextSkill:
    metadata = {
        "intent": "READ_FILE_TEXT",
        "description": "Legge il contenuto testuale di un file. Diverso da READ_SCREEN, che legge cosa e' visibile sullo schermo.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Percorso del file di testo da leggere.",
            },
        },
    }

    MAX_CHARS = 1500

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        if not raw_path:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        path = Path(raw_path).expanduser()
        if not path.is_file():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return SkillResult(success=False, data={"path": raw_path}, error="OPERATION_FAILED")

        truncated = len(content) > self.MAX_CHARS
        return SkillResult(success=True, data={"path": raw_path, "text": content[:self.MAX_CHARS], "truncated": truncated})


class DuplicateFileSkill:
    metadata = {
        "intent": "DUPLICATE_FILE",
        "description": "Duplica un file nella stessa cartella, aggiungendo 'copia' al nome.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Percorso del file da duplicare.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        if not raw_path:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        path = Path(raw_path).expanduser()
        if not path.is_file():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")

        destination = path.with_name(f"{path.stem} - copia{path.suffix}")
        counter = 2
        while destination.exists():
            destination = path.with_name(f"{path.stem} - copia {counter}{path.suffix}")
            counter += 1

        try:
            shutil.copy2(path, destination)
        except OSError:
            return SkillResult(success=False, data={"path": raw_path}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"path": raw_path, "destination": str(destination)})


class GetFolderSizeSkill:
    metadata = {
        "intent": "GET_FOLDER_SIZE",
        "description": "Calcola lo spazio totale occupato da una cartella e dal suo contenuto.",
        "parameters": {
            "path": {
                "type": "string",
                "required": True,
                "description": "Percorso della cartella.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        raw_path = (parameters.get("path") or "").strip()
        if not raw_path:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        path = Path(raw_path).expanduser()
        if not path.is_dir():
            return SkillResult(success=False, data={"path": raw_path}, error="PATH_NOT_FOUND")

        total_size = 0
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    total_size += entry.stat().st_size
            except OSError:
                continue

        return SkillResult(success=True, data={"path": raw_path, "size_mb": round(total_size / (1024 ** 2), 1)})
