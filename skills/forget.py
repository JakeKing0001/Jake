from core.skill_result import SkillResult


class ForgetSkill:
    metadata = {
        "intent": "FORGET",
        "description": "Elimina un'informazione precedentemente memorizzata.",
        "parameters": {
            "key": {
                "type": "string",
                "required": True,
                "description": "Nome esatto dell'informazione da eliminare.",
            },
        },
    }

    def __init__(self, memory_manager, dashboard=None):
        self.memory_manager = memory_manager
        # F5.7.6: con la privacy dashboard "dimentica X" cancella DAVVERO - ricordo, relazioni, registro
        # eventi (che conteneva la chiave) e indici derivati - e lascia una ricevuta verificata (solo un
        # hash, mai il ricordo). Prima restava la chiave in memory_audit e nessuna prova.
        self.dashboard = dashboard

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        key = (parameters.get("key") or "").strip()

        if not key:
            return SkillResult(success=False, data={"key": key}, error="MISSING_PARAMETERS")

        if self.dashboard is None:
            deleted = self.memory_manager.forget(key)
            if not deleted:
                return SkillResult(success=False, data={"key": key}, error="NOT_FOUND")
            return SkillResult(success=True, data={"key": key})

        receipts = [receipt for category in self.dashboard.categories_of(key)
                    if (receipt := self.dashboard.delete(key, category)) is not None]
        if not receipts:
            return SkillResult(success=False, data={"key": key}, error="NOT_FOUND")
        residues = {receipt.residue_check for receipt in receipts}
        return SkillResult(success=True, data={
            "key": key,
            "verified": all(receipt.verified for receipt in receipts),
            "residue_check": "found" if "found" in residues else "clean" if residues == {"clean"} else "not_checked",
            "receipts": [receipt.memory_hash for receipt in receipts],
        })
