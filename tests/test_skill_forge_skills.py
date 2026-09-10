"""Test unitari per skills/skill_forge_skills.py: nessuna suite esisteva finora per il livello
di wiring tra CREATE_SKILL/LIST_CREATED_SKILLS/DELETE_CREATED_SKILL e core/skill_forge.py (gia'
ampiamente testato per conto suo in tests/test_skill_forge.py - qui si verifica solo la skill,
con un SkillForge finto)."""
import unittest
from unittest import mock

from core.skill_forge import ForgeError
from skills.skill_forge_skills import CreateSkillSkill, DeleteCreatedSkillSkill, ListCreatedSkillsSkill


class CreateSkillProposeTests(unittest.TestCase):
    def test_missing_request_fails(self):
        result = CreateSkillSkill(mock.MagicMock()).execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_a_successful_proposal_asks_for_confirmation_with_the_draft_id(self):
        forge = mock.MagicMock()
        draft = mock.MagicMock(
            intent="TELL_A_JOKE_ABOUT_CATS", description="Racconta barzellette sui gatti",
            examples=["raccontami una barzelletta sui gatti"], draft_id="abc123", code="# codice",
        )
        forge.propose.return_value = draft

        result = CreateSkillSkill(forge).execute({"request": "impara a raccontare barzellette sui gatti"})

        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(result.data["confirm_parameters"], {
            "request": "impara a raccontare barzellette sui gatti", "draft_id": "abc123", "confirmed": True,
        })
        forge.install.assert_not_called()

    def test_a_forge_error_during_proposal_is_reported_not_raised(self):
        forge = mock.MagicMock()
        forge.propose.side_effect = ForgeError("contiene codice pericoloso")
        result = CreateSkillSkill(forge).execute({"request": "cancella tutto il disco"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "FORGE_FAILED")
        self.assertEqual(result.data["message"], "contiene codice pericoloso")


class CreateSkillConfirmTests(unittest.TestCase):
    def test_confirmed_installation_actually_installs_the_draft(self):
        forge = mock.MagicMock()
        draft = mock.MagicMock(intent="TELL_A_JOKE_ABOUT_CATS", description="desc", examples=["es"])
        fake_path = mock.MagicMock()
        fake_path.name = "learned_x.py"
        forge.install.return_value = (draft, fake_path)

        result = CreateSkillSkill(forge).execute({
            "request": "qualsiasi", "draft_id": "abc123", "confirmed": True,
        })

        self.assertTrue(result.success)
        self.assertEqual(result.data["intent"], "TELL_A_JOKE_ABOUT_CATS")
        self.assertEqual(result.data["file"], "learned_x.py")
        forge.install.assert_called_once_with("abc123")
        forge.propose.assert_not_called()

    def test_confirmed_without_a_draft_id_is_treated_as_a_new_proposal(self):
        """confirmed=True da solo non basta: senza draft_id non c'e' nessuna bozza da
        installare, deve ripartire da una nuova proposta invece di sollevare un errore."""
        forge = mock.MagicMock()
        draft = mock.MagicMock(intent="X", description="d", examples=["e"], draft_id="new1")
        forge.propose.return_value = draft

        result = CreateSkillSkill(forge).execute({"request": "qualcosa", "confirmed": True})

        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        forge.install.assert_not_called()
        forge.propose.assert_called_once()

    def test_a_forge_error_during_install_is_reported_not_raised(self):
        forge = mock.MagicMock()
        forge.install.side_effect = ForgeError("la bozza non esiste piu'")
        result = CreateSkillSkill(forge).execute({"request": "x", "draft_id": "scaduto", "confirmed": True})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "FORGE_FAILED")


class ListCreatedSkillsTests(unittest.TestCase):
    def test_no_created_skills_reports_not_found(self):
        forge = mock.MagicMock()
        forge.list_created.return_value = []
        result = ListCreatedSkillsSkill(forge).execute({})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_lists_the_created_skills(self):
        forge = mock.MagicMock()
        forge.list_created.return_value = [{"intent": "X", "description": "d"}]
        result = ListCreatedSkillsSkill(forge).execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.data["skills"], [{"intent": "X", "description": "d"}])


class DeleteCreatedSkillTests(unittest.TestCase):
    def test_missing_name_fails(self):
        result = DeleteCreatedSkillSkill(mock.MagicMock()).execute({})
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_deleting_an_unknown_skill_reports_not_found(self):
        forge = mock.MagicMock()
        forge.delete.return_value = None
        result = DeleteCreatedSkillSkill(forge).execute({"name": "non esiste"})
        self.assertEqual(result.error, "NOT_FOUND")

    def test_deleting_a_known_skill_also_forgets_its_learned_examples(self):
        """Senza questo collegamento, un esempio 'auto' imparato per l'intent della skill
        eliminata (core/learning_manager.py) resterebbe a puntare a un intent che non esiste
        piu' nel registro."""
        forge = mock.MagicMock()
        forge.delete.return_value = {"intent": "TELL_A_JOKE_ABOUT_CATS", "file": "learned_x.py"}
        learning = mock.MagicMock()

        result = DeleteCreatedSkillSkill(forge, learning=learning).execute({"name": "barzellette sui gatti"})

        self.assertTrue(result.success)
        learning.forget_intent.assert_called_once_with("TELL_A_JOKE_ABOUT_CATS")

    def test_deleting_without_a_learning_manager_does_not_crash(self):
        forge = mock.MagicMock()
        forge.delete.return_value = {"intent": "X"}
        result = DeleteCreatedSkillSkill(forge, learning=None).execute({"name": "x"})
        self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()
