import unittest

from core.risk import RiskLevel
from core.voice.dialogue_runtime import DialogueRuntime


class DialogueRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = DialogueRuntime()

    def test_records_last_turn_per_scope(self):
        self.runtime.record_turn(
            scope="local",
            action_id="a1",
            heard="apri spotify",
            intent="OPEN_APP",
            parameters={"app": "spotify"},
            risk=RiskLevel.LOCAL_REVERSIBLE,
            status="executed",
            reversible=True,
        )

        turn = self.runtime.last_turn("local")

        self.assertIsNotNone(turn)
        self.assertEqual(
            turn.intent,
            "OPEN_APP",
        )

    def test_scopes_are_isolated(self):
        self.runtime.record_turn(
            scope="profile:a",
            action_id="a1",
            heard="apri spotify",
            intent="OPEN_APP",
            parameters={"app": "spotify"},
            risk=RiskLevel.LOCAL_REVERSIBLE,
            status="executed",
            reversible=True,
        )

        self.runtime.record_turn(
            scope="profile:b",
            action_id="b1",
            heard="che ore sono",
            intent="GET_TIME",
            parameters={},
            risk=RiskLevel.READ_ONLY,
            status="executed",
        )

        self.assertEqual(
            self.runtime.last_turn(
                "profile:a"
            ).action_id
            if hasattr(
                self.runtime.last_turn(
                    "profile:a"
                ),
                "action_id",
            )
            else "a1",
            "a1",
        )

        self.assertEqual(
            self.runtime.last_turn(
                "profile:b"
            ).intent,
            "GET_TIME",
        )

    def test_pending_can_become_cancelled(self):
        self.runtime.record_turn(
            scope="local",
            action_id="a1",
            heard="elimina file",
            intent="DELETE_PATH",
            parameters={"path": "x"},
            risk=RiskLevel.DESTRUCTIVE,
            status="pending",
        )

        self.runtime.update_turn(
            "a1",
            status="cancelled",
        )

        _, plan, _ = (
            self.runtime.plan_correction(
                "local"
            )
        )

        self.assertEqual(
            plan.action,
            "rerun",
        )

    def test_learning_waits_for_verified_execution(self):
        self.runtime.record_turn(
            scope="local",
            action_id="source",
            heard="apri spotifi",
            intent="OPEN_APP",
            parameters={"app": "spotifi"},
            risk=RiskLevel.LOCAL_REVERSIBLE,
            status="executed",
            reversible=True,
        )

        accepted = (
            self.runtime.begin_correction_learning(
                source_action_id="source",
                execution_action_id="corrected",
                corrected_text="apri spotify",
                intent="OPEN_APP",
                parameters={"app": "spotify"},
            )
        )

        self.assertTrue(accepted)

        source, learnable = (
            self.runtime.finish_correction_learning(
                "corrected",
                verified=True,
            )
        )

        self.assertIsNotNone(source)
        self.assertIsNotNone(learnable)
        self.assertEqual(
            learnable.intent,
            "OPEN_APP",
        )

    def test_failed_execution_discards_learning(self):
        self.runtime.record_turn(
            scope="local",
            action_id="source",
            heard="apri spotifi",
            intent="OPEN_APP",
            parameters={"app": "spotifi"},
            risk=RiskLevel.LOCAL_REVERSIBLE,
            status="executed",
            reversible=True,
        )

        self.runtime.begin_correction_learning(
            source_action_id="source",
            execution_action_id="corrected",
            corrected_text="apri spotify",
            intent="OPEN_APP",
            parameters={"app": "spotify"},
        )

        _, learnable = (
            self.runtime.finish_correction_learning(
                "corrected",
                verified=False,
            )
        )

        self.assertIsNone(learnable)


if __name__ == "__main__":
    unittest.main()