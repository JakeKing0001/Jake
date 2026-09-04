import base64
import json
from urllib import error, request


class RvcError(Exception):
    """Errore riportato dal server locale di conversione vocale RVC."""


class RvcClient:
    """Client verso il server RVC locale (core/voice/rvc_server.py), stesso pattern di NestClient."""

    def __init__(self, base_url: str = "http://127.0.0.1:5050", timeout: float = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def is_available(self) -> bool:
        try:
            with request.urlopen(f"{self.base_url}/models", timeout=2) as response:
                return response.status == 200
        except (error.URLError, TimeoutError):
            return False

    def convert(self, audio_bytes: bytes) -> bytes:
        """Converte un WAV con il modello attualmente caricato dal server. Restituisce il WAV convertito."""
        payload = json.dumps({"audio_data": base64.b64encode(audio_bytes).decode("ascii")}).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/convert",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                return response.read()
        except (error.URLError, TimeoutError) as exc:
            raise RvcError(str(exc)) from exc
