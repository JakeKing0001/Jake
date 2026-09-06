import re
import subprocess

from core.skill_result import SkillResult


class ListWifiNetworksSkill:
    """Elenca le reti Wi-Fi visibili via 'netsh wlan show networks' (nessuna libreria esterna)."""

    metadata = {
        "intent": "LIST_WIFI_NETWORKS",
        "description": "Elenca le reti Wi-Fi disponibili nelle vicinanze.",
        "parameters": {},
    }

    SSID_PATTERN = re.compile(r"^SSID \d+\s*:\s*(.*)$")

    def execute(self, parameters: dict = None):
        try:
            output = subprocess.run(
                ["netsh", "wlan", "show", "networks"],
                check=True, capture_output=True, timeout=15, text=True, encoding="utf-8", errors="ignore",
            ).stdout
        except Exception:
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        networks = []
        for line in output.splitlines():
            match = self.SSID_PATTERN.match(line.strip())
            if match and match.group(1):
                networks.append(match.group(1))

        if not networks:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"networks": networks})
