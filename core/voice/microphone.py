import numpy as np


class MicrophoneError(Exception):
    """Errore di accesso al microfono (dispositivo assente, permessi, driver, ...)."""


class Microphone:
    """Cattura audio dal microfono predefinito in modalità push-to-talk."""

    def __init__(self, sample_rate: int = 16000, device: int = None):
        self.sample_rate = sample_rate
        self.device = device

    def is_available(self) -> bool:
        try:
            import sounddevice as sd
            devices = sd.query_devices()
        except Exception:
            return False
        return any(device.get("max_input_channels", 0) > 0 for device in devices)

    def record_while(self, should_continue) -> "np.ndarray":
        """Registra audio finché should_continue() e' vero. Ritorna un array mono float32."""
        import sounddevice as sd

        frames = []

        def callback(indata, frame_count, time_info, status):
            frames.append(indata.copy())

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                device=self.device,
                callback=callback,
            ):
                while should_continue():
                    sd.sleep(50)
        except Exception as exc:
            raise MicrophoneError(str(exc)) from exc

        if not frames:
            return np.zeros((0,), dtype="float32")
        return np.concatenate(frames, axis=0).reshape(-1)
