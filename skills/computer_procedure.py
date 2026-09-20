"""Riesegue una procedura di Computer Use (F3.8, "Learn by demonstration") salvata in precedenza
con un nome (`core/procedure_manager.py`) - l'esatto analogo di `skills/workflow.py::
RunWorkflowSkill` per una sequenza di `RecordedStep` (click/scritture su un `ElementSelector`,
F3.8.1) invece che di `PlanStep` (intent/parametri di skill).

`policy_engine` (F1, stesso principio "iniettato DOPO la costruzione" gia' usato da
`RunWorkflowSkill` - JakeCore lo popola solo a valle, `core/skill_registry.py` costruisce le
skill prima che `PolicyEngine` esista): riassegnato all'oggetto `ComputerAgent` interno a ogni
`execute()`, non solo salvato come attributo inerte - il vero controllo (F3.4.3, "richiedere
policy prima di upload, submit, send, delete e purchase") avviene DENTRO `ComputerAgent.
click_element`/`type_into_element`, chiamato da `replay_step`/`dry_run_step` per ogni singolo
passo che dichiara un `risk_intent` - questa skill non decide nulla da sola, riassegna solo il
motore al componente che gia' sa come usarlo (stesso principio "eredita il rischio dei passi"
gia' scelto per `RUN_WORKFLOW` in `core/risk.py`, qui applicato a passi di computer use)."""
from core.skill_result import SkillResult


class RunComputerProcedureSkill:
    """Analogo di `RunWorkflowSkill` (`skills/workflow.py`) - vedi il docstring del modulo."""

    metadata = {
        "intent": "RUN_COMPUTER_PROCEDURE",
        "description": "Esegue una procedura di azioni sullo schermo (click/scritture su un'app) "
        "precedentemente registrata e salvata con un nome, dato il suo nome.",
        "parameters": {
            "name": {
                "type": "string",
                "required": True,
                "description": "Nome della procedura salvata da eseguire.",
            },
            "parameters": {
                "type": "object",
                "required": False,
                "description": (
                    "Valori per gli eventuali segnaposto ${nome} nei passi di scrittura della "
                    "procedura (F3.8.2) - solo se la procedura ne dichiara."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "required": False,
                "description": (
                    "Se vero, verifica solo che i passi risolverebbero contro lo stato attuale "
                    "dell'app senza eseguire alcuna azione reale ('mostrami prima cosa farebbe'). "
                    "Usalo se l'utente chiede un'anteprima, una prova o di 'vedere cosa farebbe' "
                    "la procedura."
                ),
            },
        },
    }

    def __init__(self, procedure_manager, computer_agent=None, policy_engine=None):
        from core.computer_agent import ComputerAgent

        self.procedure_manager = procedure_manager
        self.computer_agent = computer_agent or ComputerAgent()
        self.policy_engine = policy_engine

    def execute(self, parameters: dict = None):
        from core.computer_use.procedure import dry_run_steps, replay_steps
        from core.computer_use.ui_automation_adapter import UIAutomationAdapter

        parameters = parameters or {}
        name = (parameters.get("name") or "").strip()
        if not name:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        steps = self.procedure_manager.load(name)
        if steps is None:
            return SkillResult(success=False, data={"name": name}, error="NOT_FOUND")
        if not steps:
            return SkillResult(success=False, data={"name": name}, error="EMPTY_PROCEDURE")

        # Vedi il docstring del modulo: riassegnato ADESSO, non solo all'__init__, cosi' un
        # collegamento successivo di JakeCore (`skill.policy_engine = self.policy_engine`, dopo
        # che PolicyEngine esiste) raggiunge davvero il componente che lo usa per decidere.
        self.computer_agent.policy_engine = self.policy_engine
        substitution_parameters = parameters.get("parameters") or {}
        adapter = UIAutomationAdapter()

        if bool(parameters.get("dry_run")):
            results = dry_run_steps(
                adapter, steps, parameters=substitution_parameters, agent=self.computer_agent,
            )
            all_would_succeed = all(result.would_succeed for result in results)
            return SkillResult(success=all_would_succeed, data={
                "name": name, "dry_run": True, "total_steps": len(steps),
                "steps": [{"would_succeed": r.would_succeed, "error": r.error} for r in results],
            })

        results = replay_steps(self.computer_agent, adapter, steps, parameters=substitution_parameters)
        completed_steps = sum(1 for result in results if result.success)
        return SkillResult(success=completed_steps == len(steps), data={
            "name": name, "completed_steps": completed_steps, "total_steps": len(steps),
            "last_error": results[-1].error if results and not results[-1].success else None,
        })
