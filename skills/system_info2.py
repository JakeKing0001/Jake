import os
import re
import subprocess
import uuid

from core.skill_result import SkillResult


class GetScreenResolutionSkill:
    metadata = {
        "intent": "GET_SCREEN_RESOLUTION",
        "description": "Restituisce la risoluzione dello schermo principale.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        import win32api

        width = win32api.GetSystemMetrics(0)
        height = win32api.GetSystemMetrics(1)
        return SkillResult(success=True, data={"width": width, "height": height})


class GetGpuInfoSkill:
    metadata = {
        "intent": "GET_GPU_INFO",
        "description": "Restituisce il nome della scheda video installata.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        try:
            output = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"],
                check=True, capture_output=True, timeout=15, text=True, encoding="utf-8", errors="ignore",
            ).stdout
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        names = [line.strip() for line in output.splitlines() if line.strip()]
        if not names:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"gpus": names})


class GetDnsServersSkill:
    metadata = {
        "intent": "GET_DNS_SERVERS",
        "description": "Elenca i server DNS configurati sulla connessione di rete attiva.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        try:
            output = subprocess.run(
                ["ipconfig", "/all"], check=True, capture_output=True, timeout=15, text=True, encoding="utf-8", errors="ignore",
            ).stdout
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        servers = re.findall(r"(?:DNS Servers|Server DNS)[^\d]*(\d+\.\d+\.\d+\.\d+)", output)
        if not servers:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"servers": servers})


class GetEnvironmentVariableSkill:
    metadata = {
        "intent": "GET_ENVIRONMENT_VARIABLE",
        "description": "Legge il valore di una variabile d'ambiente di sistema.",
        "parameters": {
            "name": {"type": "string", "required": True, "description": "Nome della variabile d'ambiente, es. 'PATH'."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        name = (parameters.get("name") or "").strip()
        if not name:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        value = os.environ.get(name)
        if value is None:
            return SkillResult(success=False, data={"name": name}, error="NOT_FOUND")
        return SkillResult(success=True, data={"name": name, "value": value})


class GetMacAddressSkill:
    metadata = {
        "intent": "GET_MAC_ADDRESS",
        "description": "Restituisce l'indirizzo MAC della scheda di rete del computer.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        mac = uuid.getnode()
        formatted = ":".join(f"{(mac >> shift) & 0xff:02x}" for shift in range(40, -1, -8))
        return SkillResult(success=True, data={"mac_address": formatted})


class ListDrivesSkill:
    metadata = {
        "intent": "LIST_DRIVES",
        "description": "Elenca tutte le unita' disco montate con lo spazio libero di ciascuna.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        import psutil

        drives = []
        for partition in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
            except OSError:
                continue
            drives.append({
                "drive": partition.device,
                "free_gb": round(usage.free / (1024 ** 3), 1),
                "total_gb": round(usage.total / (1024 ** 3), 1),
            })

        if not drives:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"drives": drives})
