import json
import platform
import socket
import time
from urllib import error, request

from core.network import is_online
from core.skill_result import SkillResult


class GetBatteryStatusSkill:
    metadata = {
        "intent": "GET_BATTERY_STATUS",
        "description": "Restituisce la percentuale di batteria e se e' in carica.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        import psutil

        battery = psutil.sensors_battery()
        if battery is None:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"percent": round(battery.percent), "plugged": battery.power_plugged})


class GetCpuUsageSkill:
    metadata = {
        "intent": "GET_CPU_USAGE",
        "description": "Restituisce la percentuale di utilizzo attuale della CPU.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        import psutil

        percent = psutil.cpu_percent(interval=0.5)
        return SkillResult(success=True, data={"percent": percent})


class GetMemoryUsageSkill:
    metadata = {
        "intent": "GET_MEMORY_USAGE",
        "description": "Restituisce quanta RAM e' in uso in questo momento.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        import psutil

        memory = psutil.virtual_memory()
        return SkillResult(success=True, data={
            "percent": memory.percent,
            "used_gb": round(memory.used / (1024 ** 3), 1),
            "total_gb": round(memory.total / (1024 ** 3), 1),
        })


class GetDiskUsageSkill:
    metadata = {
        "intent": "GET_DISK_USAGE",
        "description": "Restituisce lo spazio libero e usato su un disco.",
        "parameters": {
            "drive": {
                "type": "string",
                "required": False,
                "description": "Lettera dell'unita' (es. 'C:'). Se omesso usa il disco di sistema.",
            },
        },
    }

    def execute(self, parameters: dict = None):
        import psutil

        parameters = parameters or {}
        drive = (parameters.get("drive") or "C:").rstrip("\\/") + "\\"
        try:
            usage = psutil.disk_usage(drive)
        except OSError:
            return SkillResult(success=False, data={"drive": drive}, error="PATH_NOT_FOUND")

        return SkillResult(success=True, data={
            "drive": drive,
            "percent": usage.percent,
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "total_gb": round(usage.total / (1024 ** 3), 1),
        })


class GetUptimeSkill:
    metadata = {
        "intent": "GET_UPTIME",
        "description": "Restituisce da quanto tempo il computer e' acceso.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        import psutil

        seconds = int(time.time() - psutil.boot_time())
        hours, remainder = divmod(seconds, 3600)
        minutes = remainder // 60
        return SkillResult(success=True, data={"hours": hours, "minutes": minutes})


class GetSystemInfoSkill:
    metadata = {
        "intent": "GET_SYSTEM_INFO",
        "description": "Restituisce informazioni generali sul computer: sistema operativo, nome macchina, architettura.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        return SkillResult(success=True, data={
            "os": f"{platform.system()} {platform.release()}",
            "hostname": platform.node(),
            "architecture": platform.machine(),
        })


class GetLocalIpSkill:
    metadata = {
        "intent": "GET_LOCAL_IP",
        "description": "Restituisce l'indirizzo IP locale del computer nella rete domestica/aziendale.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                ip_address = sock.getsockname()[0]
        except OSError:
            return SkillResult(success=False, data={}, error="NETWORK_UNAVAILABLE")
        return SkillResult(success=True, data={"ip": ip_address})


class GetPublicIpSkill:
    metadata = {
        "intent": "GET_PUBLIC_IP",
        "description": "Restituisce l'indirizzo IP pubblico con cui il computer e' visto su internet.",
        "remote": True,
        "parameters": {},
    }

    def __init__(self, timeout: float = 8):
        self.timeout = timeout

    def execute(self, parameters: dict = None):
        if not is_online():
            return SkillResult(success=False, data={}, error="NETWORK_UNAVAILABLE")
        try:
            with request.urlopen("https://api.ipify.org?format=json", timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError, json.JSONDecodeError):
            return SkillResult(success=False, data={}, error="NETWORK_UNAVAILABLE")
        return SkillResult(success=True, data={"ip": payload.get("ip", "")})
