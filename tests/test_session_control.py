"""Test unitari per skills/session_control.py::ResumeInterruptedTaskSkill (F1.8.4, "checkpoint...
da cui riprendere"). Le altre skill del modulo (RepeatLastSkill, PauseListeningSkill, ecc.) non
hanno ancora una suite dedicata - fuori scope per questo incremento."""
import unittest

from skills.session_control import ResumeInterruptedTaskSkill


class FakeCheckpointStore:
    def __init__(self, checkpoint=None):
        self._checkpoint = checkpoint

    def load(self):
        return self._checkpoint


class FakeCore:
    def __init__(self, checkpoint=None, resume_response="Continuo da li'."):
        self.agent_checkpoints = FakeCheckpointStore(checkpoint)
        self.resume_calls = []
        self._resume_response = resume_response

    def _resume_interrupted_task(self, checkpoint):
        self.resume_calls.append(checkpoint)
        return self._resume_response


class ResumeInterruptedTaskSkillTests(unittest.TestCase):
    def test_no_checkpoint_reports_not_found_without_calling_resume(self):
        core = FakeCore(checkpoint=None)

        result = ResumeInterruptedTaskSkill(core).execute({})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")
        self.assertEqual(core.resume_calls, [])

    def test_an_existing_checkpoint_is_passed_to_resume_and_its_response_returned(self):
        checkpoint = object()  # basta l'identita': verifica solo che sia lo STESSO oggetto passato
        core = FakeCore(checkpoint=checkpoint, resume_response="Ho continuato e finito.")

        result = ResumeInterruptedTaskSkill(core).execute({})

        self.assertTrue(result.success)
        self.assertEqual(result.data["response"], "Ho continuato e finito.")
        self.assertEqual(core.resume_calls, [checkpoint])


if __name__ == "__main__":
    unittest.main()
