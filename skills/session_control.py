"""Comandi che riguardano Jake stesso (v3.0): ripeti, aiuto, zitto, non ascoltare, dettatura.
Registrate in JakeCore perche' parlano con la sessione vocale/HUD tramite SessionHooks."""
from core.skill_result import SkillResult


class RepeatLastSkill:
    metadata = {
        "intent": "REPEAT_LAST",
        "description": "Ripete l'ultima risposta di Jake. Usalo per 'ripeti', 'cosa hai detto', 'non ho capito'.",
        "parameters": {},
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        last = self.core.last_response
        if not last:
            return SkillResult(success=True, data={"text": "Non ho ancora detto nulla."})
        return SkillResult(success=True, data={"text": last})


class HelpSkill:
    metadata = {
        "intent": "HELP",
        "description": "Spiega cosa sa fare Jake. Usalo per 'cosa sai fare', 'aiuto', 'che comandi posso darti'.",
        "parameters": {},
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        count = len(self.core.skill_registry.skills)
        text = (
            "Posso aprire e chiudere programmi e siti, gestire file, cartelle e finestre, cercare su "
            "Google, YouTube o Amazon, mettere musica e video, impostare timer e promemoria, prendere "
            "appunti e cose da fare, leggere e descrivere lo schermo, cliccare dove mi dici, scrivere "
            "sotto dettatura, rispondere a domande, tradurre, calcolare, e regolare volume, luminosità "
            "e alimentazione. Puoi insegnarmi comandi tuoi dicendo 'quando dico X fai Y', e chiedermi di "
            f"imparare capacità nuove con 'impara a fare ...'. In tutto ho {count} capacità."
        )
        return SkillResult(success=True, data={"text": text, "count": count})


class StopTalkingSkill:
    metadata = {
        "intent": "STOP_TALKING",
        "description": "Interrompe subito la voce di Jake. Usalo per 'zitto', 'basta', 'stai zitto', 'smettila di parlare'.",
        "parameters": {},
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        self.core.session_hooks.call("stop_speaking")
        return SkillResult(success=True, data={"silent": True})


class PauseListeningSkill:
    metadata = {
        "intent": "PAUSE_LISTENING",
        "description": "Jake smette di ascoltare per un po' (default 10 minuti): utile durante una chiamata o "
        "un video. Usalo per 'non ascoltare per 10 minuti', 'vai a dormire', 'modalità silenziosa'.",
        "parameters": {
            "minutes": {"type": "integer", "required": False, "description": "Per quanti minuti (default 10)."},
        },
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        minutes = parameters.get("minutes") or 10
        try:
            minutes = max(1, int(minutes))
        except (TypeError, ValueError):
            minutes = 10
        if not self.core.session_hooks.has("pause_listening"):
            return SkillResult(success=False, data={}, error="VOICE_ONLY")
        self.core.session_hooks.call("pause_listening", minutes)
        return SkillResult(success=True, data={"minutes": minutes})


class StartDictationSkill:
    metadata = {
        "intent": "START_DICTATION",
        "description": "Attiva la dettatura: da ora tutto cio' che l'utente dice viene scritto (digitato) nel "
        "campo di testo attivo, finche' non dice 'fine dettatura'. Usalo per 'scrivi sotto dettatura', "
        "'modalità dettatura', 'inizia a dettare'. Diverso da TYPE_TEXT, che scrive una sola frase.",
        "parameters": {},
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        if not self.core.session_hooks.has("start_dictation"):
            return SkillResult(success=False, data={}, error="VOICE_ONLY")
        self.core.session_hooks.call("start_dictation")
        return SkillResult(success=True, data={})


class PrivateModeSkill:
    """v5.6, Privacy Engine: mentre attiva, gli scambi non vengono scritti ne' nella memoria a
    lungo termine ne' nel log operativo (vedi JakeCore.answer). Riparte sempre disattivata a
    ogni avvio di Jake."""

    metadata = {
        "intent": "SET_PRIVATE_MODE",
        "description": "Attiva o disattiva la modalità privata: mentre è attiva, la conversazione non viene "
        "salvata ne' nella memoria a lungo termine ne' nel log. Usalo per 'modalità privata', 'non registrare "
        "questa conversazione', 'niente log per un po'', 'torna a registrare normalmente'.",
        "parameters": {
            "enabled": {"type": "boolean", "required": True, "description": "true per attivarla, false per disattivarla."},
        },
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        enabled = parameters.get("enabled")
        if not isinstance(enabled, bool):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        self.core.private_mode = enabled
        return SkillResult(success=True, data={"enabled": enabled})


class StopDictationSkill:
    metadata = {
        "intent": "STOP_DICTATION",
        "description": "Termina la dettatura in corso. Usalo per 'fine dettatura', 'basta dettare', 'smetti di scrivere'.",
        "parameters": {},
    }

    def __init__(self, core):
        self.core = core

    def execute(self, parameters: dict = None):
        if not self.core.session_hooks.has("stop_dictation"):
            return SkillResult(success=False, data={}, error="VOICE_ONLY")
        self.core.session_hooks.call("stop_dictation")
        return SkillResult(success=True, data={})
