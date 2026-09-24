from __future__ import annotations

import threading

from core.risk import RiskLevel
from core.voice.dialogue import (
    CorrectionPlan,
    CorrectionPlanner,
    HeardTurn,
    Learnable,
)


class DialogueRuntime:
    """
    Stato operativo di F2.6.

    NON sostituisce ConversationStateManager.
    NON gestisce policy o autorizzazioni.
    NON esegue azioni.

    Tiene soltanto:
    - correlazione action_id <-> turno sentito;
    - ultimo turno per canale/profilo;
    - CorrectionPlanner;
    - learning differito di una correzione.
    """

    def __init__(self) -> None:
        self.planner = CorrectionPlanner()

        self._lock = threading.RLock()

        self._turns: dict[str, HeardTurn] = {}
        self._last_action_by_scope: dict[str, str] = {}

        # corrected_action_id -> source_action_id
        self._correction_source_by_execution: dict[str, str] = {}

    def record_turn(
        self,
        *,
        scope: str,
        action_id: str,
        heard: str,
        intent: str,
        parameters: dict,
        risk: RiskLevel,
        status: str,
        reversible: bool = False,
    ) -> HeardTurn:
        with self._lock:
            turn = self.planner.record(
                action_id,
                heard,
                intent,
                parameters,
                risk,
                status,
                reversible=reversible,
            )

            self._turns[action_id] = turn
            self._last_action_by_scope[scope] = action_id

            return turn

    def update_turn(
        self,
        action_id: str,
        *,
        status: str | None = None,
        reversible: bool | None = None,
    ) -> HeardTurn | None:
        with self._lock:
            turn = self._turns.get(action_id)

            if turn is None:
                return None

            if status is not None:
                self.planner.update_status(action_id, status)

            if reversible is not None:
                turn.reversible = reversible

            return turn

    def turn(self, action_id: str) -> HeardTurn | None:
        with self._lock:
            return self._turns.get(action_id)

    def last_turn(self, scope: str) -> HeardTurn | None:
        with self._lock:
            action_id = self._last_action_by_scope.get(scope)

            if action_id is None:
                return None

            return self._turns.get(action_id)

    def plan_correction(
        self,
        scope: str,
    ) -> tuple[str | None, CorrectionPlan, HeardTurn | None]:
        with self._lock:
            action_id = self._last_action_by_scope.get(scope)

            if action_id is None:
                return (
                    None,
                    CorrectionPlan(
                        "unknown",
                        "nessun turno precedente disponibile",
                    ),
                    None,
                )

            turn = self._turns.get(action_id)

            if turn is None:
                return (
                    action_id,
                    CorrectionPlan(
                        "unknown",
                        "turno precedente non disponibile",
                    ),
                    None,
                )

            return action_id, self.planner.plan(action_id), turn

    def begin_correction_learning(
        self,
        *,
        source_action_id: str,
        execution_action_id: str,
        corrected_text: str,
        intent: str,
        parameters: dict,
    ) -> bool:
        with self._lock:
            accepted = self.planner.propose_learning(
                source_action_id,
                corrected_text,
                intent,
                parameters,
            )

            if accepted:
                self._correction_source_by_execution[
                    execution_action_id
                ] = source_action_id

            return accepted

    def finish_correction_learning(
        self,
        execution_action_id: str,
        *,
        verified: bool,
    ) -> tuple[HeardTurn | None, Learnable | None]:
        with self._lock:
            source_action_id = self._correction_source_by_execution.pop(
                execution_action_id,
                None,
            )

            if source_action_id is None:
                return None, None

            source_turn = self._turns.get(source_action_id)

            learnable = self.planner.resolve_learning(
                source_action_id,
                verified=verified,
            )

            return source_turn, learnable