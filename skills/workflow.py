from core.skill_result import SkillResult


class SaveWorkflowSkill:
    """Scompone una richiesta in passi (via planner) e li salva con un nome riutilizzabile."""

    metadata = {
        "intent": "SAVE_WORKFLOW",
        "description": "Salva una sequenza di passi con un nome, da poter rieseguire in seguito.",
        "parameters": {
            "name": {
                "type": "string",
                "required": True,
                "description": "Nome con cui richiamare in futuro questa automazione.",
            },
            "request": {
                "type": "string",
                "required": True,
                "description": "Descrizione di cosa deve fare l'automazione, con le stesse parole dell'utente.",
            },
        },
    }

    def __init__(self, planner_provider, workflow_manager):
        self.planner_provider = planner_provider
        self.workflow_manager = workflow_manager

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        name = (parameters.get("name") or "").strip()
        request = (parameters.get("request") or "").strip()
        if not name or not request:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        plan = self.planner_provider.build_plan(request)
        if plan is None or not plan.steps:
            return SkillResult(success=False, data={"name": name}, error="PLAN_FAILED")

        self.workflow_manager.save(name, plan)
        return SkillResult(success=True, data={"name": name, "step_count": len(plan.steps)})


class RunWorkflowSkill:
    """Riesegue un'automazione salvata in precedenza, passo per passo.

    F1: buco reale trovato e corretto - prima non passava MAI blocked_intents/
    always_confirm_intents a plan_executor.execute(), che senza quei argomenti non applica
    nessun controllo (vedi core/policy_engine.py). Un'automazione salvata con un passo
    DESTRUCTIVE/ADMIN non self-confirming (es. FORGET, SET_POWER_PLAN, DELETE_TODO...) eseguiva
    quel passo SENZA alcuna conferma, con un comando diretto dell'utente ("esegui l'automazione
    X") - non serviva nemmeno un prompt costruito ad arte. Riprodotto per davvero prima di
    correggere: un'automazione con un passo FORGET ha cancellato un ricordo vero senza chiedere
    nulla. policy_engine e' iniettato DOPO la costruzione (JakeCore lo popola solo a valle, come
    gia' fa per plan_executor.kill_switch/action_ledger, vedi core/jake_core.py) - resta None
    (nessun controllo) solo per chi costruisce questa skill in isolamento senza mai collegarlo,
    non per omissione silenziosa a runtime. Un riferimento SOLO, non piu' due insiemi separati
    da collegare a mano: quella frammentazione (dimenticarne uno dei due) e' esattamente la
    causa originale di questo bug."""

    metadata = {
        "intent": "RUN_WORKFLOW",
        "description": "Esegue un'automazione precedentemente salvata, dato il suo nome.",
        "parameters": {
            "name": {
                "type": "string",
                "required": True,
                "description": "Nome dell'automazione salvata da eseguire.",
            },
            "dry_run": {
                "type": "boolean",
                "required": False,
                "description": (
                    "Se vero, mostra quali passi verrebbero eseguiti senza eseguirli davvero "
                    "('mostrami prima cosa farebbe'). Usalo se l'utente chiede un'anteprima, "
                    "una prova o di 'vedere cosa farebbe' l'automazione."
                ),
            },
        },
    }

    def __init__(self, workflow_manager, plan_executor, policy_engine=None):
        self.workflow_manager = workflow_manager
        self.plan_executor = plan_executor
        self.policy_engine = policy_engine

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        name = (parameters.get("name") or "").strip()
        if not name:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        plan = self.workflow_manager.load(name)
        if plan is None:
            return SkillResult(success=False, data={"name": name}, error="NOT_FOUND")

        outcome = self.plan_executor.execute(
            plan, policy_engine=self.policy_engine, dry_run=bool(parameters.get("dry_run")),
        )
        return SkillResult(success=True, data={"name": name, "outcome": outcome, "total_steps": len(plan.steps)})
