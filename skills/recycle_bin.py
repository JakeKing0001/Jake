import ctypes

from core.skill_result import SkillResult

SHERB_NOCONFIRMATION = 0x00000001
SHERB_NOPROGRESSUI = 0x00000002
SHERB_NOSOUND = 0x00000004


class EmptyRecycleBinSkill:
    """Svuota il cestino di Windows (SHEmptyRecycleBinW, nessuna dipendenza esterna). Azione
    irreversibile: richiede sempre conferma esplicita, come CLOSE_APP."""

    metadata = {
        "intent": "EMPTY_RECYCLE_BIN",
        "description": "Svuota il cestino di Windows, eliminando definitivamente i file al suo interno.",
        "parameters": {},
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        if not parameters.get("confirmed"):
            return SkillResult(
                success=False,
                data={
                    "message": "Confermi di voler svuotare definitivamente il cestino?",
                    "confirm_parameters": {"confirmed": True},
                },
                error="CONFIRMATION_REQUIRED",
            )

        result = ctypes.windll.shell32.SHEmptyRecycleBinW(
            None, None, SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
        )
        # 0 = successo, -2147418113 (0x8000FFFF) capita anche quando il cestino e' gia' vuoto:
        # in entrambi i casi il cestino risulta comunque vuoto, quindi non e' un errore da riportare.
        if result not in (0, -2147418113):
            return SkillResult(success=False, data={}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={})
