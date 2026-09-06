import re
import subprocess
from urllib import error, request

from core.skill_result import SkillResult


class PingHostSkill:
    metadata = {
        "intent": "PING_HOST",
        "description": "Fa un ping verso un host/indirizzo per verificare se e' raggiungibile e con che latenza.",
        "parameters": {
            "host": {
                "type": "string",
                "required": True,
                "description": "Indirizzo o nome host da contattare, es. 'google.com'.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        host = (parameters.get("host") or "").strip()
        if not host:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        try:
            result = subprocess.run(
                ["ping", "-n", "3", host], capture_output=True, timeout=15, text=True, encoding="utf-8", errors="ignore",
            )
        except Exception:
            return SkillResult(success=False, data={"host": host}, error="OPERATION_FAILED")

        if result.returncode != 0:
            return SkillResult(success=False, data={"host": host}, error="HOST_UNREACHABLE")

        match = re.search(r"Media\s*=\s*(\d+ms)|Average\s*=\s*(\d+ms)", result.stdout)
        latency = next((g for g in (match.groups() if match else []) if g), None)
        return SkillResult(success=True, data={"host": host, "latency": latency or "n/d"})


class TraceRouteSkill:
    metadata = {
        "intent": "TRACE_ROUTE",
        "description": "Traccia il percorso di rete verso un host, mostrando i salti intermedi.",
        "parameters": {
            "host": {
                "type": "string",
                "required": True,
                "description": "Indirizzo o nome host verso cui tracciare il percorso.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        host = (parameters.get("host") or "").strip()
        if not host:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        try:
            result = subprocess.run(
                ["tracert", "-h", "12", host], capture_output=True, timeout=45, text=True, encoding="utf-8", errors="ignore",
            )
        except Exception:
            return SkillResult(success=False, data={"host": host}, error="OPERATION_FAILED")

        hops = [line.strip() for line in result.stdout.splitlines() if re.match(r"^\s*\d+", line)]
        if not hops:
            return SkillResult(success=False, data={"host": host}, error="HOST_UNREACHABLE")
        return SkillResult(success=True, data={"host": host, "hop_count": len(hops)})


class CheckWebsiteStatusSkill:
    metadata = {
        "intent": "CHECK_WEBSITE_STATUS",
        "description": "Verifica se un sito web e' raggiungibile in questo momento.",
        "remote": True,
        "parameters": {
            "url": {
                "type": "string",
                "required": True,
                "description": "Indirizzo del sito da controllare, es. 'https://google.com'.",
            },
        },
    }

    def __init__(self, timeout: float = 8):
        self.timeout = timeout

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        url = (parameters.get("url") or "").strip()
        if not url:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        try:
            with request.urlopen(url, timeout=self.timeout) as response:
                status_code = response.status
        except error.HTTPError as exc:
            status_code = exc.code
        except (error.URLError, TimeoutError):
            return SkillResult(success=True, data={"url": url, "online": False})

        return SkillResult(success=True, data={"url": url, "online": status_code < 500, "status_code": status_code})
