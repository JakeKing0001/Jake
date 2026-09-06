"""Punti di aggancio tra il core e la sessione che lo ospita (v3.0).

Alcuni comandi non riguardano il PC ma Jake stesso: "zitto" (interrompi la voce), "non
ascoltare per 10 minuti", "scrivi sotto dettatura". Il core li riconosce, ma chi li puo'
eseguire e' la sessione vocale/HUD. La sessione registra qui i suoi callback; in modalita'
testo restano None e le skill rispondono che serve la voce."""


class SessionHooks:
    NAMES = ("stop_speaking", "pause_listening", "resume_listening", "start_dictation",
             "stop_dictation", "speak", "notify", "set_state")

    def __init__(self):
        for name in self.NAMES:
            setattr(self, name, None)
        self.kind = "text"  # "text" | "voice" | "hud"

    def has(self, name: str) -> bool:
        return callable(getattr(self, name, None))

    def call(self, name: str, *args, **kwargs):
        handler = getattr(self, name, None)
        if callable(handler):
            try:
                return handler(*args, **kwargs)
            except Exception:
                return None
        return None
